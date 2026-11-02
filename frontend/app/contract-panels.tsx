"use client";

// Tabs of the contract drawer beyond the editable fields: approvals, document versions,
// obligations and the contract's own history.

import { FormEvent, useEffect, useState } from "react";
import { Check, Download, Plus, Trash2, X } from "lucide-react";
import {
  api, approverRoles, can, fileUrl, obligationKinds, obligationStatuses,
  type ApprovalRequest, type AuditItem, type Contract, type DirectoryEntry, type Obligation, type User, type Version,
} from "./api";
import { AuditDetails, Status, amountLabel, dateLabel, dateTimeLabel, errorText, roleLabel } from "./shared";

type PanelProps = { contract: Contract; user: User; onChanged: (contract: Contract) => Promise<void> };

/** Load data for a panel, re-running whenever `key` changes. */
function useLoad<T>(load: () => Promise<T>, key: unknown) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    load().then(result => { if (!cancelled) { setData(result); setError(""); } })
      .catch(err => { if (!cancelled) setError(errorText(err, "Could not load")); });
    return () => { cancelled = true; };
    // `load` is a fresh closure every render; `key` says when its inputs really changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);
  return { data, error, reload: () => setTick(t => t + 1) };
}

// --- approvals -----------------------------------------------------------------------

export function ApprovalsPanel({ contract, user, onChanged }: PanelProps) {
  const { data: requests, error, reload } = useLoad(() => api.approvals(contract.id), `${contract.id}-${contract.status}-${contract.pending_approval_id}`);
  const [chain, setChain] = useState<string[]>(["legal", "finance"]);
  const [note, setNote] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function act(action: () => Promise<unknown>, done: string) {
    setBusy(true); setMessage("");
    try { await action(); setComment(""); setNote(""); reload(); await onChanged(await api.contract(contract.id)); setMessage(done); }
    catch (err) { setMessage(errorText(err, "Action failed")); }
    finally { setBusy(false); }
  }

  const pending = requests?.find(r => r.status === "Pending");
  const past = requests?.filter(r => r.status !== "Pending") ?? [];
  const canSubmit = can(user, "approval.submit") && contract.status === "Quoting" && !pending;
  const toggle = (role: string) => setChain(current => current.includes(role) ? current.filter(r => r !== role) : [...current, role]);

  return <div className="wf-panel">
    {error && <p className="form-error">{error}</p>}
    {pending && <ApprovalCard request={pending} user={user} comment={comment} setComment={setComment} busy={busy}
      onDecide={(decision) => act(() => api.decide(pending.id, decision, comment), decision === "approve" ? "Step approved." : "Request rejected.")}
      onWithdraw={() => act(() => api.withdraw(pending.id), "Request withdrawn.")} />}
    {!pending && contract.status === "Quoting" && (canSubmit ? <form className="wf-card" onSubmit={(e: FormEvent) => { e.preventDefault(); void act(() => api.submitApproval(contract.id, chain, note), "Submitted for approval."); }}>
      <h3>Submit for approval</h3>
      {contract.validation_status !== "Validated" && <p className="wf-hint warn">Confirm and validate the contract details first (Details tab).</p>}
      <p className="wf-hint">Approvers sign off in this order. Each step needs a different person, and you cannot approve your own request.</p>
      <div className="wf-chain">{chain.map((role, index) => <span key={role} className="wf-chip active">{index + 1}. {roleLabel(role)}<button type="button" aria-label={`Remove ${role}`} onClick={() => toggle(role)}><X size={13}/></button></span>)}
        {approverRoles.filter(role => !chain.includes(role)).map(role => <button type="button" key={role} className="wf-chip" onClick={() => toggle(role)}><Plus size={13}/>{roleLabel(role)}</button>)}</div>
      <label className="wf-field">Note for approvers<textarea value={note} onChange={e => setNote(e.target.value)} placeholder="Optional context, e.g. budget line or urgency"/></label>
      <button className="primary-button" disabled={busy || !chain.length || contract.validation_status !== "Validated"}>Submit for approval</button>
    </form> : <p className="wf-hint">This contract has not been submitted for approval.</p>)}
    {!pending && contract.status !== "Quoting" && !past.length && <p className="wf-hint">No approval history for this contract.</p>}
    {message && <p className="save-message">{message}</p>}
    {past.length > 0 && <><h4 className="wf-subhead">History</h4>{past.map(request => <ApprovalCard key={request.id} request={request} user={user} />)}</>}
  </div>;
}

