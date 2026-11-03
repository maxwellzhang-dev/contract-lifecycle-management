import httpx
import pymupdf
import pytest

from backend.app import jobs
from backend.app.services import ocr
from backend.app.services.extraction import extract_text_and_images

from .conftest import upload


def blank_pdf():
    document = pymupdf.open()
    document.new_page()
    data = document.tobytes()
    document.close()
    return data


def text_pdf():
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "This agreement is made between the parties named below. " * 3)
    data = document.tobytes()
    document.close()
    return data


@pytest.fixture()
def docintel(monkeypatch):
    monkeypatch.setenv("AZURE_DOCINTEL_ENDPOINT", "https://di.example")
    monkeypatch.setenv("AZURE_DOCINTEL_KEY", "di-secret")


def test_analyze_polls_until_done(docintel, monkeypatch, tmp_path):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(202, headers={"operation-location": "https://di.example/ops/1"})
        if len(seen) == 2:
            return httpx.Response(200, json={"status": "running"}, headers={"retry-after": "0"})
        return httpx.Response(200, json={"status": "succeeded", "analyzeResult": {"content": "SCANNED TERMS: liability capped."}})
    monkeypatch.setattr(ocr, "client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    scan = tmp_path / "scan.png"
    scan.write_bytes(b"\x89PNG fake")
    assert ocr.run(scan) == "SCANNED TERMS: liability capped."
    post = seen[0]
    assert post.url.path == "/documentintelligence/documentModels/prebuilt-read:analyze"
    assert post.headers["ocp-apim-subscription-key"] == "di-secret" and post.headers["content-type"] == "image/png"
    assert ocr.cached_text(scan) == "SCANNED TERMS: liability capped."

    monkeypatch.setattr(ocr, "client", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, text="bad key di-secret"))))
    with pytest.raises(ocr.OCRError) as error:
        ocr.analyze(scan)
    assert "di-secret" not in str(error.value) and "401" in str(error.value)


def test_needs_ocr(tmp_path):
    for name, data, expected in (("a.pdf", blank_pdf(), True), ("b.pdf", text_pdf(), False), ("c.png", b"x", True)):
        path = tmp_path / name
        path.write_bytes(data)
        assert ocr.needs_ocr(path) is expected, name
    assert ocr.needs_ocr(tmp_path / "d.docx") is False


def test_scanned_upload_is_read_before_extraction(client, users, docintel, monkeypatch):
    from backend.app.routers import contracts
    order = []
    monkeypatch.setattr(ocr, "analyze", lambda path: order.append("ocr") or "MASTER AGREEMENT. Supplier liability is capped at 12 months of fees.")
    original = contracts.extract_contract

    def extract(path, **kwargs):
        order.append("extract")
        text, _ = extract_text_and_images(path)
        assert "capped at 12 months" in text  # extraction sees the OCR text
        return original(path, **kwargs)
    monkeypatch.setattr(contracts, "extract_contract", extract)
    contract = upload(client, users["procurement"], name="scan.pdf", content=blank_pdf())
    assert order == ["ocr", "extract"]
    after = client.get(f"/api/contracts/{contract['id']}", headers=users["procurement"]).json()
    assert after["extraction_status"] == "Done" and after["vendor_name"] == "Acme Cloud"
    audit = client.get(f"/api/audit?contract_id={contract['id']}", headers=users["admin"]).json()
    assert any(a["action"] == "OCR completed" for a in audit)

    # Text PDFs skip OCR.
    order.clear()
    upload(client, users["procurement"], name="text.pdf", content=text_pdf())
    assert order == ["extract"]


def test_ocr_failure_still_extracts(client, users, docintel, monkeypatch):
    def broken(path):
        raise ocr.OCRError("Document Intelligence returned 500")
    monkeypatch.setattr(ocr, "analyze", broken)
    contract = upload(client, users["procurement"], name="scan.pdf", content=blank_pdf())
    after = client.get(f"/api/contracts/{contract['id']}", headers=users["procurement"]).json()
    assert after["extraction_status"] == "Done"
    audit = client.get(f"/api/audit?contract_id={contract['id']}", headers=users["admin"]).json()
    assert any(a["action"] == "OCR failed" for a in audit)


def test_backfill_reads_existing_scans(client, users, docintel, monkeypatch):
    monkeypatch.delenv("AZURE_DOCINTEL_KEY")
    contract = upload(client, users["procurement"], name="old-scan.pdf", content=blank_pdf())  # before OCR was set up
    assert client.post("/api/ocr/backfill", headers=users["admin"]).status_code == 503
    monkeypatch.setenv("AZURE_DOCINTEL_KEY", "di-secret")
    monkeypatch.setattr(ocr, "analyze", lambda path: "OLD SCANNED CONTRACT TEXT")
    assert client.get("/api/ocr/status", headers=users["admin"]).json() == {"configured": True, "scanned_without_text": 1}
    assert client.post("/api/ocr/backfill", headers=users["legal"]).status_code == 403
    assert client.post("/api/ocr/backfill", headers=users["admin"]).json() == {"queued": 1}
    assert client.get("/api/ocr/status", headers=users["admin"]).json()["scanned_without_text"] == 0
    from backend.app.database import connection
    with connection() as conn:
        path = conn.execute("SELECT file_path FROM contracts WHERE id=?", (contract["id"],)).fetchone()[0]
    from pathlib import Path
    assert extract_text_and_images(Path(path))[0] == "OLD SCANNED CONTRACT TEXT"
    assert jobs.mode() == "inline"
