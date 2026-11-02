"""LLM features: contract review, Q&A, version comparison and natural-language search."""

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user, require
from ..database import add_audit, connection, utc_now
from ..services import llm
from ..services.extraction import extract_text_and_images
from ..storage import stored_file
from ..workflow import CONTRACT_STATUSES, OBLIGATION_KINDS
from .contracts import load_contract

router = APIRouter(prefix="/api")

RISK_CATEGORIES = ["Liability", "Indemnity", "Termination", "Renewal", "Payment", "Confidentiality",
                   "Data protection", "Intellectual property", "Service levels", "Governing law", "Other"]
METADATA_FIELDS = ("title", "reference_number", "vendor_name", "project_name", "status", "project_start_date",
                   "project_end_date", "renewal_date", "renewal_notice_deadline", "currency", "one_time_amount",
                   "optional_amount", "payment_terms", "deliverables", "department", "owner")
MAX_DOC_CHARS = 120_000
MAX_QUESTION = 1000

REVIEW_SCHEMA = llm.obj(
    summary=llm.s(),
    key_terms=llm.arr(llm.obj(label=llm.s(), value=llm.s(), quote=llm.s(nullable=True))),
    risks=llm.arr(llm.obj(
        category=llm.s(enum=RISK_CATEGORIES), severity=llm.s(enum=["high", "medium", "low"]),
        finding=llm.s(), quote=llm.s(nullable=True), recommendation=llm.s())),
    obligations=llm.arr(llm.obj(
        title=llm.s(), kind=llm.s(enum=list(OBLIGATION_KINDS)), due_date=llm.s(nullable=True),
        amount=llm.n(), currency=llm.s(nullable=True), quote=llm.s(nullable=True))),
    missing_clauses=llm.arr(llm.s()),
    field_mismatches=llm.arr(llm.obj(
        field=llm.s(enum=list(METADATA_FIELDS)), record_value=llm.s(nullable=True),
        document_value=llm.s(), quote=llm.s(nullable=True))),
)
REVIEW_PROMPT = """You review a contract for a procurement and legal team.
Return:
- summary: 3-5 plain sentences on what is bought, from whom, for how long and for how much.
- key_terms: the most important commercial terms (term, fees, payment, renewal, termination notice, liability cap, SLAs).
- risks: clauses that are unusual, one-sided against the customer, or ambiguous. Use severity high only for
  issues that could cause material financial or legal exposure. Quote the clause text exactly when possible.
- obligations: dated or recurring things either party must do (payments, deliverables, notices, renewals).
  Use ISO 8601 dates only when the date is stated or can be computed exactly from stated dates; otherwise null.
- missing_clauses: standard protections that are absent (for example limitation of liability, termination for
  convenience, data protection, confidentiality), only when the document text is available.
- field_mismatches: where the RECORD METADATA disagrees with the document text. Ignore fields the document does
  not mention. Dates as YYYY-MM-DD, amounts as plain numbers.
If there is no document text, review the metadata only, leave quotes null and say so in the summary."""

QUESTION_SCHEMA = llm.obj(
    answer=llm.s(), found={"type": "boolean"},
    citations=llm.arr(llm.obj(quote=llm.s(), note=llm.s(nullable=True))),
)
QUESTION_PROMPT = """Answer the user's question about this contract using only the record metadata and document
text provided. Set found=false and explain what is missing when the answer is not in the material. Keep the
answer short and specific, and quote the supporting text exactly in citations."""

COMPARE_SCHEMA = llm.obj(
    summary=llm.s(),
    changes=llm.arr(llm.obj(
        topic=llm.s(), before=llm.s(nullable=True), after=llm.s(nullable=True),
        impact=llm.s(enum=["favourable", "unfavourable", "neutral", "unclear"]), note=llm.s())),
)
COMPARE_PROMPT = """Compare two versions of the same contract. List each material change (commercial terms,
dates, obligations, liability, termination, renewal, scope). Ignore formatting-only changes. impact is from the
customer's point of view. Quote short before/after snippets; null when a clause was added or removed."""