function ApprovalCard({ request, user, comment, setComment, busy, onDecide, onWithdraw }: {
  request: ApprovalRequest; user: User; comment?: string; setComment?: (v: string) => void; busy?: boolean;
  onDecide?: (d: "approve" | "reject") => void; onWithdraw?: () => void;
}) {
  const current = request.steps.find(step => step.status === "Pending");
  const decidedByMe = request.steps.some(step => step.decided_by === user.id);
  const canDecide = !!current && request.status === "Pending" && (user.role === current.role || user.role === "admin")
    && request.submitted_by !== user.id && !decidedByMe;
  const canWithdraw = request.status === "Pending" && (request.submitted_by === user.id || user.role === "admin");
  return <article className="wf-card">
    <header className="wf-card-head"><div><h3>Approval request #{request.id}</h3><small>Submitted by {request.submitted_by_name} · {dateTimeLabel(request.submitted_at)}</small></div><Status value={request.status} /></header>
    {request.note && <p className="wf-note">“{request.note}”</p>}
    <ol className="wf-steps">{request.steps.map(step => <li key={step.id} className={`wf-step ${step.status.toLowerCase()}`}>
      <span className="wf-step-dot">{step.status === "Approved" ? <Check size={13}/> : step.status === "Rejected" ? <X size={13}/> : step.step_order}</span>
      <div><strong>{roleLabel(step.role)}</strong><small>{step.status === "Pending" ? "Waiting for decision" : step.status === "Waiting" ? "After previous step" : step.decided_by_name ? `${step.status} by ${step.decided_by_name} · ${dateTimeLabel(step.decided_at)}` : step.status}</small>{step.comment && <p>{step.comment}</p>}</div>
    </li>)}</ol>
    {canDecide && setComment && <div className="wf-decide">
      <label className="wf-field">Comment<textarea value={comment} onChange={e => setComment(e.target.value)} placeholder="Required when rejecting"/></label>
      <div className="wf-actions"><button className="danger-button" disabled={busy} onClick={() => onDecide?.("reject")}>Reject</button><button className="primary-button" disabled={busy} onClick={() => onDecide?.("approve")}>Approve as {roleLabel(current!.role)}</button></div>
    </div>}
    {!canDecide && current && request.status === "Pending" && <p className="wf-hint">Waiting for a {current.role} approver{request.submitted_by === user.id ? " (you submitted this request)" : ""}.</p>}
    {canWithdraw && onWithdraw && <button className="secondary-button" disabled={busy} onClick={onWithdraw}>Withdraw request</button>}
  </article>;
}

// --- versions ------------------------------------------------------------------------

export function VersionsPanel({ contract, user, onChanged }: PanelProps) {
  const { data: versions, error, reload } = useLoad(() => api.versions(contract.id), `${contract.id}-${contract.document_version}`);
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const blocked = contract.pending_approval_id ? "Withdraw the pending approval before uploading a new version."
    : ["Completed", "Cancelled"].includes(contract.status) ? `${contract.status} contracts cannot receive new versions.` : "";

  async function submit(event: FormEvent) {
    event.preventDefault(); if (!file) return;
    setBusy(true); setMessage("");
    const form = new FormData(); form.append("file", file); form.append("note", note);
    try { const updated = await api.uploadVersion(contract.id, form); setFile(null); setNote(""); reload(); await onChanged(updated); setMessage(`Version ${updated.document_version} uploaded. Re-validate the details against the new document.`); }
    catch (err) { setMessage(errorText(err, "Upload failed")); }
    finally { setBusy(false); }
  }

  return <div className="wf-panel">
    {error && <p className="form-error">{error}</p>}
    {can(user, "contract.edit") && (blocked ? <p className="wf-hint">{blocked}</p> : <form className="wf-card" onSubmit={submit}>
      <h3>Upload a new version</h3>
      <p className="wf-hint">The previous file is kept. The record goes back to the review queue for validation.</p>
      <input type="file" accept=".pdf,.docx,.png,.jpg,.jpeg,.webp" onChange={e => setFile(e.target.files?.[0] ?? null)} />
      <label className="wf-field">What changed?<input value={note} onChange={e => setNote(e.target.value)} placeholder="e.g. Vendor redlines on liability"/></label>
      <button className="primary-button" disabled={!file || busy}>{busy ? "Uploading…" : "Upload version"}</button>
    </form>)}
    {message && <p className="save-message">{message}</p>}
    {versions && !versions.length && <p className="wf-hint">No documents uploaded for this contract.</p>}
    {!!versions?.length && <ul className="wf-list">{versions.map((v: Version) => <li key={v.id}>
      <span className="wf-badge">v{v.version}</span>
      <div><strong>{v.file_name ?? "Document"}</strong><small>{v.uploaded_by ?? "Unknown"} · {dateTimeLabel(v.uploaded_at)}{v.file_size ? ` · ${(v.file_size / 1024).toFixed(0)} KB` : ""}</small>{v.note && <p>{v.note}</p>}</div>
      {v.version === contract.document_version && <em className="wf-current">Current</em>}
      <a className="wf-icon-link" href={fileUrl(contract.id, v.version)} target="_blank" rel="noreferrer" aria-label={`Open version ${v.version}`}><Download size={16}/></a>
    </li>)}</ul>}
  </div>;
}

