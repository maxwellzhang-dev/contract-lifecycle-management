"""Administration of background jobs."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import jobs
from ..auth import require
from ..database import add_audit, after_commit, connection, utc_now

router = APIRouter(prefix="/api")

LABELS = {"extract": "AI extraction", "email": "Email", "ai_review": "AI review for approvers", "ocr": "OCR"}


def job_out(row) -> dict:
    payload = json.loads(row["payload"])
    item = {k: row[k] for k in ("id", "kind", "status", "attempts", "max_attempts", "run_after", "last_error",
                                "created_at", "updated_at")}
    item["label"] = LABELS.get(row["kind"], row["kind"])
    item["contract_id"] = payload.get("contract_id")
    item["target"] = payload.get("to")  # email recipient; bodies are not shown
    return item


@router.get("/jobs")
def list_jobs(status: str = "", limit: int = Query(50, ge=1, le=200), _: dict = Depends(require("user.manage"))):
    with connection() as conn:
        where, params = ("WHERE status=?", (status,)) if status else ("", ())
        rows = conn.execute(f"SELECT * FROM jobs {where} ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        return {"counts": jobs.summary(conn), "items": [job_out(r) for r in rows],
                "workers": len(jobs.worker_pool.threads), "mode": jobs.mode()}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, user: dict = Depends(require("user.manage"))):
    with connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Job not found")
        if row["status"] != "failed":
            raise HTTPException(409, f"Only failed jobs can be retried (this one is {row['status']}).")
        now = utc_now()
        conn.execute("UPDATE jobs SET status='queued', attempts=0, run_after=?, updated_at=? WHERE id=?", (now, now, job_id))
        add_audit(conn, "Job retried", user["name"], json.loads(row["payload"]).get("contract_id"),
                  f"{LABELS.get(row['kind'], row['kind'])} job #{job_id}")
        after_commit(conn, (lambda: jobs.run_job(job_id)) if jobs.mode() == "inline" else jobs.worker_pool.wake)
        return job_out(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


# --- OCR ------------------------------------------------------------------------------

def scanned_without_text(conn) -> list[tuple[int, str]]:
    """(contract id, file path) of current documents that need OCR and have none yet."""
    from pathlib import Path

    from ..services import ocr
    found = []
    for row in conn.execute("SELECT id, file_path FROM contracts WHERE file_path IS NOT NULL"):
        path = Path(row["file_path"])
        if path.exists() and ocr.cached_text(path) is None and ocr.needs_ocr(path):
            found.append((row["id"], row["file_path"]))
    return found


@router.get("/ocr/status")
def ocr_status(_: dict = Depends(require("user.manage"))):
    from ..services import ocr
    with connection() as conn:
        waiting = len(scanned_without_text(conn)) if ocr.configured() else 0
    return {"configured": ocr.configured(), "scanned_without_text": waiting}


@router.post("/ocr/backfill")
def ocr_backfill(user: dict = Depends(require("user.manage"))):
    from ..services import ocr
    if not ocr.configured():
        raise HTTPException(503, "OCR needs AZURE_DOCINTEL_ENDPOINT and AZURE_DOCINTEL_KEY.")
    with connection() as conn:
        documents = scanned_without_text(conn)
        for contract_id, file_path in documents:
            jobs.enqueue(conn, "ocr", {"contract_id": contract_id, "file_path": file_path, "then_extract": False})
        add_audit(conn, "OCR backfill started", user["name"], details=f"{len(documents)} scanned document(s) queued")
    return {"queued": len(documents)}
