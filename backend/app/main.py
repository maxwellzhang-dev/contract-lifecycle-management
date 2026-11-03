import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import connection, init_db, is_postgres
from . import jobs
from .access import Guard
from .routers import ai, approvals, auth, comments, contracts, insights, members, obligations, signatures, sso, system, templates
from .services import rag
from .workflow import run_reminders

log = logging.getLogger("clause")


def cors_origins() -> list[str]:
    configured = os.getenv("CORS_ORIGINS", "")
    if configured:
        return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    return ["http://localhost:3000", "http://127.0.0.1:3000"]


def reminders_once():
    with connection() as conn:
        return run_reminders(conn)


async def reminder_loop(interval_hours: float):
    while True:
        try:
            created = await asyncio.to_thread(reminders_once)
            if created:
                log.info("Created %s reminder notification(s)", created)
        except Exception:
            log.exception("Reminder run failed")
        await asyncio.sleep(interval_hours * 3600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = None
    if os.getenv("REMINDERS_ENABLED", "1") == "1":
        task = asyncio.create_task(reminder_loop(float(os.getenv("REMINDER_INTERVAL_HOURS", "6"))))
    if os.getenv("JOBS_ENABLED", "1") == "1":
        jobs.worker_pool.start(int(os.getenv("JOB_WORKERS", "2")))
    if rag.enabled():
        try:
            await asyncio.to_thread(rag.service.start)  # resumes any queued indexing
        except Exception:
            log.exception("Could not start document search (LightRAG)")
    yield
    await asyncio.to_thread(rag.service.shutdown)
    await asyncio.to_thread(jobs.worker_pool.stop)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Clause Contract Lifecycle API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "database": "postgresql" if is_postgres() else "sqlite", "model": os.getenv("AZURE_OPENAI_DEPLOYMENT", "not configured")}


for module in (auth, sso, templates, contracts, members, approvals, obligations, comments, insights, ai, signatures, system):
    # Guard: any route about a specific contract answers 404 for users who cannot see it.
    app.include_router(module.router, dependencies=[Guard])