// --- obligations ---------------------------------------------------------------------

const emptyObligation = { title: "", kind: "Deliverable", due_date: "", amount: "", owner: "" };

export function ObligationsPanel({ contract, user }: PanelProps) {
  const { data: items, error, reload } = useLoad(() => api.obligations(`?contract_id=${contract.id}`), contract.id);
  const { data: people } = useLoad(() => api.directory(), "directory");
  const [form, setForm] = useState(emptyObligation);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const manage = can(user, "obligation.manage");

  async function run(action: () => Promise<unknown>) {
    setBusy(true); setMessage("");
    try { await action(); reload(); } catch (err) { setMessage(errorText(err, "Action failed")); } finally { setBusy(false); }
  }

  function add(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      await api.createObligation(contract.id, {
        title: form.title, kind: form.kind, due_date: form.due_date || null, owner: form.owner || null,
        amount: form.amount ? Number(form.amount) : null, currency: form.amount ? contract.currency ?? null : null,
      });
      setForm(emptyObligation);
    });
  }

  return <div className="wf-panel">
    {error && <p className="form-error">{error}</p>}
    {manage && <form className="wf-card wf-obligation-form" onSubmit={add}>
      <h3>Add obligation or milestone</h3>
      <div className="wf-grid">
        <label className="wf-field wide">Title<input required value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} placeholder="e.g. Go-live sign-off"/></label>
        <label className="wf-field">Type<select value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value })}>{obligationKinds.map(k => <option key={k}>{k}</option>)}</select></label>
        <label className="wf-field">Due date<input type="date" value={form.due_date} onChange={e => setForm({ ...form, due_date: e.target.value })}/></label>
        <label className="wf-field">Amount {contract.currency ? `(${contract.currency})` : ""}<input type="number" min="0" step="0.01" value={form.amount} onChange={e => setForm({ ...form, amount: e.target.value })}/></label>
        <label className="wf-field">Owner<input list="people" value={form.owner} onChange={e => setForm({ ...form, owner: e.target.value })} placeholder="Gets the reminders"/></label>
      </div>
      <datalist id="people">{(people as DirectoryEntry[] | null)?.map(p => <option key={p.id} value={p.name}/>)}</datalist>
      <button className="primary-button" disabled={busy || !form.title.trim()}>Add</button>
    </form>}
    {message && <p className="form-error">{message}</p>}
    {items && !items.length && <p className="wf-hint">No obligations recorded yet.</p>}
    {!!items?.length && <ul className="wf-list">{items.map((item: Obligation) => <li key={item.id} className={item.overdue ? "overdue" : ""}>
      <span className="wf-badge">{item.kind}</span>
      <div><strong>{item.title}</strong><small>{item.due_date ? `Due ${dateLabel(item.due_date)}` : "No due date"}{item.overdue ? ` · ${-item.days_until_due!} days overdue` : ""}{item.amount != null ? ` · ${amountLabel(item.currency, item.amount)}` : ""}{item.owner ? ` · ${item.owner}` : ""}</small>{item.completed_by && <small>{item.status} by {item.completed_by} · {dateLabel(item.completed_at)}</small>}</div>
      {manage ? <select aria-label="Obligation status" value={item.status} disabled={busy} onChange={e => void run(() => api.updateObligation(item.id, { status: e.target.value }))}>{obligationStatuses.map(s => <option key={s}>{s}</option>)}</select> : <Status value={item.overdue ? "Overdue" : item.status}/>}
      {manage && <button className="wf-icon-link" aria-label="Remove obligation" disabled={busy} onClick={() => { if (window.confirm(`Remove “${item.title}”?`)) void run(() => api.deleteObligation(item.id)); }}><Trash2 size={16}/></button>}
    </li>)}</ul>}
  </div>;
}

// --- history -------------------------------------------------------------------------

export function HistoryPanel({ contract, user }: PanelProps) {
  const allowed = can(user, "audit.read");
  const { data: items, error } = useLoad(() => allowed ? api.audit(contract.id) : Promise.resolve([] as AuditItem[]), `${contract.id}-${contract.status}-${contract.validation_status}-${contract.document_version}`);
  if (!allowed) return <div className="wf-panel"><p className="wf-hint">Your role cannot view the audit history.</p></div>;
  return <div className="wf-panel">
    {error && <p className="form-error">{error}</p>}
    {items && !items.length && <p className="wf-hint">No activity recorded yet.</p>}
    <ul className="wf-list wf-history">{items?.map(item => <li key={item.id}>
      <div><strong>{item.action}</strong><small>{item.actor} · {dateTimeLabel(item.created_at)}</small><AuditDetails details={item.details}/></div>
    </li>)}</ul>
  </div>;
}
