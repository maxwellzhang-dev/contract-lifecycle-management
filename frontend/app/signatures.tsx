"use client";

// E-signature: the contract drawer's Signatures tab, and the public signing page.

import { FormEvent, PointerEvent, useEffect, useRef, useState } from "react";
import { Check, Copy, Download, FileSignature, Plus, RotateCcw, Trash2, X } from "lucide-react";
import {
  api, can, signedCopyUrl, signingDocumentUrl,
  type Contract, type SignatureRequest, type SigningView, type User,
} from "./api";
import { Status, dateTimeLabel, errorText } from "./shared";

type PanelProps = { contract: Contract; user: User; onChanged: (contract: Contract) => Promise<void> };
const signable = new Set(["Cleared to Start", "In Progress"]);

export function SignaturesPanel({ contract, user, onChanged }: PanelProps) {
  const [requests, setRequests] = useState<SignatureRequest[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [signers, setSigners] = useState([{ name: "", email: "" }, { name: user.name, email: user.email }]);
  const [message, setMessage] = useState("");
  const [composing, setComposing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.signatures(contract.id).then(r => { if (!cancelled) setRequests(r); }).catch(err => { if (!cancelled) setError(errorText(err, "Could not load signatures")); });
    return () => { cancelled = true; };
  }, [contract.id]);

  // Links come back only from the call that created them, so keep them on screen.
  function merge(updated: SignatureRequest) {
    setRequests(current => (current ?? []).some(r => r.id === updated.id)
      ? current!.map(r => r.id === updated.id ? { ...updated, signers: updated.signers.map(s => ({ ...s, link: s.link ?? r.signers.find(o => o.id === s.id)?.link ?? null })) } : r)
      : [updated, ...(current ?? [])]);
  }
  async function run(action: () => Promise<SignatureRequest>, after?: () => void) {
    setBusy(true); setError("");
    try { merge(await action()); after?.(); await onChanged(await api.contract(contract.id)); }
    catch (err) { setError(errorText(err, "Something went wrong")); }
    finally { setBusy(false); }
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    await run(() => api.sendForSignature(contract.id, signers.filter(s => s.name.trim() || s.email.trim()), message), () => setMessage(""));
  }

  const active = requests?.find(r => r.status === "Sent");
  const canSend = can(user, "signature.send");
  const blocked = !signable.has(contract.status) ? `Contracts can be sent for signature once approved. This one is ${contract.status}.`
    : !contract.file_name ? "Upload the contract document first." : "";

  return <div className="wf-panel">
    {error && <p className="form-error">{error}</p>}
    {contract.signed_at && <p className="wf-hint ok"><Check size={14}/> Fully signed on {dateTimeLabel(contract.signed_at)}.</p>}
    {canSend && !active && !blocked && contract.signed_at && !composing && <button className="secondary-button" onClick={() => setComposing(true)}><FileSignature size={14}/> Send again for signature</button>}
    {canSend && !active && (blocked ? <p className="wf-hint">{blocked}</p> : (!contract.signed_at || composing) && <form className="wf-card" onSubmit={send}>
      <h3><FileSignature size={16}/> Send for signature</h3>
      <p className="wf-hint">Signers sign in this order. Each gets a personal link when it is their turn, and does not need an account. The document is fixed at version {contract.document_version}.</p>
      <ol className="sig-signers">{signers.map((s, i) => <li key={i}>
        <span className="wf-step-dot">{i + 1}</span>
        <input aria-label={`Signer ${i + 1} name`} placeholder="Full name" value={s.name} onChange={e => setSigners(signers.map((o, j) => j === i ? { ...o, name: e.target.value } : o))} />
        <input aria-label={`Signer ${i + 1} email`} placeholder="email@company.com" type="email" value={s.email} onChange={e => setSigners(signers.map((o, j) => j === i ? { ...o, email: e.target.value } : o))} />
        {signers.length > 1 && <button type="button" className="icon-button" aria-label={`Remove signer ${i + 1}`} onClick={() => setSigners(signers.filter((_, j) => j !== i))}><Trash2 size={15}/></button>}
      </li>)}</ol>
      {signers.length < 10 && <button type="button" className="secondary-button" onClick={() => setSigners([...signers, { name: "", email: "" }])}><Plus size={14}/> Add signer</button>}
      <label className="wf-field">Message to signers (optional)<textarea value={message} onChange={e => setMessage(e.target.value)} maxLength={1000} /></label>
      <div className="wf-actions"><button className="primary-button" disabled={busy}>Send for signature</button></div>
    </form>)}
    {requests?.length === 0 && !canSend && <p className="wf-hint">This contract has not been sent for signature.</p>}
    {requests?.map(request => <SignatureCard key={request.id} request={request} user={user} busy={busy}
      onNewLink={signerId => void run(() => api.newSigningLink(request.id, signerId))}
      onVoid={() => { const reason = window.prompt("Why are you voiding this signature request?") ?? undefined; if (reason !== undefined) void run(() => api.voidSignature(request.id, reason)); }} />)}
  </div>;
}

