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
- **Comments and @mentions.** Discuss a contract in its Comments tab. Writing `@Full Name` notifies that person.
- **Tags and advanced filters.** Up to 10 tags per contract. The contracts list filters by status, vendor, department, owner, tag, currency, total value range and end-date range, and the CSV export uses the same filters.
- **Vendors.** One card per vendor with contract counts, value per currency, next end date and open or overdue obligations. Opening a vendor shows its contracts.
- **Reports.** Contract counts and value per currency, grouped by department, vendor, status, owner, tag, start month or end month, with an optional date range. **Download Excel** produces a workbook with the contract list, every grouping and the obligations.
- **Templates and manual contracts.** **New** creates a contract without an upload: either a blank record, or from a template whose `{{placeholders}}` are filled in to generate a Word document (version 1). Admins and Legal manage templates. Two templates are seeded on first start.
- Contract dashboard with lifecycle counts, renewal monitoring, and multi-currency portfolio totals.
- Contract directory with search, status filtering, editing, CSV export, and deletion (admins only).
- PDF, DOCX, PNG, JPG, JPEG and WEBP uploads up to 25 MB.
- Azure OpenAI `gpt-5.4` extraction with field-level confidence scores and missing-field indicators.
- Human review, validation, and a per-contract and global audit trail.
- Side-by-side document review: PDFs are embedded directly, and DOCX files are rendered locally as read-only previews of paragraphs and tables.

## AI assistant

These features use the same Azure OpenAI deployment as extraction (`AZURE_OPENAI_*` in `.env`). They are hidden from viewers, and they show a clear message when the deployment is not configured.

| Feature | Where | What it does |
| --- | --- | --- |
| AI review | Contract → **AI assistant** | Summary, key terms, risky clauses (high/medium/low, with the quoted clause and a suggestion), possibly missing clauses, and places where the record disagrees with the document. One click copies the document's value into the record, or adds a suggested obligation. The latest review is saved and marked out of date when a new document version is uploaded. |
| Ask about this contract | Contract → **AI assistant** | Answers questions from the document and record only, with quotes, and says when the answer is not in the contract. Questions and answers are kept as history. |
| Compare versions | Contract → **AI assistant** (2+ versions) | Lists material changes between two document versions and whether each is favourable to us. |
| Portfolio assistant | **AI assistant** page | Questions across all contracts ("what ends next quarter and what is it worth?", "which vendors have high-risk contracts?"). Answers come from contract records, obligations, approvals and the latest AI reviews; every contract the answer relies on is linked. Totals are kept per currency. History is per user. |
| AI review for approvers | Approvals | When a contract is submitted for approval, an AI review of the submitted document runs in the background (or the existing review of that version is reused). Approvers see the risk counts, top risks and possibly missing clauses on the approval card, and a risk chip in their inbox. A failed AI call never blocks the approval. Turn off with `AI_REVIEW_ON_SUBMIT=0`. |
| AI search | Contracts page | Turns a plain-language request into the normal filters, using only vendors, departments, owners, tags and currencies that exist. |

Notes:

- During these calls, the contract text and metadata are sent to the Azure OpenAI resource with `store: false`. This is the same resource that extraction already uses.
- Prompts treat contract text as data. Instructions written inside a document are ignored.
- Model output can be wrong. Quotes are shown so reviewers can check them, and nothing changes a contract until a person clicks.
- Every AI review and comparison is recorded in the audit trail.

### Document search with LightRAG (optional)

