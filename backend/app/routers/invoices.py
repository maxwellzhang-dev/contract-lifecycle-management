"""Invoices and payments against a contract, and how much of its value is used.

The contract value (one-time plus optional amount) is the budget. Every invoice that is not
disputed counts as spent; paid invoices also count as paid. Invoices must be in the contract's
currency, so amounts are never converted. When spending passes the warning threshold
(`SPEND_WARN_PERCENT`, 80% by default) or the budget, the owner and finance are notified once.
"""

import os
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..access import visibility_sql
from ..auth import current_user, require
from ..database import add_audit, connection, utc_now
from ..workflow import contract_recipients, notify, users_with_roles
from .contracts import load_contract

router = APIRouter(prefix="/api")

INVOICE_STATUSES = ("Received", "Approved", "Paid", "Disputed")


class InvoiceIn(BaseModel):
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    amount: float | None = None
    currency: str | None = None
    status: str | None = None
    obligation_id: int | None = None
    notes: str | None = None


def warn_percent() -> float:
    try:
        return float(os.getenv("SPEND_WARN_PERCENT", "80"))
    except ValueError:
        return 80.0


def budget_of(contract) -> float | None:
    if contract["one_time_amount"] is None and contract["optional_amount"] is None:
        return None
    return round((contract["one_time_amount"] or 0) + (contract["optional_amount"] or 0), 2)


def spend_level(budget: float | None, invoiced: float) -> str:
    if not budget:
        return "over" if invoiced > 0 and budget == 0 else "unknown" if budget is None else "ok"
    percent = invoiced / budget * 100
    return "over" if percent > 100 else "warning" if percent >= warn_percent() else "ok"


