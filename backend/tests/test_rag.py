"""Document search with the real LightRAG library and fake model functions."""

import hashlib
import json
import time

import pytest

pytest.importorskip("lightrag")

import numpy as np  # noqa: E402  (installed with LightRAG)

from backend.app.services import rag  # noqa: E402

from .test_ai import DOCX, docx_bytes  # noqa: E402


class ByteTokenizer:
    def encode(self, content):
        return list(content.encode("utf-8"))

    def decode(self, tokens):
        return bytes(tokens).decode("utf-8", errors="ignore")


@pytest.fixture()
def fake_rag(monkeypatch, tmp_path):
    from lightrag.utils import EmbeddingFunc, Tokenizer

    for key, value in {"RAG_ENABLED": "1", "AZURE_OPENAI_ENDPOINT": "https://example.invalid", "AZURE_OPENAI_API_KEY": "k",
                       "AZURE_OPENAI_DEPLOYMENT": "d", "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "e", "RAG_DIR": str(tmp_path / "rag")}.items():
        monkeypatch.setenv(key, value)
    prompts = []

    def make_llm():
        async def complete(prompt, system_prompt=None, history_messages=None, **kwargs):
            prompts.append({"prompt": prompt, "system": system_prompt or "", "kwargs": sorted(kwargs)})
            if kwargs.get("response_format"):
                return json.dumps({"high_level_keywords": ["liability"], "low_level_keywords": ["cap"]})
            if "stream" in kwargs:  # the final answer
                return "Hosting Agreement: liability is capped at three months of fees."
            return ""  # entity extraction: nothing; chunks are still indexed
        return complete

    def make_embedding():
        async def embed(texts, **_):
            vectors = []
            for text in texts:
                words = set(text.lower().replace(".", " ").split())
                vector = np.array([1.0 if w in words else 0.0 for w in ("liability", "cap", "capped", "fees", "termination", "notice")]
                                  + [hashlib.sha256(text.encode()).digest()[0] / 2550], dtype=np.float32)
                vectors.append(vector / (np.linalg.norm(vector) or 1))
            return np.array(vectors)
        return EmbeddingFunc(embedding_dim=7, max_token_size=8192, func=embed)

    monkeypatch.setattr(rag, "make_llm_func", make_llm)
    monkeypatch.setattr(rag, "make_embedding_func", make_embedding)
    monkeypatch.setattr(rag, "make_tokenizer", lambda: Tokenizer("bytes", ByteTokenizer()))
    monkeypatch.setattr(rag, "QUEUE_POLL_SECONDS", 0.2)
    yield prompts
    rag.service.shutdown()


def wait_for(client, headers, predicate, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get("/api/ai/rag/status", headers=headers).json()
        if predicate(status):
            return status
        time.sleep(0.3)
    raise AssertionError(f"timed out; last status {status}")


def upload_docx(client, headers, *paragraphs):
    response = client.post("/api/contracts/upload", headers=headers,
                           files={"file": ("contract.docx", docx_bytes(*paragraphs), DOCX)})
    assert response.status_code == 201, response.text
    return response.json()


def test_documents_are_indexed_and_answers_cite_contracts(client, users, fake_rag):
    contract = upload_docx(client, users["procurement"], "Supplier liability is capped at three months of fees.",
                           "Either party may terminate on thirty days notice.")
    status = wait_for(client, users["legal"], lambda s: s["counts"].get("Indexed") == 1)
    assert status["enabled"] and status["documents"] == 1 and status["can_reindex"] is False

    response = client.post("/api/ai/rag/ask", headers=users["legal"], json={"question": "What is the liability cap?"})
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["source"] == "documents" and item["answer"]["found"]
    assert item["answer"]["answer"].startswith("Hosting Agreement")
    assert [c["id"] for c in item["answer"]["contracts"]] == [contract["id"]]
    assert "capped at three months" in item["answer"]["contracts"][0]["reason"]
    assert any("Answer from the contract documents" in (p["prompt"] + p["system"]) for p in fake_rag)
    history = client.get("/api/ai/portfolio", headers=users["legal"]).json()
    assert history[0]["source"] == "documents"

    # A new version replaces the indexed document.
    client.post(f"/api/contracts/{contract['id']}/versions", headers=users["procurement"],
                files={"file": ("v2.docx", docx_bytes("Liability is now capped at twelve months of fees."), DOCX)})
    wait_for(client, users["legal"], lambda s: s["counts"].get("Indexed") == 1 and not s["counts"].get("Queued")
             and not s["counts"].get("Indexing"))
    from backend.app.database import connection
    with connection() as conn:
        assert conn.execute("SELECT indexed_doc_id FROM rag_documents").fetchone()[0] == f"contract-{contract['id']}-v2"

    # Deleting the contract removes it from the index.
    client.delete(f"/api/contracts/{contract['id']}", headers=users["admin"])
    wait_for(client, users["legal"], lambda s: s["counts"] == {})


def test_unreadable_documents_fail_visibly_and_reindex_is_admin_only(client, users, fake_rag):
    client.post("/api/contracts/upload", headers=users["procurement"],
                files={"file": ("scan.pdf", b"%PDF-1.4 not really", "application/pdf")})
    status = wait_for(client, users["admin"], lambda s: s["counts"].get("Failed") == 1)
    assert status["failed"][0]["error"]
    assert client.post("/api/ai/rag/reindex", headers=users["legal"]).status_code == 403
    assert client.post("/api/ai/rag/reindex", headers=users["admin"]).json() == {"queued": 1}


def test_document_search_is_off_by_default(client, users, monkeypatch):
    monkeypatch.delenv("RAG_ENABLED", raising=False)
    status = client.get("/api/ai/rag/status", headers=users["legal"]).json()
    assert status["enabled"] is False and "RAG_ENABLED" in status["reason"]
    assert client.post("/api/ai/rag/ask", headers=users["legal"], json={"question": "x"}).status_code == 503
    assert client.get("/api/ai/rag/status", headers=users["viewer"]).status_code == 403