The **AI assistant** page can also answer from the *text* of every contract document ("which contracts cap the supplier's liability, and at what level?"). It uses [LightRAG](https://github.com/HKUDS/LightRAG): each document is split into chunks, the model extracts entities and relationships (parties, clauses, amounts, dates) into a knowledge graph, and questions are answered from the graph plus vector search. Answers link to the contracts and show the passages they used. Switch between **Contract records** (exact figures from the database) and **Document text** (clauses across documents) above the question box.

1. Install the extra packages: `.venv/bin/pip install -r backend/requirements-rag.txt` (Docker: `--build-arg INSTALL_RAG=1`).
2. Deploy an embedding model in Azure OpenAI (for example `text-embedding-3-large`).
3. Set `RAG_ENABLED=1`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` and `RAG_EMBEDDING_DIM` (3072 for `text-embedding-3-large`, 1536 for `text-embedding-3-small`), and restart the API.
4. As an admin, open the AI assistant page, choose **Document text** and click **Rebuild index** to index existing contracts. New uploads, new versions and contracts created from templates are indexed automatically in the background. Deleted contracts are removed from the index.

Notes:

- Indexing calls the model several times per chunk, so it costs noticeably more tokens than the other AI features. `RAG_LLM_DEPLOYMENT` can point indexing and answers at a cheaper deployment than `AZURE_OPENAI_DEPLOYMENT`.
- Scanned documents without a text layer are not indexed yet. They are listed on the page with the reason.
- The index is stored in `RAG_DIR` (default `backend/data/rag`). For several API replicas, set `RAG_STORAGE=postgres` to use LightRAG's PostgreSQL storage. This needs the `pgvector` and Apache AGE extensions and LightRAG's `POSTGRES_*` variables, and has not been tested with Clause yet.
- tiktoken downloads its tokenizer file on first use. In an environment without internet access, set `TIKTOKEN_CACHE_DIR` to a folder that already contains it (the Docker image does this at build time).
- Changing the embedding model or dimension needs a fresh index: empty `RAG_DIR`, then rebuild.

## E-signature

Approved contracts (`Cleared to Start` or `In Progress`) with a document can be sent for signature from the contract's **Signatures** tab.

- Add signers in order (vendor and internal). Each signer gets a personal link when it is their turn; they do not need an account. Links expire after `SIGNING_LINK_DAYS` (default 30) and stop working once used.
- Links are emailed when SMTP is configured, and shown once to the sender so they can be shared another way. Signers who are Clause users also get an in-app notification. A sender can resend a fresh link (the old one stops working).
- The signer reads the document, types their full name exactly as sent (and can draw a signature), and agrees to sign electronically. They can also decline with a reason, which notifies the sender.
- The document version is frozen by its SHA-256 fingerprint. Signing is refused if the stored file no longer matches, and new versions cannot be uploaded while a request is open. Putting the contract on hold or cancelling it voids the request.
- When everyone has signed, Clause produces a signed copy: the original PDF followed by a certificate of completion with each signer's name, email, time, IP address, device and signature, plus the document fingerprint. For DOCX and images, the certificate is produced on its own.
- Every step (sent, opened, signed, declined, voided, completed) is recorded in the audit trail.

This is a simple electronic signature with an audit trail, suitable for most commercial agreements. Contracts that need a qualified or advanced electronic signature (for example under eIDAS) or a certificate-based digital signature need a dedicated signing provider.

## Single sign-on (Microsoft Entra ID)

Clause supports sign-in with Microsoft Entra ID (or any OpenID Connect provider) next to, or instead of, passwords. It uses the authorization code flow with PKCE, and checks the ID token's signature, issuer, audience, expiry and nonce.

1. In the Entra admin center, go to **App registrations → New registration**. Use a single-tenant app with the **Web** redirect URI `https://<your-clause-address>/backend-api/api/auth/sso/callback` (locally: `http://localhost:3000/backend-api/api/auth/sso/callback`).
2. Under **Certificates & secrets**, create a client secret.
3. Optional: under **App roles**, create roles with the values `Clause.Admin`, `Clause.Legal`, `Clause.Procurement`, `Clause.Finance` and `Clause.Viewer`, and assign users or groups in **Enterprise applications**. A user's Clause role is then set from their app role on every sign-in (the highest one wins). Without app roles, roles are managed on the Users page.
4. Set these values in `.env` and restart the API:

| Variable | Meaning |
| --- | --- |
| `ENTRA_TENANT_ID` | Directory (tenant) ID. Or set `SSO_ISSUER` for another OpenID Connect provider. |
| `SSO_CLIENT_ID`, `SSO_CLIENT_SECRET` | Application (client) ID and the client secret |
| `PUBLIC_APP_URL` | Clause's public address; the redirect URI is derived from it unless `SSO_REDIRECT_URI` is set |
| `SSO_AUTO_PROVISION` | `1` (default) creates an account on first sign-in; `0` only lets existing users in |
| `SSO_DEFAULT_ROLE` | Role for new accounts without an app role (default `viewer`) |
| `SSO_ALLOWED_DOMAINS` | Optional comma-separated email domains that may sign in |
| `PASSWORD_LOGIN` | `0` hides password sign-in once SSO works |

Users are matched by their Entra object ID, then by email, so existing accounts keep their history. Deactivated users cannot sign in, and a role change from Entra ends the user's other sessions.

## Who can see which contracts

Roles decide what people can *do*. Each user's **Can see** setting (Users page) decides which contracts they *see*:

| Setting | Sees |
| --- | --- |
| All contracts (default) | Every contract. Admins always see everything. |
| Own department + involved | Contracts whose department matches theirs (case-insensitive), plus the ones they are involved in |
| Only contracts they're involved in | Contracts where they are the owner or renewal owner, uploaded it, submitted or decided an approval, have an approval step waiting for their role, are a signer, or were added as a member |

- Anyone who can edit contracts can share a contract with extra people from **Details → Who can see this contract**. The person is notified.
- Contracts a user cannot see are hidden everywhere: lists, dashboard, vendors, reports, exports, obligations, audit trail, AI answers and @mention notifications. Opening one directly returns "not found".
- Document search (LightRAG) spans every document, so it is only offered to users who see all contracts.
- New single sign-on accounts get `SSO_DEFAULT_ACCESS_SCOPE` (default `all`). With `department`, Clause uses the Entra `department` claim when it is present (add it as an optional claim in the app registration).

## Roles

| Role | Can do |
| --- | --- |
| `admin` | Everything, including deleting contracts, managing users and running reminders. Can decide any approval step and void any signature request. |
| `legal` | Upload, create, edit, validate, submit for approval, send for signature, manage obligations and templates, decide `legal` steps |
| `procurement` | Upload, create, edit, validate, submit for approval, send for signature, manage obligations, decide `procurement` steps |
| `finance` | Edit, manage obligations, decide `finance` steps |
| `viewer` | Read only, plus comments; can read saved AI reviews but not run AI features (no audit trail) |

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
| Uploaded files | `backend/data/uploads/` (signed copies too) |
| Document search index (optional) | `backend/data/rag/`: chunks of document text, extracted entities and embeddings |
| Azure configuration | Root `.env` file, excluded from Git |

Contract files and structured data are stored locally. During AI extraction, AI review, questions and document indexing, document text or scanned pages are sent to the Azure OpenAI resource configured in `.env`. Upload only documents approved for processing by that resource.

## Local development

### Requirements

- Node.js 22.13+
- Python 3.10+

### Install

```bash
# Run these commands from the repository root.
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

To run the same tests against PostgreSQL, point `TEST_DATABASE_URL` at an empty, disposable database. The tests drop and recreate its `public` schema:

```bash
docker compose -f docker-compose.postgres.yml up -d
TEST_DATABASE_URL=postgresql://clause:clause@127.0.0.1:5432/clause .venv/bin/python -m pytest backend/tests
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

## Database

Clause uses SQLite by default (`DATABASE_PATH`). For more than one user at a time, several API replicas or managed backups, use PostgreSQL 14 or later. Set `DATABASE_URL`, and the tables are created on start:

```bash
DATABASE_URL=postgresql://clause:clause@127.0.0.1:5432/clause
```

`docker-compose.postgres.yml` starts a local PostgreSQL with those credentials. In Azure, use Azure Database for PostgreSQL (Flexible Server) and add `?sslmode=require` to the URL.

To move an existing SQLite database into an empty PostgreSQL database, keeping all ids and history:

```bash
.venv/bin/python -m backend.app.copy_to_postgres --sqlite backend/data/contracts.db \
  --postgres postgresql://clause:clause@127.0.0.1:5432/clause
```

Uploaded files are not in the database. Keep `UPLOAD_DIR` pointing at the same folder, or a shared file share when you run several replicas. Sign-in sessions are not copied, so everyone signs in again.

## Deployment

Azure Container Apps deployment guidance is available in [docs/azure-container-apps.md](docs/azure-container-apps.md).

## Prototype scope

- Accounts can sign in with a password or with Microsoft Entra ID. There is no multi-factor authentication for password accounts; use Entra ID (with its MFA policies) and `PASSWORD_LOGIN=0` in production.
- SQLite suits a single machine. Use PostgreSQL (`DATABASE_URL`) for production and for more than one API replica.
- E-signature is a simple electronic signature with an audit trail and certificate, not a qualified or certificate-based digital signature.
- AI extraction runs synchronously during upload. Production use should move extraction to a reliable background job queue.
- Reminders run inside the API process. They are de-duplicated in the database, so several replicas on PostgreSQL do not send duplicates; you can still run them from one scheduled job instead (`REMINDERS_ENABLED=0` on the replicas).
- DOCX preview renders readable paragraphs and tables; it is not a pixel-perfect representation of Word page layout.
