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
