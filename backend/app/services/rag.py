"""Graph RAG over contract documents with LightRAG.

Optional: needs `pip install -r backend/requirements-rag.txt`, RAG_ENABLED=1, the Azure
OpenAI settings and an embedding deployment. LightRAG binds its storage to one asyncio
event loop, so it lives on a dedicated background thread; the API talks to it through
`run()`. Documents are indexed from a queue table, so uploads never wait on indexing.
"""

import asyncio
import logging
import os
import threading
from concurrent.futures import Future
from pathlib import Path

import httpx

from ..database import PROJECT_ROOT, connection, utc_now
from . import llm

log = logging.getLogger("clause.rag")

DOC_PREFIX = "contract-"
QUEUE_POLL_SECONDS = 5


class RagUnavailable(Exception):
    """Shown to the user as-is."""


def installed() -> bool:
    try:
        import lightrag  # noqa: F401
    except ImportError:
        return False
    return True


def enabled() -> bool:
    return (os.getenv("RAG_ENABLED", "0") == "1" and llm.configured()
            and bool(os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")) and installed())


def status_reason() -> str | None:
    if os.getenv("RAG_ENABLED", "0") != "1":
        return "Document search is off (RAG_ENABLED=0)."
    if not installed():
        return "LightRAG is not installed: pip install -r backend/requirements-rag.txt"
    if not llm.configured():
        return "Document search needs the Azure OpenAI settings."
    if not os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"):
        return "Document search needs AZURE_OPENAI_EMBEDDING_DEPLOYMENT (an embedding model deployment)."
    return None


def working_dir() -> Path:
    path = Path(os.getenv("RAG_DIR", PROJECT_ROOT / "backend/data/rag"))
    path = path if path.is_absolute() else PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def doc_id(contract_id: int, version: int) -> str:
    return f"{DOC_PREFIX}{contract_id}-v{version}"


def contract_id_from_path(file_path: str | None) -> int | None:
    if file_path and file_path.startswith(DOC_PREFIX):
        try:
            return int(file_path[len(DOC_PREFIX):])
        except ValueError:
            return None
    return None


# --- model functions (tests replace these factories) ---------------------------------

def azure() -> tuple[str, str]:
    return os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/"), os.getenv("AZURE_OPENAI_API_KEY", "")


def make_llm_func():
    deployment = os.getenv("RAG_LLM_DEPLOYMENT") or os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
    effort = os.getenv("RAG_REASONING_EFFORT", "low")
    client = httpx.AsyncClient(timeout=300)

    async def complete(prompt, system_prompt=None, history_messages=None, **kwargs) -> str:
        endpoint, key = azure()
        messages = [{"role": m["role"], "content": m["content"]} for m in history_messages or []]
        messages.append({"role": "user", "content": prompt})
        payload = {"model": deployment, "input": messages, "reasoning": {"effort": effort}, "store": False,
                   "max_output_tokens": 8000}
        if system_prompt:
            payload["instructions"] = system_prompt
        if kwargs.get("response_format") or kwargs.get("keyword_extraction"):
            payload["text"] = {"format": {"type": "json_object"}}
            payload["instructions"] = (payload.get("instructions", "") + "\nReply with a JSON object only.").strip()
        response = await client.post(f"{endpoint}/openai/v1/responses", json=payload,
                                     headers={"Authorization": f"Bearer {key}"})
        if not response.is_success:
            raise RuntimeError(f"Azure OpenAI returned {response.status_code}: {response.text.replace(key, '[redacted]')[:300]}")
        from .extraction import response_text
        return response_text(response.json())

    return complete


def make_embedding_func():
    import numpy as np
    from lightrag.utils import EmbeddingFunc

    deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")
    dim = int(os.getenv("RAG_EMBEDDING_DIM", "3072"))
    client = httpx.AsyncClient(timeout=120)

    async def embed(texts: list[str], **_):
        endpoint, key = azure()
        response = await client.post(f"{endpoint}/openai/v1/embeddings", json={"model": deployment, "input": texts},
                                     headers={"Authorization": f"Bearer {key}"})
        if not response.is_success:
            raise RuntimeError(f"Azure OpenAI embeddings returned {response.status_code}: {response.text.replace(key, '[redacted]')[:300]}")
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        return np.array([item["embedding"] for item in data], dtype=np.float32)

    return EmbeddingFunc(embedding_dim=dim, max_token_size=8192, func=embed)


def make_tokenizer():
    return None  # LightRAG's default (tiktoken)


def storage_options() -> dict:
    if os.getenv("RAG_STORAGE", "file") == "postgres":
        # LightRAG's own PostgreSQL storages (need the pgvector and Apache AGE extensions and
        # LightRAG's POSTGRES_* variables). Required when several API replicas share the index.
        return {"kv_storage": "PGKVStorage", "vector_storage": "PGVectorStorage",
                "graph_storage": "PGGraphStorage", "doc_status_storage": "PGDocStatusStorage"}
    return {}


# --- the background service ----------------------------------------------------------

class RagService:
    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.rag = None
        self.lock = threading.Lock()
        self.wake: asyncio.Event | None = None
        self.worker: asyncio.Task | None = None

    def start(self, with_worker: bool = True):
        with self.lock:
            if self.loop:
                return
            if not enabled():
                raise RagUnavailable(status_reason() or "Document search is not available.")
            ready = threading.Event()

            def run_loop():
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                ready.set()
                self.loop.run_forever()

            self.thread = threading.Thread(target=run_loop, name="clause-rag", daemon=True)
            self.thread.start()
            ready.wait()
            try:
                self._submit(self._init(with_worker)).result(timeout=120)
            except Exception:
                self._stop_loop()
                raise

    async def _init(self, with_worker: bool):
        from lightrag import LightRAG

        options = {"tokenizer": tokenizer} if (tokenizer := make_tokenizer()) else {}
        self.rag = LightRAG(working_dir=str(working_dir()), llm_model_func=make_llm_func(),
                            llm_model_name=os.getenv("RAG_LLM_DEPLOYMENT") or os.getenv("AZURE_OPENAI_DEPLOYMENT", "azure"),
                            embedding_func=make_embedding_func(), **storage_options(), **options)
        await self.rag.initialize_storages()
        self.wake = asyncio.Event()
        if with_worker:
            self.worker = asyncio.create_task(self._work_forever())

    def _submit(self, coro) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def run(self, coro_factory, timeout: float = 180):
        """Run `coro_factory(rag)` on the LightRAG loop and wait for the result."""
        self.start()
        return self._submit(coro_factory(self.rag)).result(timeout=timeout)

    def poke(self):
        """Wake the indexing worker, starting the service in the background if needed."""
        if self.loop and self.wake:
            self.loop.call_soon_threadsafe(self.wake.set)
            return

        def start_quietly():
            try:
                self.start()
            except Exception:
                log.exception("Could not start document search (LightRAG)")
        threading.Thread(target=start_quietly, name="clause-rag-start", daemon=True).start()

    async def _work_forever(self):
        while True:
            try:
                await self.process_queue()
            except Exception:
                log.exception("Document indexing failed")
            try:
                await asyncio.wait_for(self.wake.wait(), QUEUE_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    async def process_queue(self) -> int:
        """Index or remove every queued document. Returns how many were handled."""
        handled = 0
        while True:
            with connection() as conn:
                job = conn.execute(
                    "SELECT * FROM rag_documents WHERE status IN ('Queued','Delete') ORDER BY updated_at LIMIT 1").fetchone()
                if not job:
                    return handled
                conn.execute("UPDATE rag_documents SET status=?, updated_at=? WHERE contract_id=?",
                             ("Removing" if job["status"] == "Delete" else "Indexing", utc_now(), job["contract_id"]))
            try:
                if job["status"] == "Delete":
                    await self._delete(job["indexed_doc_id"])
                    with connection() as conn:
                        conn.execute("DELETE FROM rag_documents WHERE contract_id=? AND status='Removing'", (job["contract_id"],))
                else:
                    await self._index(job)
            except Exception as exc:
                log.warning("Indexing contract %s failed: %s", job["contract_id"], exc)
                with connection() as conn:
                    conn.execute("UPDATE rag_documents SET status='Failed', error=?, updated_at=? WHERE contract_id=?",
                                 (str(exc)[:500], utc_now(), job["contract_id"]))
            handled += 1

    async def _delete(self, document_id: str | None):
        if document_id:
            await self.rag.adelete_by_doc_id(document_id)

    async def _index(self, job):
        from ..storage import stored_file
        from .extraction import extract_text_and_images

        with connection() as conn:
            contract = conn.execute("SELECT * FROM contracts WHERE id=?", (job["contract_id"],)).fetchone()
        if not contract or not contract["file_path"]:
            raise RuntimeError("The contract or its document no longer exists.")
        text, _ = await asyncio.to_thread(extract_text_and_images, stored_file(contract["file_path"]))
        if not text.strip():
            raise RuntimeError("No readable text (scanned documents are not indexed yet).")
        new_id = doc_id(contract["id"], contract["document_version"])
        header = (f"Contract #{contract['id']}: {contract['title']}"
                  + (f" with {contract['vendor_name']}" if contract["vendor_name"] else "")
                  + (f", reference {contract['reference_number']}" if contract["reference_number"] else "") + ".")
        if job["indexed_doc_id"] and job["indexed_doc_id"] != new_id:
            await self._delete(job["indexed_doc_id"])
        await self.rag.ainsert(f"{header}\n\n{text}", ids=[new_id], file_paths=[f"{DOC_PREFIX}{contract['id']}"])
        status = (await self.rag.aget_docs_by_ids([new_id])).get(new_id) or {}
        state = status.get("status") if isinstance(status, dict) else getattr(status, "status", None)
        state = getattr(state, "value", state)
        if state != "processed":
            error = status.get("error_msg") if isinstance(status, dict) else getattr(status, "error_msg", None)
            raise RuntimeError(f"LightRAG could not process the document ({state or 'unknown'}). {error or ''}".strip())
        with connection() as conn:
            conn.execute(
                "UPDATE rag_documents SET status='Indexed', indexed_doc_id=?, document_version=?, error=NULL, updated_at=? WHERE contract_id=?",
                (new_id, contract["document_version"], utc_now(), contract["id"]),
            )

    def _stop_loop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=10)
        self.loop = self.thread = self.rag = self.worker = None

    def shutdown(self):
        with self.lock:
            if not self.loop:
                return
            async def close():
                if self.worker:
                    self.worker.cancel()
                if self.rag:
                    await self.rag.finalize_storages()
            try:
                self._submit(close()).result(timeout=30)
            except Exception:
                log.exception("Closing LightRAG failed")
            self._stop_loop()


service = RagService()


# --- queue helpers used by the routers -----------------------------------------------

def queue(conn, contract_id: int):
    """Ask for the contract's current document to be (re)indexed. No-op when RAG is off."""
    if not enabled():
        return
    row = conn.execute("SELECT 1 FROM rag_documents WHERE contract_id=?", (contract_id,)).fetchone()
    if row:
        conn.execute("UPDATE rag_documents SET status='Queued', error=NULL, updated_at=? WHERE contract_id=?",
                     (utc_now(), contract_id))
    else:
        conn.execute("INSERT INTO rag_documents (contract_id, status, updated_at) VALUES (?,?,?)",
                     (contract_id, "Queued", utc_now()))
    service.poke()


def queue_delete(conn, contract_id: int):
    if not enabled():
        return
    conn.execute("UPDATE rag_documents SET status='Delete', updated_at=? WHERE contract_id=? AND indexed_doc_id IS NOT NULL",
                 (utc_now(), contract_id))
    conn.execute("DELETE FROM rag_documents WHERE contract_id=? AND indexed_doc_id IS NULL", (contract_id,))
    service.poke()