SEARCH_SCHEMA = llm.obj(
    search=llm.s(nullable=True), status=llm.s(nullable=True, enum=list(CONTRACT_STATUSES)),
    vendor=llm.s(nullable=True), department=llm.s(nullable=True), owner=llm.s(nullable=True),
    tag=llm.s(nullable=True), currency=llm.s(nullable=True), min_value=llm.n(), max_value=llm.n(),
    end_from=llm.s(nullable=True), end_to=llm.s(nullable=True), explanation=llm.s(),
)
SEARCH_PROMPT = """Turn a request for contracts into filters. Use only vendor, department, owner, tag and currency
values from the KNOWN VALUES lists, matching spelling exactly; leave a filter null when nothing fits. min_value and
max_value apply to the total contract value. end_from/end_to filter the contract end date (YYYY-MM-DD), computed
from TODAY. Put remaining free-text words in search only when no structured filter covers them. explanation is one
short sentence describing the filters you chose."""


class QuestionIn(BaseModel):
    question: str


class CompareIn(BaseModel):
    from_version: int
    to_version: int


class SearchIn(BaseModel):
    query: str


def call(*args, **kwargs) -> dict:
    try:
        return llm.structured(*args, **kwargs)
    except llm.LLMUnavailable as exc:
        raise HTTPException(503, str(exc))


def metadata_block(contract) -> str:
    lines = [f"{key}: {contract[key]}" for key in METADATA_FIELDS if contract[key] not in (None, "")]
    return "RECORD METADATA\n" + "\n".join(lines)


def document_text(path_value: str | None, limit: int = MAX_DOC_CHARS):
    if not path_value:
        return "", []
    path = stored_file(path_value)
    try:
        text, images = extract_text_and_images(path)
    except Exception:
        return "", []
    return text[:limit], images[:5]


def contract_material(contract) -> tuple[str, list]:
    text, images = document_text(contract["file_path"])
    body = metadata_block(contract) + "\n\nDOCUMENT TEXT\n" + (text.strip() or "(no document text available)")
    return body, images


def review_out(row) -> dict | None:
    if not row:
        return None
    return {**{k: row[k] for k in ("id", "contract_id", "document_version", "model", "created_by", "created_at")},
            "result": json.loads(row["result"])}


@router.get("/ai/status")
def ai_status(_: dict = Depends(current_user)):
    return {"configured": llm.configured(), "model": llm.model_name()}


@router.get("/contracts/{contract_id}/ai/review")
def latest_review(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        contract = load_contract(conn, contract_id)
        row = conn.execute("SELECT * FROM ai_reviews WHERE contract_id=? ORDER BY id DESC LIMIT 1", (contract_id,)).fetchone()
    review = review_out(row)
    if review:
        review["stale"] = review["document_version"] != contract["document_version"]
    return review


def clean_review(result: dict) -> dict:
    for item in result.get("obligations", []):
        try:
            item["due_date"] = date.fromisoformat(item["due_date"]).isoformat() if item.get("due_date") else None
        except ValueError:
            item["due_date"] = None
        if item.get("currency"):
            item["currency"] = item["currency"].upper()[:3]
    order = {"high": 0, "medium": 1, "low": 2}
    result["risks"] = sorted(result.get("risks", []), key=lambda r: order.get(r["severity"], 3))
    return result


@router.post("/contracts/{contract_id}/ai/review", status_code=201)
def run_review(contract_id: int, user: dict = Depends(require("ai.use"))):
    with connection() as conn:
        contract = load_contract(conn, contract_id)
    body, images = contract_material(contract)
    result = clean_review(call(REVIEW_PROMPT, body, "contract_review", REVIEW_SCHEMA, images=images, effort="medium"))
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO ai_reviews (contract_id, document_version, result, model, created_by, created_at) VALUES (?,?,?,?,?,?)",
            (contract_id, contract["document_version"], json.dumps(result), llm.model_name(), user["name"], utc_now()),
        )
        high = sum(r["severity"] == "high" for r in result["risks"])
        add_audit(conn, "AI review generated", user["name"], contract_id,
                  f"{len(result['risks'])} risk(s), {high} high; {len(result['obligations'])} obligation(s) suggested")
        review = review_out(conn.execute("SELECT * FROM ai_reviews WHERE id=?", (cursor.lastrowid,)).fetchone())
    review["stale"] = False
    return review


@router.get("/contracts/{contract_id}/ai/questions")
def question_history(contract_id: int, _: dict = Depends(current_user)):
    with connection() as conn:
        rows = conn.execute(
            """SELECT q.*, u.name asked_by FROM ai_questions q JOIN users u ON u.id=q.user_id
               WHERE q.contract_id=? ORDER BY q.id DESC LIMIT 30""",
            (contract_id,),
        ).fetchall()
    return [{**dict(r), "answer": json.loads(r["answer"])} for r in rows]