function CopyLink({ link }: { link: string }) {
  const [copied, setCopied] = useState(false);
  return <div className="sig-link">
    <input readOnly value={link} aria-label="Signing link" onFocus={e => e.target.select()} />
    <button type="button" className="secondary-button" onClick={() => { void navigator.clipboard?.writeText(link).then(() => setCopied(true)); }}>{copied ? <Check size={14}/> : <Copy size={14}/>}{copied ? "Copied" : "Copy"}</button>
  </div>;
}

function SignatureCard({ request, user, busy, onNewLink, onVoid }: {
  request: SignatureRequest; user: User; busy: boolean; onNewLink: (signerId: number) => void; onVoid: () => void;
}) {
  const open = request.status === "Sent";
  const canManage = open && can(user, "signature.send");
  return <article className="wf-card">
    <header className="wf-card-head"><div><h3>Signature request #{request.id}</h3>
      <small>Sent by {request.created_by_name} · {dateTimeLabel(request.created_at)} · document v{request.document_version}</small></div>
      <Status value={request.status} /></header>
    {request.message && <p className="wf-note">“{request.message}”</p>}
    {request.void_reason && <p className="wf-hint">Voided: {request.void_reason}</p>}
    {open && request.signers.some(s => s.link) && <p className="wf-hint">Signing links are shown only now. Emails are sent when SMTP is configured; otherwise share the links yourself.</p>}
    <ol className="wf-steps">{request.signers.map(s => <li key={s.id} className={`wf-step ${s.status === "Signed" ? "approved" : s.status === "Declined" ? "rejected" : s.status.toLowerCase()}`}>
      <span className="wf-step-dot">{s.status === "Signed" ? <Check size={13}/> : s.status === "Declined" ? <X size={13}/> : s.signer_order}</span>
      <div><strong>{s.name}</strong><small>{s.email} · {s.status === "Signed" ? `Signed ${dateTimeLabel(s.signed_at)}` : s.status === "Declined" ? `Declined ${dateTimeLabel(s.signed_at)}` : s.status === "Pending" ? (s.viewed_at ? `Opened ${dateTimeLabel(s.viewed_at)}, not signed yet` : "Waiting to sign") : "Signs after the previous signer"}</small>
        {s.decline_reason && <p>{s.decline_reason}</p>}
        {open && s.link && s.status !== "Signed" && <CopyLink link={s.link} />}
        {canManage && !s.link && s.status === "Pending" && <button className="link-button" disabled={busy} onClick={() => onNewLink(s.id)}><RotateCcw size={13}/> Resend with a new link</button>}
      </div>
    </li>)}</ol>
    <div className="wf-actions">
      {request.has_signed_copy && <a className="primary-button" href={signedCopyUrl(request.id)} target="_blank" rel="noreferrer"><Download size={14}/> Signed copy (PDF)</a>}
      {canManage && <button className="danger-button" disabled={busy} onClick={onVoid}>Void request</button>}
    </div>
    <small className="sig-hash">Document SHA-256: {request.document_sha256}</small>
  </article>;
}

// --- public signing page -------------------------------------------------------------

