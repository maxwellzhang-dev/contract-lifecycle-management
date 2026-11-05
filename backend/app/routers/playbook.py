"""The company's clause playbook: standard positions the AI review checks every contract against.

Legal writes each rule as a standard position (what we expect), an optional fallback (what we can
accept), and suggested wording. The AI review then reports, per active rule, whether the contract
meets it, deviates from it, or is missing the clause, quoting the text and proposing wording.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user, require
from ..database import add_audit, connection, utc_now

router = APIRouter(prefix="/api")

PLAYBOOK_CATEGORIES = ("Liability", "Indemnity", "Termination", "Renewal", "Payment", "Confidentiality",
                       "Data protection", "Intellectual property", "Service levels", "Governing law", "Other")
SEVERITIES = ("high", "medium", "low")
MAX_RULES = 60  # every active rule goes into the review prompt
LIMITS = {"title": 120, "standard": 1500, "fallback": 1000, "guidance": 1500}

SEED_RULES = (
    ("Liability cap", "Liability", "high",
     "The supplier's total liability is capped at no less than 12 months of fees, and the customer's liability is capped too.",
     "A cap of 6 months of fees is acceptable for low-value contracts (under 50,000).",
     "Each party's total liability under this Agreement shall not exceed the fees paid or payable in the twelve (12) months before the claim."),
    ("Termination for convenience", "Termination", "medium",
     "The customer can terminate for convenience with no more than 90 days' written notice.",
     "Up to 120 days' notice, or termination for convenience after an initial 12-month term.",
     "The Customer may terminate this Agreement for convenience on ninety (90) days' written notice."),
    ("Auto-renewal", "Renewal", "medium",
     "No automatic renewal, or automatic renewal with at least 60 days' notice to opt out and price increases capped at 5%.",
     "", "This Agreement shall not renew automatically. Any renewal requires a written agreement signed by both parties."),
    ("Payment terms", "Payment", "low",
     "Invoices are payable 30 days or more after receipt; no payment in advance for more than one quarter.",
     "Annual payment in advance for subscriptions under 100,000.", "Invoices are payable within thirty (30) days of receipt."),
    ("Data protection", "Data protection", "high",
     "Where the supplier processes personal data, the contract includes data protection obligations (processing on instructions, security, breach notice within 72 hours, deletion at the end).",
     "", "The Supplier shall process Personal Data only on the Customer's documented instructions and notify the Customer of any Personal Data Breach without undue delay and within 72 hours."),
    ("Governing law", "Governing law", "low",
     "Singapore law governs and disputes go to the Singapore courts or SIAC arbitration.",
     "English law with arbitration in Singapore.", "This Agreement is governed by the laws of Singapore."),
)


class RuleIn(BaseModel):
    title: str | None = None
    category: str | None = None
    severity: str | None = None
    standard: str | None = None
    fallback: str | None = None
    guidance: str | None = None
    active: bool | None = None


def rule_out(row) -> dict:
    item = dict(row)
    item["active"] = bool(item["active"])
    return item


def active_rules(conn) -> list[dict]:
    return [rule_out(r) for r in conn.execute(
        "SELECT * FROM playbook_rules WHERE active=1 ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id")]


def playbook_block(rules: list[dict]) -> str:
    lines = ["COMPANY PLAYBOOK (the customer's standard positions; check every rule)"]
    for rule in rules:
        lines.append(f"Rule {rule['id']} [{rule['category']}, {rule['severity']}] {rule['title']}")
        lines.append(f"  Standard: {rule['standard']}")
        if rule["fallback"]:
            lines.append(f"  Acceptable fallback: {rule['fallback']}")
        if rule["guidance"]:
            lines.append(f"  Preferred wording: {rule['guidance']}")
    return "\n".join(lines)


def clean(values: dict, partial: bool) -> dict:
    out = {}
    for key, value in values.items():
        if isinstance(value, str):
            value = value.strip()
            if key in LIMITS and len(value) > LIMITS[key]:
                raise HTTPException(422, f"{key} is limited to {LIMITS[key]} characters.")
            value = value or None
        out[key] = value
    for key in ("title", "standard"):
        if (key in out or not partial) and not out.get(key):
            raise HTTPException(422, f"A rule needs a {'title' if key == 'title' else 'standard position'}.")
    if "category" in out or not partial:
        out["category"] = out.get("category") or "Other"
        if out["category"] not in PLAYBOOK_CATEGORIES:
            raise HTTPException(422, f"category must be one of: {', '.join(PLAYBOOK_CATEGORIES)}")
    if "severity" in out or not partial:
        out["severity"] = out.get("severity") or "medium"
        if out["severity"] not in SEVERITIES:
            raise HTTPException(422, "severity must be high, medium or low")
    if "active" in out:
        out["active"] = 1 if out["active"] else 0
    return out


def seed_playbook(conn):
    if conn.execute("SELECT COUNT(*) FROM playbook_rules").fetchone()[0]:
        return
    now = utc_now()
    for title, category, severity, standard, fallback, guidance in SEED_RULES:
        conn.execute(
            """INSERT INTO playbook_rules (title, category, severity, standard, fallback, guidance, active, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,1,'System',?,?)""",
            (title, category, severity, standard, fallback or None, guidance or None, now, now),
        )


@router.get("/playbook")
def list_rules(_: dict = Depends(current_user)):
    with connection() as conn:
        rows = conn.execute("SELECT * FROM playbook_rules ORDER BY active DESC, category, id").fetchall()
    return {"rules": [rule_out(r) for r in rows], "categories": PLAYBOOK_CATEGORIES, "max_active": MAX_RULES}


def check_limit(conn, extra: int):
    count = conn.execute("SELECT COUNT(*) FROM playbook_rules WHERE active=1").fetchone()[0]
    if count + extra > MAX_RULES:
        raise HTTPException(422, f"At most {MAX_RULES} rules can be active, so every review can check them all.")


@router.post("/playbook", status_code=201)
def create_rule(body: RuleIn, user: dict = Depends(require("playbook.manage"))):
    values = clean(body.model_dump(exclude={"active"}), partial=False)
    now = utc_now()
    with connection() as conn:
        check_limit(conn, 1)
        cursor = conn.execute(
            """INSERT INTO playbook_rules (title, category, severity, standard, fallback, guidance, active, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,1,?,?,?)""",
            (values["title"], values["category"], values["severity"], values["standard"], values.get("fallback"),
             values.get("guidance"), user["name"], now, now),
        )
        add_audit(conn, "Playbook rule added", user["name"], details=values["title"])
        return rule_out(conn.execute("SELECT * FROM playbook_rules WHERE id=?", (cursor.lastrowid,)).fetchone())


@router.patch("/playbook/{rule_id}")
def update_rule(rule_id: int, body: RuleIn, user: dict = Depends(require("playbook.manage"))):
    values = clean(body.model_dump(exclude_unset=True), partial=True)
    with connection() as conn:
        old = conn.execute("SELECT * FROM playbook_rules WHERE id=?", (rule_id,)).fetchone()
        if not old:
            raise HTTPException(404, "Rule not found")
        if values.get("active") and not old["active"]:
            check_limit(conn, 1)
        if values:
            conn.execute(f"UPDATE playbook_rules SET {', '.join(f'{k}=?' for k in values)}, updated_at=? WHERE id=?",
                         (*values.values(), utc_now(), rule_id))
            action = ("Playbook rule turned off" if values.get("active") == 0 else "Playbook rule turned on") \
                if list(values) == ["active"] else "Playbook rule updated"
            add_audit(conn, action, user["name"], details=old["title"])
        return rule_out(conn.execute("SELECT * FROM playbook_rules WHERE id=?", (rule_id,)).fetchone())


@router.delete("/playbook/{rule_id}")
def delete_rule(rule_id: int, user: dict = Depends(require("playbook.manage"))):
    with connection() as conn:
        old = conn.execute("SELECT * FROM playbook_rules WHERE id=?", (rule_id,)).fetchone()
        if not old:
            raise HTTPException(404, "Rule not found")
        conn.execute("DELETE FROM playbook_rules WHERE id=?", (rule_id,))
        add_audit(conn, "Playbook rule deleted", user["name"], details=old["title"])
    return {"deleted": True}
