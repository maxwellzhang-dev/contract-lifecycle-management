"""Which contracts a user may see.

Every user has an access scope:

- `all`: every contract (the default, and always the case for admins);
- `department`: contracts of their own department, plus the ones they are involved in;
- `assigned`: only contracts they are involved in.

"Involved" means named as owner or renewal owner, the uploader, an explicit member, the
submitter or a decider of an approval, an approver whose role has a step waiting, or a signer.
Roles still decide what a user can *do*; scope only decides what they can *see*. Contracts a
user cannot see behave as if they do not exist (404), so their existence is not revealed.
"""

from fastapi import Depends, HTTPException, Request

from .auth import current_user
from .database import connection

ACCESS_SCOPES = ("all", "department", "assigned")


def sees_everything(user: dict) -> bool:
    return user["role"] == "admin" or user.get("access_scope", "all") == "all"


def visibility_sql(user: dict, alias: str = "contracts") -> tuple[str, tuple]:
    """A SQL condition on `alias` (the contracts table) and its parameters."""
    if sees_everything(user):
        return "1=1", ()
    c = alias
    name = user["name"].strip().lower()
    clauses = [
        f"lower(COALESCE({c}.owner,''))=?", f"lower(COALESCE({c}.renewal_owner,''))=?",
        f"lower(COALESCE({c}.uploaded_by,''))=?",
        f"EXISTS (SELECT 1 FROM contract_members m WHERE m.contract_id={c}.id AND m.user_id=?)",
        f"""EXISTS (SELECT 1 FROM approval_requests r JOIN approval_steps s ON s.request_id=r.id
                    WHERE r.contract_id={c}.id AND (r.submitted_by=? OR s.decided_by=?
                          OR (r.status='Pending' AND s.status='Pending' AND s.role=?)))""",
        f"""EXISTS (SELECT 1 FROM signature_requests q JOIN signature_signers g ON g.request_id=q.id
                    WHERE q.contract_id={c}.id AND g.user_id=?)""",
    ]
    params = [name, name, name, user["id"], user["id"], user["id"], user["role"], user["id"]]
    if user.get("access_scope") == "department" and user.get("department"):
        clauses.insert(0, f"lower(COALESCE({c}.department,''))=?")
        params.insert(0, user["department"].strip().lower())
    return "(" + " OR ".join(clauses) + ")", tuple(params)


def can_view(conn, user: dict, contract_id: int) -> bool:
    if sees_everything(user):
        return True
    condition, params = visibility_sql(user, "c")
    return conn.execute(f"SELECT 1 FROM contracts c WHERE c.id=? AND {condition}", (contract_id, *params)).fetchone() is not None


def visible_ids(conn, user: dict) -> set[int] | None:
    """Ids of visible contracts, or None when the user sees everything."""
    if sees_everything(user):
        return None
    condition, params = visibility_sql(user, "c")
    return {r[0] for r in conn.execute(f"SELECT c.id FROM contracts c WHERE {condition}", params)}


# Path parameters that identify something belonging to a contract, and how to find it.
_OWNERS = {
    "obligation_id": "SELECT contract_id FROM obligations WHERE id=?",
    "comment_id": "SELECT contract_id FROM contract_comments WHERE id=?",
}


def contract_of(conn, request: Request) -> int | None:
    params = request.path_params
    if "contract_id" in params:
        return int(params["contract_id"])
    if "request_id" in params:
        table = "signature_requests" if "/signatures/" in request.url.path else "approval_requests"
        row = conn.execute(f"SELECT contract_id FROM {table} WHERE id=?", (int(params["request_id"]),)).fetchone()
        return row[0] if row else None
    for key, sql in _OWNERS.items():
        if key in params:
            row = conn.execute(sql, (int(params[key]),)).fetchone()
            return row[0] if row else None
    return None


def contract_guard(request: Request):
    """Router dependency: refuse any request about a contract the user cannot see."""
    params = request.path_params
    if not any(key in params for key in ("contract_id", "request_id", *_OWNERS)):
        return
    try:
        user = current_user(request)
    except HTTPException:
        return  # the endpoint's own authentication answers
    with connection() as conn:
        contract_id = contract_of(conn, request)
        if contract_id is not None and not can_view(conn, user, contract_id):
            raise HTTPException(404, "Contract not found")


Guard = Depends(contract_guard)