function SignaturePad({ onChange }: { onChange: (dataUrl: string | null) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const [empty, setEmpty] = useState(true);
  function point(event: PointerEvent<HTMLCanvasElement>) {
    const rect = canvas.current!.getBoundingClientRect();
    return [(event.clientX - rect.left) * (canvas.current!.width / rect.width), (event.clientY - rect.top) * (canvas.current!.height / rect.height)];
  }
  function down(event: PointerEvent<HTMLCanvasElement>) {
    drawing.current = true; canvas.current!.setPointerCapture(event.pointerId);
    const ctx = canvas.current!.getContext("2d")!; const [x, y] = point(event);
    ctx.lineWidth = 2.5; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#1e2a6b";
    ctx.beginPath(); ctx.moveTo(x, y);
  }
  function move(event: PointerEvent<HTMLCanvasElement>) {
    if (!drawing.current) return;
    const ctx = canvas.current!.getContext("2d")!; const [x, y] = point(event);
    ctx.lineTo(x, y); ctx.stroke(); setEmpty(false);
  }
  function up() {
    if (!drawing.current) return;
    drawing.current = false;
    if (!empty) onChange(canvas.current!.toDataURL("image/png"));
  }
  function clear() {
    canvas.current!.getContext("2d")!.clearRect(0, 0, canvas.current!.width, canvas.current!.height);
    setEmpty(true); onChange(null);
  }
  return <div className="sig-pad">
    <canvas ref={canvas} width={600} height={180} aria-label="Draw your signature" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={up} />
    <div><small>{empty ? "Optional: draw your signature with a mouse or finger." : "Signature drawn."}</small>{!empty && <button type="button" className="link-button" onClick={clear}>Clear</button>}</div>
  </div>;
}

export function SigningPage({ token }: { token: string }) {
  const [view, setView] = useState<SigningView | null>(null);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [consent, setConsent] = useState(false);
  const [image, setImage] = useState<string | null>(null);
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.signingView(token).then(v => { if (!cancelled) setView(v); }).catch(err => { if (!cancelled) setError(errorText(err, "This signing link does not work.")); });
    return () => { cancelled = true; };
  }, [token]);

  async function act(action: () => Promise<SigningView>) {
    setBusy(true); setError("");
    try { setView(await action()); } catch (err) { setError(errorText(err, "Something went wrong")); } finally { setBusy(false); }
  }

  if (!view) return <main className="sign-page"><div className="sign-card"><h1>Clause e-signature</h1>{error ? <p className="form-error">{error}</p> : <p>Loading…</p>}</div></main>;
  const done = view.signer.status === "Signed" || view.signer.status === "Declined" || view.request_status !== "Sent";
  return <main className="sign-page">
    <header className="sign-head"><div><small>Signature requested</small><h1>{view.contract_title}</h1><p>{view.vendor_name ? `${view.vendor_name} · ` : ""}{view.file_name}</p></div>
      <ol className="sign-order">{view.signers.map(s => <li key={s.signer_order} className={s.status.toLowerCase()}>{s.status === "Signed" ? <Check size={12}/> : s.signer_order} {s.name}</li>)}</ol></header>
    <div className="sign-layout">
      <iframe className="sign-doc" title="Document to sign" src={signingDocumentUrl(token)} />
      <aside className="sign-card">
        {view.message && <p className="wf-note">“{view.message}”</p>}
        {error && <p className="form-error">{error}</p>}
        {view.signer.status === "Signed" && <div className="sign-done"><Check size={28}/><h2>Thank you, {view.signer.name}</h2><p>You signed on {dateTimeLabel(view.signer.signed_at)}. {view.request_status === "Completed" ? "Everyone has signed; the sender has the signed copy." : "The next signer has been asked to sign."}</p></div>}
        {view.signer.status === "Declined" && <div className="sign-done declined"><X size={28}/><h2>You declined to sign</h2><p>The sender has been told your reason.</p></div>}
        {!done && view.signer.status === "Waiting" && <p className="wf-hint">It is not your turn yet. You will get a new link when the previous signers are done.</p>}
        {!done && view.request_status === "Sent" && view.signer.status === "Pending" && !declining && <form onSubmit={e => { e.preventDefault(); void act(() => api.sign(token, name, image)); }}>
          <h2>Sign as {view.signer.name}</h2>
          <p className="wf-hint">Read the document, then type your full name exactly as shown above.</p>
          <label className="wf-field">Full name<input value={name} onChange={e => setName(e.target.value)} autoComplete="name" placeholder={view.signer.name} /></label>
          {name.trim() && <p className="sign-preview">{name}</p>}
          <SignaturePad onChange={setImage} />
          <label className="sign-consent"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} /> I agree to sign this document electronically, and that my typed name is my signature.</label>
          <button className="primary-button" disabled={busy || !consent || !name.trim()}>{busy ? "Signing…" : "Sign document"}</button>
          <button type="button" className="link-button" onClick={() => setDeclining(true)}>Decline to sign</button>
        </form>}
        {declining && !done && <form onSubmit={e => { e.preventDefault(); void act(() => api.declineSigning(token, reason)); }}>
          <h2>Decline to sign</h2>
          <label className="wf-field">Reason (shared with the sender)<textarea value={reason} onChange={e => setReason(e.target.value)} maxLength={1000} /></label>
          <div className="wf-actions"><button type="button" className="secondary-button" onClick={() => setDeclining(false)}>Back</button><button className="danger-button" disabled={busy || !reason.trim()}>Decline</button></div>
        </form>}
        {view.request_status === "Voided" && <p className="wf-hint">The sender cancelled this signature request.</p>}
        <small className="sig-hash">Document fingerprint (SHA-256): {view.document_sha256}</small>
      </aside>
    </div>
  </main>;
}
