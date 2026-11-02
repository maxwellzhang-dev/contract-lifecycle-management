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

The typical lifecycle is `Quoting → Under Review → Cleared to Start → In Progress → Completed`. The API enforces it:

| From | Allowed next status |
| --- | --- |
| `Quoting` | `Under Review` (only by submitting for approval), `On Hold`, `Cancelled` |
| `Under Review` | `Cleared to Start` (only when the last approval step is approved), back to `Quoting` (rejected or withdrawn), `On Hold`, `Cancelled` |
| `Cleared to Start` | `In Progress`, `On Hold`, `Cancelled` |
| `In Progress` | `Completed`, `On Hold`, `Cancelled` |
| `On Hold` | the status it was in before the hold, or `Cancelled`. A hold during review cancels the approval, so it resumes in `Quoting`. |
| `Completed`, `Cancelled` | none (final) |

New uploads always start in `Quoting`. Confirming AI-extracted metadata does not change the business status.

## Features

- **Sign-in and roles.** Local accounts with hashed passwords and session cookies. Every action is recorded under the signed-in user.
- **Approval workflow.** A validated contract in `Quoting` is submitted with an ordered chain of approver roles (default Legal → Finance). Each step is decided by a different person, and nobody can approve a request they submitted. Rejecting requires a comment and returns the contract to `Quoting`. The submitter or an admin can withdraw a request. While a request is pending, the contract terms are locked.
- **Approvals inbox.** Lists the steps waiting for the signed-in user's role.
- **Document versions.** Upload a new version of a contract document. Older files are kept and can still be downloaded. A new version sends the record back to the review queue for validation.
- **Obligations and milestones.** Deliverables, payments, milestones and compliance items with due dates, amounts and owners, per contract and across the portfolio.
- **Notifications and reminders.** In-app notifications for approval requests and decisions, status changes, contract end dates (60/30/7 days), renewal dates and notice deadlines (30/7 days), and obligations (7 days before and when overdue). Reminders go to the user whose name matches the contract owner, renewal owner or obligation owner, or to admins and procurement when nobody matches. They run every 6 hours, and an admin can trigger a run manually with `POST /api/reminders/run`. Email is optional: set the `SMTP_*` variables in `.env`.
- **User administration.** Admins add users, change roles, reset passwords and deactivate accounts.
- Contract dashboard with lifecycle counts, renewal monitoring, and multi-currency portfolio totals.
- Contract directory with search, status filtering, editing, CSV export, and deletion (admins only).
- PDF, DOCX, PNG, JPG, JPEG and WEBP uploads up to 25 MB.
- Azure OpenAI `gpt-5.4` extraction with field-level confidence scores and missing-field indicators.
- Human review, validation, and a per-contract and global audit trail.
- Side-by-side document review: PDFs are embedded directly, and DOCX files are rendered locally as read-only previews of paragraphs and tables.

## Roles

| Role | Can do |
| --- | --- |
| `admin` | Everything, including deleting contracts, managing users and running reminders. Can decide any approval step. |
| `legal` | Upload, edit, validate, submit for approval, manage obligations, decide `legal` steps |
| `procurement` | Upload, edit, validate, submit for approval, manage obligations, decide `procurement` steps |
| `finance` | Edit, manage obligations, decide `finance` steps |
| `viewer` | Read only (no audit trail) |

On first start, the database is seeded with these demo accounts. They all use the password from `CLAUSE_SEED_PASSWORD` (default `ChangeMe123!`):

| Email | Name | Role |
| --- | --- | --- |
| `maxwell@clause.local` | Maxwell Zhang | admin |
| `legal@clause.local` | Lena Ong | legal |
| `procurement@clause.local` | Daniel Tan | procurement |
| `finance@clause.local` | Priya Nair | finance |
| `viewer@clause.local` | Victor Lim | viewer |

Change these passwords (account menu → Change password) before sharing the app with anyone. Set `SEED_DEMO_DATA=0` to start without demo contracts.

Existing `contracts.db` files are upgraded automatically on start: new tables and columns are added, and each existing document becomes version 1.

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

### Test

```bash
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/python -m pytest backend/tests
```

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

- Authentication uses local accounts stored in SQLite. Microsoft Entra ID (SSO) is not yet integrated.
- SQLite is appropriate for a single-machine prototype. A multi-user production deployment should use PostgreSQL or another managed database.
- AI extraction runs synchronously during upload. Production use should move extraction to a reliable background job queue.
- Reminders run inside the API process. With more than one API replica, run them from a single scheduled job instead (`REMINDERS_ENABLED=0` on the replicas).
- DOCX preview renders readable paragraphs and tables; it is not a pixel-perfect representation of Word page layout.
