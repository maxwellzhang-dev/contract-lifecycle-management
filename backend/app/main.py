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

from .database import connection, init_db
from .routers import approvals, auth, contracts, obligations
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
    yield
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
    return {"status": "ok", "database": "sqlite", "model": os.getenv("AZURE_OPENAI_DEPLOYMENT", "not configured")}


for module in (auth, contracts, approvals, obligations):
    app.include_router(module.router)
