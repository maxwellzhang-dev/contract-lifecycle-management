"""OCR for scanned documents with Azure AI Document Intelligence (prebuilt-read).

Scanned PDFs and images have no text layer, so AI review, questions, version comparison and
document search had nothing to read. When Document Intelligence is configured, such files get
an OCR job; the recognised text is kept next to the upload (`<file>.ocr.txt`) and used
wherever the document's text is needed.
"""

import os
import time
from pathlib import Path

import httpx

API_VERSION = "2024-11-30"
MIN_CHARS_PER_PAGE = 40
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
CONTENT_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".webp": "image/webp"}


class OCRError(Exception):
    pass


def configured() -> bool:
    return bool(os.getenv("AZURE_DOCINTEL_ENDPOINT") and os.getenv("AZURE_DOCINTEL_KEY"))


def text_path(path: Path) -> Path:
    return path.with_name(path.name + ".ocr.txt")


def cached_text(path: Path) -> str | None:
    sidecar = text_path(path)
    return sidecar.read_text(encoding="utf-8") if sidecar.exists() else None


def needs_ocr(path: Path) -> bool:
    """True for images, and for PDFs whose text layer is (nearly) empty."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return True
    if suffix != ".pdf":
        return False
    import pymupdf
    try:
        with pymupdf.open(path) as document:
            chars = sum(len(page.get_text("text").strip()) for page in document)
            return chars < MIN_CHARS_PER_PAGE * max(document.page_count, 1)
    except Exception:
        return False  # unreadable PDFs are reported by extraction instead


def client() -> httpx.Client:  # replaced in tests
    return httpx.Client(timeout=60)


def analyze(path: Path, max_wait: float = 300) -> str:
    """Send the file to Document Intelligence and return the recognised text."""
    endpoint = os.getenv("AZURE_DOCINTEL_ENDPOINT", "").rstrip("/")
    key = os.getenv("AZURE_DOCINTEL_KEY", "")
    model = os.getenv("AZURE_DOCINTEL_MODEL", "prebuilt-read")
    if not (endpoint and key):
        raise OCRError("OCR needs AZURE_DOCINTEL_ENDPOINT and AZURE_DOCINTEL_KEY.")
    headers = {"Ocp-Apim-Subscription-Key": key}
    url = f"{endpoint}/documentintelligence/documentModels/{model}:analyze?api-version={API_VERSION}"
    with client() as http:
        response = http.post(url, content=path.read_bytes(),
                             headers={**headers, "Content-Type": CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")})
        if response.status_code != 202:
            raise OCRError(f"Document Intelligence returned {response.status_code}: {response.text.replace(key, '[redacted]')[:300]}")
        operation = response.headers.get("operation-location")
        if not operation:
            raise OCRError("Document Intelligence did not return an operation to poll.")
        deadline = time.time() + max_wait
        while True:
            result = http.get(operation, headers=headers)
            if result.status_code != 200:
                raise OCRError(f"Document Intelligence returned {result.status_code} while polling.")
            body = result.json()
            status = body.get("status")
            if status == "succeeded":
                return (body.get("analyzeResult") or {}).get("content", "")
            if status == "failed":
                error = (body.get("error") or {}).get("message", "unknown error")
                raise OCRError(f"Document Intelligence could not read the document: {error}")
            if time.time() > deadline:
                raise OCRError("Document Intelligence took too long.")
            time.sleep(float(result.headers.get("retry-after", "1")))


def run(path: Path) -> str:
    """OCR `path` and cache the text. Returns the text."""
    text = analyze(path)
    text_path(path).write_text(text, encoding="utf-8")
    return text
