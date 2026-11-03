"""A small durable job queue in the application database.

Slow or flaky work (AI extraction, OCR, emails, automatic AI reviews) is written to the
`jobs` table in the same transaction as the change that needs it, then picked up by worker
threads in the API process. Jobs are claimed atomically, so several workers or API replicas
(on PostgreSQL) never run the same job twice. Failures are retried with exponential backoff;
after the last attempt the job is marked failed and its handler's `on_failure` runs. Jobs left
"running" by a crashed process are picked up again after `STALE_MINUTES`.

`JOBS_MODE=inline` runs each job right after the enqueuing transaction commits, in the same
thread. The test suite uses it so results are visible as soon as a request returns.
"""

import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .database import after_commit, connection, utc_now

log = logging.getLogger("clause.jobs")

STALE_MINUTES = 15
DEFAULT_ATTEMPTS = 4


class Retry(Exception):
    """Raised by a handler to ask for another attempt later."""


@dataclass
class Handler:
    run: Callable[[dict, int], None]          # (payload, attempt) -> None; raise to retry
    on_failure: Callable[[dict, str], None] | None = None
    max_attempts: int = DEFAULT_ATTEMPTS


HANDLERS: dict[str, Handler] = {}


def handler(kind: str, max_attempts: int = DEFAULT_ATTEMPTS, on_failure=None):
    def register(fn):
        HANDLERS[kind] = Handler(fn, on_failure, max_attempts)
        return fn
    return register


def mode() -> str:
    return os.getenv("JOBS_MODE", "worker")


def enqueue(conn, kind: str, payload: dict, delay_seconds: float = 0) -> int:
    """Add a job in the caller's transaction. It runs once that transaction commits."""
    if kind not in HANDLERS:
        raise ValueError(f"Unknown job kind: {kind}")
    now = datetime.now(timezone.utc)
    job_id = conn.execute(
        """INSERT INTO jobs (kind, payload, status, attempts, max_attempts, run_after, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (kind, json.dumps(payload), "queued", 0, HANDLERS[kind].max_attempts,
         (now + timedelta(seconds=delay_seconds)).isoformat(), now.isoformat(), now.isoformat()),
    ).lastrowid
    if mode() == "inline":
        after_commit(conn, lambda: run_job(job_id))
    else:
        after_commit(conn, worker_pool.wake)
    return job_id


# --- running jobs --------------------------------------------------------------------

def claim(worker: str) -> int | None:
    """Mark the next due job as running for `worker` and return its id."""
    now = utc_now()
    with connection() as conn:
        stale = (datetime.now(timezone.utc) - timedelta(minutes=STALE_MINUTES)).isoformat()
        conn.execute("UPDATE jobs SET status='queued', locked_by=NULL WHERE status='running' AND locked_at < ?", (stale,))
        row = conn.execute(
            "SELECT id FROM jobs WHERE status='queued' AND run_after <= ? ORDER BY run_after, id LIMIT 1", (now,)
        ).fetchone()
        if not row:
            return None
        claimed = conn.execute(
            "UPDATE jobs SET status='running', locked_by=?, locked_at=?, updated_at=? WHERE id=? AND status='queued'",
            (worker, now, now, row[0]),
        ).rowcount
    return row[0] if claimed else None


def run_job(job_id: int, worker: str = "inline"):
    with connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return
        if worker == "inline":
            conn.execute("UPDATE jobs SET status='running', locked_by=?, locked_at=? WHERE id=?", (worker, utc_now(), job_id))
    spec = HANDLERS.get(job["kind"])
    attempt = job["attempts"] + 1
    payload = json.loads(job["payload"])
    try:
        if not spec:
            raise RuntimeError(f"No handler for job kind {job['kind']}")
        spec.run(payload, attempt)
    except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"[:1000]
        last = not spec or attempt >= job["max_attempts"] or worker == "inline"
        if last:
            log.warning("Job %s (%s) failed for good: %s", job_id, job["kind"], error)
            with connection() as conn:
                conn.execute("UPDATE jobs SET status='failed', attempts=?, last_error=?, locked_by=NULL, updated_at=? WHERE id=?",
                             (attempt, error, utc_now(), job_id))
            if spec and spec.on_failure:
                try:
                    spec.on_failure(payload, error)
                except Exception:
                    log.exception("on_failure for job %s failed", job_id)
        else:
            delay = min(30 * 2 ** (attempt - 1), 3600)
            log.info("Job %s (%s) attempt %s failed, retrying in %ss: %s", job_id, job["kind"], attempt, delay, error)
            with connection() as conn:
                conn.execute(
                    "UPDATE jobs SET status='queued', attempts=?, last_error=?, run_after=?, locked_by=NULL, updated_at=? WHERE id=?",
                    (attempt, error, (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(), utc_now(), job_id),
                )
        return
    with connection() as conn:
        conn.execute("UPDATE jobs SET status='done', attempts=?, last_error=NULL, locked_by=NULL, updated_at=? WHERE id=?",
                     (attempt, utc_now(), job_id))


def run_due(worker: str = "manual", limit: int = 100) -> int:
    """Run due jobs in this thread (used by tests and the worker loop). Returns how many ran."""
    count = 0
    while count < limit and (job_id := claim(worker)) is not None:
        run_job(job_id, worker)
        count += 1
    return count


class WorkerPool:
    def __init__(self):
        self.threads: list[threading.Thread] = []
        self.event = threading.Event()
        self.stopping = threading.Event()

    def start(self, count: int):
        if self.threads or mode() == "inline":
            return
        self.stopping.clear()
        host = socket.gethostname()
        for index in range(count):
            name = f"{host}:{os.getpid()}:{index}"
            thread = threading.Thread(target=self.loop, args=(name,), name=f"clause-job-{index}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def loop(self, name: str):
        poll = float(os.getenv("JOB_POLL_SECONDS", "2"))
        while not self.stopping.is_set():
            try:
                if run_due(name, limit=10):
                    continue
            except Exception:
                log.exception("Job worker %s crashed; continuing", name)
            self.event.wait(poll)
            self.event.clear()

    def wake(self):
        self.event.set()

    def stop(self):
        self.stopping.set()
        self.event.set()
        for thread in self.threads:
            thread.join(timeout=10)
        self.threads = []


worker_pool = WorkerPool()


def summary(conn) -> dict:
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    return counts


def wait_idle(timeout: float = 30):
    """Block until no job is queued or running (tests)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with connection() as conn:
            busy = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running') AND run_after <= ?",
                                (utc_now(),)).fetchone()[0]
        if not busy:
            return
        time.sleep(0.1)
    raise TimeoutError("jobs still running")
