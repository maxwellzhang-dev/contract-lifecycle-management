"""Vendor roll-ups, grouped reports and the Excel export."""

import io
import json
from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..access import visibility_sql
from ..auth import current_user
from ..database import connection

router = APIRouter(prefix="/api")

ACTIVE_STATUSES = {"Cleared to Start", "In Progress"}
TERMINAL_STATUSES = {"Completed", "Cancelled"}
GROUPS = ("department", "vendor", "status", "owner", "tag", "start_month", "end_month")
NO_VALUE = "(none)"


def total(contract) -> float:
    return (contract["one_time_amount"] or 0) + (contract["optional_amount"] or 0)


def currency_totals(contracts) -> list[dict]:
    sums: dict[str, float] = defaultdict(float)
    for c in contracts:
        if c["currency"]:
            sums[c["currency"]] += total(c)
    return [{"currency": k, "value": round(v, 2)} for k, v in sorted(sums.items(), key=lambda kv: -kv[1])]


def load_contracts(conn, user: dict):
    vis, params = visibility_sql(user, "contracts")
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM contracts WHERE {vis}", params).fetchall()]
    for row in rows:
        row["tags"] = json.loads(row.get("tags") or "[]")
    return rows


@router.get("/vendors")
def vendors(user: dict = Depends(current_user)):
    today = date.today().isoformat()
    with connection() as conn:
        contracts = load_contracts(conn, user)
        vis, vparams = visibility_sql(user, "c")
        obligations = conn.execute(
            f"""SELECT c.vendor_name, COUNT(*) open_count,
                      SUM(CASE WHEN o.due_date < ? THEN 1 ELSE 0 END) overdue
               FROM obligations o JOIN contracts c ON c.id=o.contract_id
               WHERE o.status='Open' AND c.status NOT IN ('Completed','Cancelled') AND {vis}
               GROUP BY c.vendor_name""",
            (today, *vparams),
        ).fetchall()
    obligation_counts = {row["vendor_name"]: dict(row) for row in obligations}
    grouped: dict[str, list] = defaultdict(list)
    for c in contracts:
        grouped[c["vendor_name"] or NO_VALUE].append(c)
    result = []
    for name, items in grouped.items():
        upcoming = sorted(c["project_end_date"] for c in items
                          if c["project_end_date"] and c["project_end_date"] >= today and c["status"] not in TERMINAL_STATUSES)
        counts = obligation_counts.get(None if name == NO_VALUE else name, {})
        result.append({
            "vendor_name": name,
            "contract_count": len(items),
            "active_count": sum(c["status"] in ACTIVE_STATUSES for c in items),
            "statuses": dict(sorted({s: sum(c["status"] == s for c in items) for s in {c["status"] for c in items}}.items())),
            "value_by_currency": currency_totals(items),
            "departments": sorted({c["department"] for c in items if c["department"]}),
            "next_end_date": upcoming[0] if upcoming else None,
            "open_obligations": counts.get("open_count", 0) or 0,
            "overdue_obligations": counts.get("overdue", 0) or 0,
            "last_updated": max(c["updated_at"] for c in items),
        })
    result.sort(key=lambda v: (v["vendor_name"] == NO_VALUE, -v["contract_count"], v["vendor_name"].lower()))
    return result


def group_keys(contract, group_by: str) -> list[str]:
    if group_by == "tag":
        return contract["tags"] or [NO_VALUE]
    if group_by in {"start_month", "end_month"}:
        value = contract["project_start_date" if group_by == "start_month" else "project_end_date"]
        return [value[:7] if value else NO_VALUE]
    column = {"vendor": "vendor_name"}.get(group_by, group_by)
    return [contract[column] or NO_VALUE]


def build_report(contracts, group_by: str) -> list[dict]:
    groups: dict[str, list] = defaultdict(list)
    for c in contracts:
        for key in group_keys(c, group_by):
            groups[key].append(c)
    rows = [{
        "group": key,
        "count": len(items),
        "active": sum(c["status"] in ACTIVE_STATUSES for c in items),
        "completed": sum(c["status"] == "Completed" for c in items),
        "value_by_currency": currency_totals(items),
    } for key, items in groups.items()]
    if group_by.endswith("_month"):
        rows.sort(key=lambda r: (r["group"] == NO_VALUE, r["group"]))
    else:
        rows.sort(key=lambda r: (r["group"] == NO_VALUE, -r["count"], r["group"].lower()))
    return rows