@router.post("/contracts/{contract_id}/ai/questions", status_code=201)
def ask(contract_id: int, body: QuestionIn, user: dict = Depends(require("ai.use"))):
    question = body.question.strip()
    if not question:
        raise HTTPException(422, "Ask a question.")
    if len(question) > MAX_QUESTION:
        raise HTTPException(422, f"Questions are limited to {MAX_QUESTION} characters.")
    with connection() as conn:
        contract = load_contract(conn, contract_id)
    material, images = contract_material(contract)
    answer = call(QUESTION_PROMPT, f"{material}\n\nQUESTION\n{question}", "contract_answer", QUESTION_SCHEMA, images=images)
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO ai_questions (contract_id, user_id, question, answer, document_version, created_at) VALUES (?,?,?,?,?,?)",
            (contract_id, user["id"], question, json.dumps(answer), contract["document_version"], utc_now()),
        )
        row = conn.execute(
            "SELECT q.*, u.name asked_by FROM ai_questions q JOIN users u ON u.id=q.user_id WHERE q.id=?",
            (cursor.lastrowid,),
        ).fetchone()
    return {**dict(row), "answer": answer}


@router.post("/contracts/{contract_id}/ai/compare")
def compare(contract_id: int, body: CompareIn, user: dict = Depends(require("ai.use"))):
    if body.from_version == body.to_version:
        raise HTTPException(422, "Choose two different versions.")
    with connection() as conn:
        load_contract(conn, contract_id)
        rows = {r["version"]: r for r in conn.execute(
            "SELECT * FROM contract_versions WHERE contract_id=? AND version IN (?,?)",
            (contract_id, body.from_version, body.to_version))}
    if len(rows) != 2:
        raise HTTPException(404, "Version not found")
    texts = {}
    for version, row in rows.items():
        text, _ = document_text(row["file_path"], MAX_DOC_CHARS // 2)
        if not text.strip():
            raise HTTPException(422, f"Version {version} has no readable text (scanned images cannot be compared yet).")
        texts[version] = text
    material = (f"VERSION {body.from_version} (older)\n{texts[body.from_version]}\n\n"
                f"VERSION {body.to_version} (newer)\n{texts[body.to_version]}")
    result = call(COMPARE_PROMPT, material, "version_comparison", COMPARE_SCHEMA, effort="medium")
    with connection() as conn:
        add_audit(conn, "AI version comparison", user["name"], contract_id,
                  f"v{body.from_version} → v{body.to_version}: {len(result['changes'])} change(s)")
    return {"from_version": body.from_version, "to_version": body.to_version, **result}


def known_values(conn) -> dict[str, list[str]]:
    def distinct(column):
        return [r[0] for r in conn.execute(f"SELECT DISTINCT {column} FROM contracts WHERE {column} IS NOT NULL ORDER BY 1")]
    tags = sorted({t for (raw,) in conn.execute("SELECT tags FROM contracts") for t in json.loads(raw or "[]")})
    return {"vendor": distinct("vendor_name"), "department": distinct("department"), "owner": distinct("owner"),
            "currency": distinct("currency"), "tag": tags}


@router.post("/ai/search")
def natural_search(body: SearchIn, user: dict = Depends(require("ai.use"))):
    query = body.query.strip()
    if not query or len(query) > 500:
        raise HTTPException(422, "Describe the contracts you are looking for (up to 500 characters).")
    with connection() as conn:
        known = known_values(conn)
    prompt = (f"TODAY: {date.today().isoformat()}\nKNOWN VALUES\n"
              + "\n".join(f"{k}: {json.dumps(v)}" for k, v in known.items()) + f"\n\nREQUEST\n{query}")
    result = call(SEARCH_PROMPT, prompt, "contract_filters", SEARCH_SCHEMA)
    # Keep only values that exist, with their exact spelling, so the filters always match something real.
    for key, values in known.items():
        if result.get(key):
            match = {v.lower(): v for v in values}.get(str(result[key]).lower())
            result[key] = match
    for key in ("end_from", "end_to"):
        try:
            result[key] = date.fromisoformat(result[key]).isoformat() if result.get(key) else None
        except ValueError:
            result[key] = None
    return result
