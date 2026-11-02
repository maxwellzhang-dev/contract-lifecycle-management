import os
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .database import PROJECT_ROOT

ALLOWED_SUFFIXES = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def upload_dir() -> Path:
    path = Path(os.getenv("UPLOAD_DIR", PROJECT_ROOT / "backend/data/uploads"))
    path = path if path.is_absolute() else PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_upload(file: UploadFile) -> Path:
    suffix = Path(file.filename or "contract").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, "Supported formats are PDF, DOCX, PNG, JPG and WEBP.")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File exceeds the 25 MB limit.")
    destination = upload_dir() / f"{uuid.uuid4().hex}{suffix}"
    with destination.open("wb") as target:
        shutil.copyfileobj(file.file, target)
    return destination


def stored_file(path_value: str | None) -> Path:
    """Resolve a stored path, refusing anything outside the upload directory."""
    if not path_value:
        raise HTTPException(404, "Contract file not found")
    path = Path(path_value).resolve()
    if upload_dir().resolve() not in path.parents or not path.exists():
        raise HTTPException(404, "Contract file not found")
    return path