def filter_period(contracts, start: str, end: str, field: str):
    if not start and not end:
        return contracts
    column = "project_start_date" if field == "start" else "project_end_date"
    return [c for c in contracts if c[column] and (not start or c[column] >= start) and (not end or c[column] <= end)]


def check_params(group_by: str, field: str, start: str, end: str):
    if group_by not in GROUPS:
        raise HTTPException(422, f"group_by must be one of: {', '.join(GROUPS)}")
    if field not in {"start", "end"}:
        raise HTTPException(422, "date_field must be start or end")
    for value in (start, end):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise HTTPException(422, "Dates must be in YYYY-MM-DD format")


@router.get("/reports/summary")
def report(group_by: str = "department", date_field: str = "start", start: str = "", end: str = "",
           user: dict = Depends(current_user)):
    check_params(group_by, date_field, start, end)
    with connection() as conn:
        contracts = filter_period(load_contracts(conn, user), start, end, date_field)
    return {
        "group_by": group_by,
        "contract_count": len(contracts),
        "value_by_currency": currency_totals(contracts),
        "rows": build_report(contracts, group_by),
    }


# --- Excel ---------------------------------------------------------------------------

HEADER_FILL = PatternFill("solid", fgColor="E8E7FB")


def write_sheet(workbook, title: str, headers: list[str], rows: list[list]):
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center")
    for row in rows:
        sheet.append([excel_safe(v) for v in row])
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = sheet.dimensions
    for index, header in enumerate(headers, start=1):
        width = max([len(str(header))] + [len(str(r[index - 1])) for r in rows if r[index - 1] is not None])
        sheet.column_dimensions[get_column_letter(index)].width = min(max(width + 2, 10), 60)
    return sheet


def excel_safe(value):
    # Text that looks like a formula is stored as text, not evaluated.
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def currency_columns(contracts) -> list[str]:
    return sorted({c["currency"] for c in contracts if c["currency"]})


def summary_rows(contracts, group_by: str, currencies: list[str]):
    rows = []
    for r in build_report(contracts, group_by):
        values = {v["currency"]: v["value"] for v in r["value_by_currency"]}
        rows.append([r["group"], r["count"], r["active"], r["completed"], *[values.get(c, 0) for c in currencies]])
    return rows


@router.get("/export/report.xlsx")
def export_workbook(date_field: str = "start", start: str = "", end: str = "", user: dict = Depends(current_user)):
    check_params("department", date_field, start, end)
    with connection() as conn:
        contracts = filter_period(load_contracts(conn, user), start, end, date_field)
        ids = {c["id"] for c in contracts}
        obligations = [dict(r) for r in conn.execute(
            """SELECT o.*, c.title contract_title, c.vendor_name, c.reference_number
               FROM obligations o JOIN contracts c ON c.id=o.contract_id ORDER BY o.due_date""").fetchall()
            if r["contract_id"] in ids]
    workbook = Workbook()
    workbook.remove(workbook.active)
    write_sheet(workbook, "Contracts", [
        "Reference", "Title", "Vendor", "Project", "Status", "Owner", "Department", "Start", "End", "Renewal",
        "Currency", "One-time amount", "Optional amount", "Total", "Validation", "Tags",
    ], [[
        c["reference_number"], c["title"], c["vendor_name"], c["project_name"], c["status"], c["owner"], c["department"],
        c["project_start_date"], c["project_end_date"], c["renewal_date"], c["currency"], c["one_time_amount"],
        c["optional_amount"], total(c), c["validation_status"], ", ".join(c["tags"]),
    ] for c in sorted(contracts, key=lambda c: (c["reference_number"] or "", c["title"]))])
    currencies = currency_columns(contracts)
    value_headers = [f"Total {c}" for c in currencies]
    for title, group in (("By department", "department"), ("By vendor", "vendor"), ("By status", "status"),
                         ("By owner", "owner"), ("By start month", "start_month")):
        write_sheet(workbook, title, [title[3:].capitalize(), "Contracts", "Active", "Completed", *value_headers],
                    summary_rows(contracts, group, currencies))
    write_sheet(workbook, "Obligations", [
        "Contract", "Reference", "Vendor", "Obligation", "Type", "Due", "Status", "Owner", "Currency", "Amount", "Completed by",
    ], [[
        o["contract_title"], o["reference_number"], o["vendor_name"], o["title"], o["kind"], o["due_date"], o["status"],
        o["owner"], o["currency"], o["amount"], o["completed_by"],
    ] for o in obligations])
    buffer = io.BytesIO()
    workbook.save(buffer)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="clause-report-{stamp}.xlsx"'},
    )