def spend_summary(conn, contract) -> dict:
    row = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN status!='Disputed' THEN amount ELSE 0 END),0) invoiced,
                  COALESCE(SUM(CASE WHEN status='Paid' THEN amount ELSE 0 END),0) paid,
                  COALESCE(SUM(CASE WHEN status='Disputed' THEN amount ELSE 0 END),0) disputed,
                  COALESCE(SUM(CASE WHEN status IN ('Received','Approved') AND due_date < ? THEN amount ELSE 0 END),0) overdue,
                  COUNT(*) n
           FROM contract_invoices WHERE contract_id=?""",
        (date.today().isoformat(), contract["id"]),
    ).fetchone()
    budget = budget_of(contract)
    invoiced = round(row["invoiced"], 2)
    return {
        "currency": contract["currency"], "budget": budget, "invoiced": invoiced, "paid": round(row["paid"], 2),
        "unpaid": round(invoiced - row["paid"], 2), "disputed": round(row["disputed"], 2), "overdue": round(row["overdue"], 2),
        "remaining": round(budget - invoiced, 2) if budget is not None else None,
        "percent": round(invoiced / budget * 100, 1) if budget else None,
        "level": spend_level(budget, invoiced), "warn_percent": warn_percent(), "invoice_count": row["n"],
    }


def invoice_out(row) -> dict:
    item = dict(row)
    item["overdue"] = bool(row["status"] in ("Received", "Approved") and row["due_date"] and row["due_date"] < date.today().isoformat())
    return item


def load_invoice(conn, invoice_id: int):
    row = conn.execute("SELECT * FROM contract_invoices WHERE id=?", (invoice_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Invoice not found")
    return row


def check_values(conn, contract, values: dict, invoice_id: int | None = None):
    for key in ("invoice_date", "due_date"):
        if values.get(key):
            try:
                date.fromisoformat(values[key])
            except ValueError:
                raise HTTPException(422, f"{key} must be a date in YYYY-MM-DD format")
    if "amount" in values and (values["amount"] is None or values["amount"] <= 0):
        raise HTTPException(422, "Enter an amount above zero.")
    if "status" in values and values["status"] not in INVOICE_STATUSES:
        raise HTTPException(422, f"status must be one of: {', '.join(INVOICE_STATUSES)}")
    if "currency" in values:
        if not contract["currency"]:
            raise HTTPException(422, "Set the contract currency before recording invoices.")
        if (values["currency"] or contract["currency"]).upper() != contract["currency"].upper():
            raise HTTPException(422, f"Invoices must be in the contract currency ({contract['currency']}); amounts are never converted.")
        values["currency"] = contract["currency"]
    if values.get("obligation_id") is not None:
        ok = conn.execute("SELECT 1 FROM obligations WHERE id=? AND contract_id=?", (values["obligation_id"], contract["id"])).fetchone()
        if not ok:
            raise HTTPException(422, "That obligation belongs to another contract.")
    if values.get("invoice_number"):
        duplicate = conn.execute(
            "SELECT 1 FROM contract_invoices WHERE contract_id=? AND lower(invoice_number)=lower(?) AND id!=?",
            (contract["id"], values["invoice_number"], invoice_id or 0)).fetchone()
        if duplicate:
            raise HTTPException(409, f"Invoice {values['invoice_number']} is already recorded for this contract.")


def after_change(conn, contract, before: dict, user: dict):
    """Notify once when spending crosses the warning threshold or the budget."""
    after = spend_summary(conn, contract)
    rank = {"unknown": 0, "ok": 0, "warning": 1, "over": 2}
    if rank[after["level"]] > rank[before["level"]]:
        recipients = [uid for uid in contract_recipients(conn, contract["owner"]) + users_with_roles(conn, ("finance",))
                      if uid != user["id"]]
        if after["level"] == "over":
            title = f"{contract['title']} is over budget"
            body = (f"Invoiced {contract['currency']} {after['invoiced']:,.2f} of {after['budget']:,.2f} "
                    f"({after['percent']}%). Recorded by {user['name']}." if after["budget"] else f"Invoices recorded against a contract with no value. Recorded by {user['name']}.")
        else:
            title = f"{contract['title']} has used {after['percent']:.0f}% of its value"
            body = f"Invoiced {contract['currency']} {after['invoiced']:,.2f} of {after['budget']:,.2f}. {after['remaining']:,.2f} remaining."
        notify(conn, recipients, "spend", title, body, contract["id"], f"spend:{contract['id']}:{after['level']}:{after['budget']}")
        add_audit(conn, "Spend alert", "System", contract["id"], title)
    return after


@router.get("/contracts/{contract_id}/invoices")
def list_invoices(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        contract = load_contract(conn, contract_id)
        rows = conn.execute(
            """SELECT i.*, o.title obligation_title FROM contract_invoices i LEFT JOIN obligations o ON o.id=i.obligation_id
               WHERE i.contract_id=? ORDER BY COALESCE(i.invoice_date, i.created_at) DESC, i.id DESC""",
            (contract_id,),
        ).fetchall()
        return {"spend": spend_summary(conn, contract), "items": [invoice_out(r) for r in rows], "statuses": INVOICE_STATUSES}


def clean(body: InvoiceIn, unset: bool) -> dict:
    values = body.model_dump(exclude_unset=unset)
    return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in values.items()}


@router.post("/contracts/{contract_id}/invoices", status_code=201)
def create_invoice(contract_id: int, body: InvoiceIn, user: dict = Depends(require("invoice.manage"))):
    values = clean(body, unset=False)
    values["status"] = values["status"] or "Received"
    with connection() as conn:
        contract = load_contract(conn, contract_id)
        check_values(conn, contract, values)
        before = spend_summary(conn, contract)
        now = utc_now()
        cursor = conn.execute(
            """INSERT INTO contract_invoices (contract_id, invoice_number, invoice_date, due_date, amount, currency, status,
                   paid_at, obligation_id, notes, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (contract_id, values["invoice_number"], values["invoice_date"], values["due_date"], values["amount"],
             values["currency"], values["status"], now if values["status"] == "Paid" else None, values["obligation_id"],
             values["notes"], user["name"], now, now),
        )
        invoice = load_invoice(conn, cursor.lastrowid)
        if invoice["status"] == "Paid":
            complete_obligation(conn, invoice, user)
        label = values["invoice_number"] or f"#{cursor.lastrowid}"
        add_audit(conn, "Invoice recorded", user["name"], contract_id,
                  f"Invoice {label}: {values['currency']} {values['amount']:,.2f} ({values['status']})")
        after_change(conn, contract, before, user)
        return invoice_out(invoice)


def complete_obligation(conn, invoice, user: dict):
    """Paying an invoice that settles a payment obligation completes the obligation."""
    if not invoice["obligation_id"]:
        return
    done = conn.execute(
        "UPDATE obligations SET status='Done', completed_at=?, completed_by=?, updated_at=? WHERE id=? AND status='Open'",
        (utc_now(), user["name"], utc_now(), invoice["obligation_id"]),
    ).rowcount
    if done:
        title = conn.execute("SELECT title FROM obligations WHERE id=?", (invoice["obligation_id"],)).fetchone()["title"]
        add_audit(conn, "Obligation completed", user["name"], invoice["contract_id"], f"{title} (invoice paid)")


