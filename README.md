# Clause — Contract Lifecycle Management Prototype

Clause is a local Contract Lifecycle Management (CLM) prototype for managing contract documents, structured metadata, lifecycle status, and portfolio reporting. It combines AI-assisted document extraction with human review while keeping business decisions under user control.

## Contract lifecycle

| Status | Description |
| --- | --- |
| `Quoting` | The vendor has submitted, or is preparing, a quotation or proposal. |
| `Under Review` | The contract or proposal is being reviewed by Legal, Procurement, or relevant stakeholders. |
| `Cleared to Start` | Required internal approvals are complete and the vendor is cleared to begin work. |
| `In Progress` | The vendor has started delivering the contracted work or service. |
| `Completed` | Deliverables have been received and accepted. |
| `On Hold` | The engagement is temporarily paused. |
| `Cancelled` | The engagement has been discontinued before completion. |

The typical lifecycle is `Quoting → Under Review → Cleared to Start → In Progress → Completed`. New uploads default to `Quoting` unless the source document explicitly supports a standardized status. Confirming AI-extracted metadata does not automatically change the business lifecycle status.

## Features

- Contract dashboard with lifecycle counts, renewal monitoring, and multi-currency portfolio totals.
- Interactive **Contract Total Value** card, calculated as `one_time_amount + optional_amount` and grouped by currency without exchange-rate conversion.
- Contract directory with search, status filtering, editing, CSV export, and contract deletion.
- PDF, DOCX, PNG, JPG, JPEG, and WEBP uploads up to 25 MB.
- Azure OpenAI `gpt-5.4` extraction with field-level confidence scores and missing-field indicators.
- Human review, metadata updates, validation, and an audit trail.
- Side-by-side document review: PDFs are embedded directly and DOCX files are rendered locally as read-only previews of paragraphs and tables.

## Data handling

| Data | Location / behaviour |
| --- | --- |
| Contract records | `backend/data/contracts.db` |
| Uploaded files | `backend/data/uploads/` |
| Azure configuration | Root `.env` file, excluded from Git |

Contract files and structured data are stored locally. During AI extraction, document text or scanned pages are sent to the Azure OpenAI resource configured in `.env`. Upload only documents approved for processing by that resource.

## Local development

### Requirements

- Node.js 22.13+
- Python 3.10+

### Install

```bash
# Run these commands from the repository root.
cd others/contract-lifecycle-prototype

python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

cd frontend
npm ci
cd ..

cp .env.example .env
```

Configure the Azure OpenAI values in the newly created `.env` file. Do not commit this file.

### Run

```bash
./start.sh
```

The local services are available at:

- Web application: http://localhost:3000
- API: http://127.0.0.1:8000
- API documentation: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

Use `Ctrl+C` to stop both services.

## Deployment

Azure Container Apps deployment guidance is available in [docs/azure-container-apps.md](docs/azure-container-apps.md).

## Prototype scope

- Authentication uses a local demonstration user; Microsoft Entra ID is not yet integrated.
- SQLite is appropriate for a single-machine prototype. A multi-user production deployment should use PostgreSQL or another managed database.
- AI extraction runs synchronously during upload. Production use should move extraction to a reliable background job queue.
- DOCX preview renders readable paragraphs and tables; it is not a pixel-perfect representation of Word page layout.
