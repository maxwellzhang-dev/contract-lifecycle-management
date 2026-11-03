import base64
import json
import mimetypes
import os
import re
from pathlib import Path

import fitz
import httpx
from docx import Document

FIELDS = [
    "title", "reference_number", "vendor_name", "project_name", "phase",
    "status", "project_start_date", "project_end_date", "renewal_date",
    "currency", "one_time_amount", "optional_amount", "vendor_contact",
    "internal_contact", "deliverables", "payment_terms", "notes",
    "department", "owner",
]

SYSTEM_PROMPT = """You extract contract metadata for a human validation workflow.
Return only facts supported by the supplied contract. Never infer names, dates, values,
or obligations that are not present. Use ISO 8601 YYYY-MM-DD dates. Use ISO 4217 currency
codes. Amounts must be numbers without separators or symbols. Set unknown values to null.
For status, return only one of Quoting, Under Review, Cleared to Start, In Progress,
Completed, On Hold, or Cancelled when it is explicitly supported by the document; otherwise
return null. Do not infer a status from dates, payment terms, signatures, or context.
For every field provide a confidence from 0 to 1. Keep deliverables, payment terms and
notes concise but faithful. A field below 0.75 must appear in low_confidence_fields, and
every null field must appear in missing_fields."""


def schema():
    properties = {field: {"type": ["string", "number", "null"]} for field in FIELDS}
    properties["status"] = {"type": ["string", "null"], "enum": ["Quoting", "Under Review", "Cleared to Start", "In Progress", "Completed", "On Hold", "Cancelled", None]}
    for field in ("one_time_amount", "optional_amount"):
        properties[field] = {"type": ["number", "null"]}
    properties["field_confidences"] = {
        "type": "object",
        "properties": {
            field: {"type": "number", "minimum": 0, "maximum": 1}
            for field in FIELDS
        },
        "required": FIELDS,
        "additionalProperties": False,
    }
    properties["missing_fields"] = {"type": "array", "items": {"type": "string"}}
    properties["low_confidence_fields"] = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": properties,
        "required": FIELDS + ["field_confidences", "missing_fields", "low_confidence_fields"],
        "additionalProperties": False,
    }


def extract_text_and_images(path: Path):
    suffix = path.suffix.lower()
    text = ""
    images = []
    if suffix == ".pdf":
        document = fitz.open(path)
        text = "\n\n".join(page.get_text("text") for page in document)
        if len(text.strip()) < 500:
            for page_number in range(min(5, document.page_count)):
                page = document[page_number]
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                images.append(("image/png", pixmap.tobytes("png")))
    elif suffix == ".docx":
        document = Document(path)
        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        tables = [" | ".join(cell.text for cell in row.cells) for table in document.tables for row in table.rows]
        text = "\n".join(paragraphs + tables)
    elif suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        images.append((mimetypes.guess_type(path.name)[0] or "image/jpeg", path.read_bytes()))
    else:
        raise ValueError("Supported formats are PDF, DOCX, PNG, JPG and WEBP.")
    if len(text.strip()) < 200 and suffix != ".docx":
        from .ocr import cached_text
        ocr_text = cached_text(path)  # scanned document read by Document Intelligence
        if ocr_text and ocr_text.strip():
            text = ocr_text
    return text[:180000], images


def response_text(payload):
    for item in payload.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return content.get("text", "")
    raise ValueError("Azure OpenAI returned no output text.")


def azure_extract(path: Path):
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
    if not endpoint or not api_key or not deployment:
        raise ExtractionNotConfigured("Azure OpenAI environment variables are incomplete.")

    text, images = extract_text_and_images(path)
    content = [{"type": "input_text", "text": f"Extract the contract fields from this document.\n\n{text}"}]
    for mime, image in images:
        encoded = base64.b64encode(image).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "high"})

    payload = {
        "model": deployment,
        "instructions": SYSTEM_PROMPT,
        "input": [{"role": "user", "content": content}],
        "reasoning": {"effort": "low"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "contract_extraction",
                "strict": True,
                "schema": schema(),
            }
        },
        "max_output_tokens": 5000,
        "store": False,
    }
    with httpx.Client(timeout=120) as client:
        response = client.post(
            f"{endpoint}/openai/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        if not response.is_success:
            detail = response.text.replace(api_key, "[redacted]")
            raise ValueError(f"Azure OpenAI returned {response.status_code}: {detail[:900]}")
    result = json.loads(response_text(response.json()))
    confidences = result.get("field_confidences", {})
    populated = [score for field, score in confidences.items() if result.get(field) is not None]
    result["extraction_confidence"] = round(sum(populated) / len(populated), 3) if populated else 0
    result["extraction_note"] = f"Extracted with Azure OpenAI deployment {deployment}."
    return result


def fallback_extract(path: Path, reason: str):
    text, _ = extract_text_and_images(path)
    vendor = None
    for pattern in (r"(?:vendor|supplier|contractor)\s*[:\-]\s*([^\n]{2,80})", r"between\s+([^\n,]{2,80})\s+and"):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            vendor = match.group(1).strip()
            break
    dates = re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", text)
    currency_match = re.search(r"\b(SGD|USD|EUR|GBP|AUD|JPY)\b", text, re.IGNORECASE)
    result = {field: None for field in FIELDS}
    result.update({
        "title": path.stem,
        "vendor_name": vendor,
        "currency": currency_match.group(1).upper() if currency_match else None,
        "notes": f"Candidate dates found: {', '.join(dates[:4])}" if dates else None,
    })
    result["field_confidences"] = {field: (0.5 if result[field] is not None else 0) for field in FIELDS}
    result["missing_fields"] = [field for field in FIELDS if result[field] is None]
    result["low_confidence_fields"] = [field for field in FIELDS if result[field] is not None]
    result["extraction_confidence"] = 0.5
    result["extraction_note"] = f"Local fallback used because AI extraction failed: {reason[:180]}"
    return result


class ExtractionNotConfigured(ValueError):
    """Azure OpenAI is not set up; retrying will not help."""


def extract_contract(path: Path, allow_fallback: bool = True):
    """AI extraction. Falls back to simple local rules when the AI is not configured, or when
    it fails and `allow_fallback` is set (otherwise the error is raised so the job can retry)."""
    try:
        return azure_extract(path)
    except ExtractionNotConfigured as exc:
        return fallback_extract(path, str(exc))
    except Exception as exc:
        if allow_fallback:
            return fallback_extract(path, str(exc))
        raise