@router.patch("/invoices/{invoice_id}")
def update_invoice(invoice_id: int, body: InvoiceIn, user: dict = Depends(require("invoice.manage"))):
    values = clean(body, unset=True)
    with connection() as conn:
        old = load_invoice(conn, invoice_id)
        contract = load_contract(conn, old["contract_id"])
        values = {k: v for k, v in values.items() if old[k] != v}
        if not values:
            return invoice_out(old)
        check_values(conn, contract, values, invoice_id)
        before = spend_summary(conn, contract)
        if "status" in values:
            values["paid_at"] = utc_now() if values["status"] == "Paid" else None
        conn.execute(f"UPDATE contract_invoices SET {', '.join(f'{k}=?' for k in values)}, updated_at=? WHERE id=?",
                     (*values.values(), utc_now(), invoice_id))
        invoice = load_invoice(conn, invoice_id)
        if values.get("status") == "Paid":
            complete_obligation(conn, invoice, user)
        label = invoice["invoice_number"] or f"#{invoice_id}"
        details = "; ".join(f"{k}: {old[k]!r} → {v!r}" for k, v in values.items() if k != "paid_at")
        add_audit(conn, "Invoice paid" if values.get("status") == "Paid" else "Invoice updated", user["name"],
                  contract["id"], f"Invoice {label}: {details}")
        after_change(conn, contract, before, user)
        return invoice_out(invoice)


@router.delete("/invoices/{invoice_id}")
def delete_invoice(invoice_id: int, user: dict = Depends(require("invoice.manage"))):
    with connection() as conn:
        old = load_invoice(conn, invoice_id)
        conn.execute("DELETE FROM contract_invoices WHERE id=?", (invoice_id,))
        add_audit(conn, "Invoice deleted", user["name"], old["contract_id"],
                  f"Invoice {old['invoice_number'] or f'#{invoice_id}'}: {old['currency']} {old['amount']:,.2f}")
    return {"deleted": True}


@router.get("/spend")
def portfolio_spend(level: str = Query(""), user: dict = Depends(current_user)):
    """Budget use for every visible contract that has invoices or a value, most used first."""
    vis, vparams = visibility_sql(user, "c")
    with connection() as conn:
        rows = conn.execute(
            f"""SELECT c.*, COALESCE(SUM(CASE WHEN i.status!='Disputed' THEN i.amount ELSE 0 END),0) invoiced,
                       COALESCE(SUM(CASE WHEN i.status='Paid' THEN i.amount ELSE 0 END),0) paid, COUNT(i.id) invoice_count
                FROM contracts c LEFT JOIN contract_invoices i ON i.contract_id=c.id
                WHERE {vis} GROUP BY c.id""",
            vparams,
        ).fetchall()
    items = []
    for row in rows:
        if not row["invoice_count"]:
            continue
        budget = budget_of(row)
        item = {"contract_id": row["id"], "title": row["title"], "vendor_name": row["vendor_name"], "status": row["status"],
                "currency": row["currency"], "budget": budget, "invoiced": round(row["invoiced"], 2), "paid": round(row["paid"], 2),
                "percent": round(row["invoiced"] / budget * 100, 1) if budget else None,
                "remaining": round(budget - row["invoiced"], 2) if budget is not None else None,
                "level": spend_level(budget, row["invoiced"]), "invoice_count": row["invoice_count"]}
        if not level or item["level"] == level:
            items.append(item)
    items.sort(key=lambda i: (-(i["percent"] if i["percent"] is not None else 1e9), i["title"]))
    totals: dict[str, dict] = {}
    for item in items:
        if item["currency"]:
            t = totals.setdefault(item["currency"], {"currency": item["currency"], "budget": 0.0, "invoiced": 0.0, "paid": 0.0})
            t["budget"] = round(t["budget"] + (item["budget"] or 0), 2)
            t["invoiced"] = round(t["invoiced"] + item["invoiced"], 2)
            t["paid"] = round(t["paid"] + item["paid"], 2)
    return {"items": items, "totals": sorted(totals.values(), key=lambda t: -t["invoiced"]), "warn_percent": warn_percent(),
            "counts": {lvl: sum(i["level"] == lvl for i in items) for lvl in ("ok", "warning", "over", "unknown")}}
