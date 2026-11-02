"use client";

import { FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import { CalendarDays, CheckCircle2, CircleAlert, ClipboardCheck, FileText, History, LayoutDashboard, RefreshCw, Search, Upload, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Table } from "@/components/ui/table";
import { API_BASE, api, type AuditItem, type Contract, type Dashboard } from "./api";

type View = "Overview" | "Contracts" | "Review queue" | "Renewals" | "Audit trail";
type PeriodMonths = 1 | 3 | 6 | 12;

const navItems: { name: View; label: string; icon: LucideIcon }[] = [
  { name: "Overview", label: "Overview", icon: LayoutDashboard }, { name: "Contracts", label: "Contract management", icon: FileText },
  { name: "Review queue", label: "Review queue", icon: ClipboardCheck }, { name: "Renewals", label: "Renewals", icon: RefreshCw },
  { name: "Audit trail", label: "Audit trail", icon: History },
];

const contractStatuses = ["Quoting", "Under Review", "Cleared to Start", "In Progress", "Completed", "On Hold", "Cancelled"] as const;

const emptyDashboard: Dashboard = { total: 0, period_total: 0, active: 0, under_review: 0, completed: 0, draft: 0, expired: 0, awaiting_validation: 0, missing_information: 0, expiring_30_days: 0, value_by_currency: [], total_value_by_currency: [], status_distribution: [] };

function Mark() {
  return <div className="brand-mark" aria-hidden="true"><span /><span /><span /></div>;
}

const vendorHues = ["indigo", "violet", "sky", "teal", "amber", "rose"] as const;
function vendorHue(name?: string | null) {
  if (!name) return "slate";
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return vendorHues[hash % vendorHues.length];
}

function money(contract: Contract) {
  if (contract.one_time_amount == null) return "—";
  return `${contract.currency ?? ""} ${contract.one_time_amount.toLocaleString()}`.trim();
}

function dateLabel(value?: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-SG", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`));
}

function tone(status: string) {
  if (["Cleared to Start", "In Progress", "Completed", "Validated"].includes(status)) return "green";
  if (status === "Under Review" || status === "Pending") return "amber";
  if (status === "Expired") return "red";
  return "slate";
}

function Status({ value, plain = false }: { value: string; plain?: boolean }) {
  // plain: a dot and text, for the business status; the filled pill is kept for validation so the two columns read differently.
  return <span className={`status ${tone(value)}${plain ? " plain" : ""}`}><i />{value}</span>;
}

function ContractTable({ contracts, onSelect }: { contracts: Contract[]; onSelect: (item: Contract) => void }) {
  // Hide the owner column until at least one contract has an owner, so an all-dash column does not take space.
  const showOwner = contracts.some(contract => contract.owner);
  return <div className="table-wrap"><Table><thead><tr><th>Vendor & project</th>{showOwner && <th>Owner</th>}<th>Contract value</th><th>Status</th><th>Expiry date</th><th>Validation</th><th /></tr></thead><tbody>
    {contracts.map((contract) => { const days = daysLeft(contract.project_end_date); const level = urgency(days); return <tr key={contract.id} onClick={() => onSelect(contract)} className={`click-row ${level}`}>
      <td><div className="vendor-cell"><span className={`vendor-logo hue-${vendorHue(contract.vendor_name)}`}>{(contract.vendor_name ?? "?").slice(0, 1)}</span><div><strong>{contract.vendor_name ?? "Vendor not identified"}</strong><small>{contract.project_name ?? contract.title}</small></div></div></td>
      {showOwner && <td>{contract.owner ?? "Unassigned"}</td>}<td className="nowrap"><strong>{money(contract)}</strong></td><td><Status value={contract.status} plain /></td><td className="nowrap">{dateLabel(contract.project_end_date)}{level && days !== null && <em className="due-in">{days < 0 ? `${-days}d overdue` : days === 0 ? "Today" : `in ${days}d`}</em>}</td><td><Status value={contract.validation_status} /></td><td><button className="row-menu" aria-label="Open contract">›</button></td>
    </tr>; })}
    {!contracts.length && <tr><td colSpan={showOwner ? 7 : 6} className="empty-cell">No contracts match these filters.</td></tr>}
  </tbody></Table></div>;
}

export default function ContractApp() {
  const [view, setView] = useState<View>("Overview");
  const [dashboard, setDashboard] = useState(emptyDashboard);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [selected, setSelected] = useState<Contract | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [periodMonths, setPeriodMonths] = useState<PeriodMonths>(12);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      setError("");
      const [dashboardData, contractData, auditData] = await Promise.all([api.dashboard(periodMonths), api.contracts(), api.audit()]);
      setDashboard(dashboardData); setContracts(contractData); setAudit(auditData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect to the local API.");
    } finally { setLoading(false); }
  }

  useEffect(() => {
    let cancelled = false;
    async function loadInitialData() {
      try {
        const [dashboardData, contractData, auditData] = await Promise.all([api.dashboard(periodMonths), api.contracts(), api.audit()]);
        if (cancelled) return;
        setDashboard(dashboardData); setContracts(contractData); setAudit(auditData);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not connect to the local API.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadInitialData();
    return () => { cancelled = true; };
  }, [periodMonths]);
  const reviewQueue = useMemo(() => contracts.filter(c => c.validation_status !== "Validated"), [contracts]);
  const renewals = useMemo(() => contracts.filter(c => c.project_end_date).sort((a, b) => (a.project_end_date ?? "").localeCompare(b.project_end_date ?? "")), [contracts]);
  const filtered = useMemo(() => contracts.filter(c => {
    const text = `${c.vendor_name} ${c.project_name} ${c.title} ${c.reference_number} ${c.owner}`.toLowerCase();
    return (!search || text.includes(search.toLowerCase())) && (!statusFilter || c.status === statusFilter);
  }), [contracts, search, statusFilter]);

  function navigate(next: View) { setView(next); setSelected(null); }

  return <main className="app-shell">
    <aside className="sidebar">
      <div className="brand"><Mark /><div><strong>Clause</strong><small>Contract intelligence</small></div></div>
      <nav aria-label="Main navigation">{navItems.map(item => { const Icon = item.icon; return <button onClick={() => navigate(item.name)} className={view === item.name ? "nav-item active" : "nav-item"} key={item.name}><Icon size={18}/>{item.label}{item.name === "Review queue" && <em>{reviewQueue.length}</em>}</button>; })}</nav>
      <div className="sidebar-foot"><div className="profile"><div className="avatar">MZ</div><div><strong>Maxwell Zhang</strong><small>Contract administrator</small></div><span>⋯</span></div></div>
    </aside>

    <section className="workspace">
      <header className="topbar">
        <div className="search"><Search size={18}/><input value={search} onChange={e => setSearch(e.target.value)} onFocus={() => setView("Contracts")} aria-label="Search contracts" placeholder="Search contracts, vendors, owners…" /><kbd>⌘ K</kbd></div>
        <div className="sync-status"><CheckCircle2 size={16}/> Synced just now</div><Button onClick={() => setUploadOpen(true)}><Upload size={16}/> Upload contract</Button>
      </header>
      <div className="content">
        {error && <div className="error-banner"><strong>Local API unavailable.</strong> {error} <button onClick={refresh}>Retry</button></div>}
        {loading ? <div className="loading-state"><span /><p>Loading your contract portfolio…</p></div> : <>
          {view === "Overview" && <Overview dashboard={dashboard} contracts={contracts} onSelect={setSelected} onNavigate={navigate} />}
          {view === "Contracts" && <ContractsView contracts={filtered} status={statusFilter} setStatus={setStatusFilter} search={search} setSearch={setSearch} periodMonths={periodMonths} setPeriodMonths={setPeriodMonths} onSelect={setSelected} />}
          {view === "Review queue" && <ReviewView contracts={reviewQueue} onSelect={setSelected} />}
          {view === "Renewals" && <RenewalView contracts={renewals} onSelect={setSelected} />}
          {view === "Audit trail" && <AuditView items={audit} />}
        </>}
      </div>
    </section>
    {uploadOpen && <UploadModal onClose={() => setUploadOpen(false)} onComplete={async contract => { setUploadOpen(false); await refresh(); setSelected(contract); }} />}
    {selected && <ReviewDrawer contract={selected} onClose={() => setSelected(null)} onSaved={async contract => { setSelected(contract); await refresh(); }} onDeleted={async () => { setSelected(null); await refresh(); }} />}
  </main>;
}

function Overview({ dashboard, contracts, onSelect, onNavigate }: { dashboard: Dashboard; contracts: Contract[]; onSelect: (c: Contract) => void; onNavigate: (v: View) => void }) {
  const today = new Date();
  const daysUntil = (value?: string | null) => value ? Math.ceil((new Date(`${value}T00:00:00`).getTime() - today.getTime()) / 86400000) : null;
  const active = contracts.filter(contract => contract.status === "In Progress").length;
  const needsValidation = contracts.filter(contract => contract.validation_status !== "Validated").length;
  const totalValue = dashboard.total_value_by_currency.length
    ? <span className="kpi-currencies">{[...dashboard.total_value_by_currency].sort((a, b) => (b.currency === HOME_CURRENCY ? 1 : 0) - (a.currency === HOME_CURRENCY ? 1 : 0) || b.value - a.value).map(item => <span key={item.currency}>{item.currency} {item.value.toLocaleString()}</span>)}</span>
    : "No value data";
  const expiring = contracts.filter(contract => { const days = daysUntil(contract.project_end_date); return days !== null && days >= 0 && days <= 30; });
  const actions = [
    ...expiring.map(contract => ({ contract, kind: "Expiry", copy: `Ends in ${daysUntil(contract.project_end_date)} days`, urgent: true })),
    ...contracts.filter(contract => contract.validation_status !== "Validated").map(contract => ({ contract, kind: "Review", copy: `${contract.missing_fields.length} missing field${contract.missing_fields.length === 1 ? "" : "s"}`, urgent: false })),
  ].slice(0, 6);
  const summary = [
    { text: needsValidation ? `${needsValidation} contract${needsValidation === 1 ? "" : "s"} to validate` : "All contracts validated", tone: needsValidation ? "amber" : "green" },
    { text: expiring.length ? `${expiring.length} expiring within 30 days` : "Nothing expiring in 30 days", tone: expiring.length ? "rose" : "green" },
    { text: `${active} active`, tone: active ? "green" : "slate" },
  ];
  const keyDates = contracts.flatMap(contract => [
    { contract, label: "Renewal", date: contract.renewal_date }, { contract, label: "Expiry", date: contract.project_end_date },
  ]).filter(item => { const days = daysUntil(item.date); return days !== null && days >= 0 && days <= 90; }).sort((a, b) => (a.date ?? "").localeCompare(b.date ?? "")).slice(0, 6);
  return <>
    <div className="overview-heading"><div><h1>Overview</h1><p className="overview-summary">{summary.map(part => <span key={part.text} className={`summary-chip ${part.tone}`}>{part.text}</span>)}</p></div></div>
    <section className="kpi-grid"><article className="hero-card"><div className="hero-top"><span className="hero-icon"><FileText size={20}/></span><span>Portfolio value</span></div><div className="hero-value">{totalValue}<small>Kept per currency, never converted.</small></div><footer><span>{dashboard.total} contract{dashboard.total === 1 ? "" : "s"}</span><button onClick={() => onNavigate("Contracts")}>Open contracts →</button></footer></article><Kpi tone="green" icon={CalendarDays} value={active} label="Active" emptyLabel="No active contracts" note={`of ${dashboard.total} contracts`} /><Kpi tone={needsValidation ? "amber" : "green"} icon={needsValidation ? ClipboardCheck : CheckCircle2} value={needsValidation} label="Needs validation" emptyLabel="All contracts validated" note={needsValidation ? "Waiting in the review queue" : "Review queue is clear"} /><Kpi tone={expiring.length ? "rose" : "green"} icon={expiring.length ? CircleAlert : CheckCircle2} value={expiring.length} label="Expiring in 30 days" emptyLabel="Nothing expiring" note="End dates in the next 30 days" /></section>
    <section className="overview-main"><article className="panel action-panel"><header><div><h2>Action list</h2><p>Prioritized work that needs a decision or review.</p></div>{actions.length > 0 && <button onClick={() => onNavigate("Review queue")}>Open queue</button>}</header>{actions.length ? <div className="action-list">{actions.map(({ contract, kind, copy, urgent }) => <button key={`${kind}-${contract.id}`} className={urgent ? "urgent" : undefined} onClick={() => onSelect(contract)}><span className={urgent ? "action-symbol urgent" : "action-symbol"}>{urgent ? <CircleAlert size={18}/> : <ClipboardCheck size={18}/>}</span><div><strong>{contract.title}</strong><small>{contract.vendor_name ?? "Vendor not identified"} · {kind}</small></div><span className={urgent ? "action-meta urgent" : contract.missing_fields.length ? "action-meta warn" : "action-meta"}>{copy}</span></button>)}</div> : <EmptyState icon={CheckCircle2} title="You’re all caught up" copy="There are no expiring contracts or pending reviews." />}</article>
      <article className="panel dates-panel"><header><div><h2>Upcoming key dates</h2><p>Renewals and expiries in the next 90 days.</p></div></header>{keyDates.length ? <div className="date-list">{keyDates.map(({ contract, label, date }) => { const days = daysUntil(date); return <button key={`${label}-${contract.id}`} className={`date-row ${urgency(days)}`} onClick={() => onSelect(contract)}><div className="date-when"><time>{dateLabel(date)}</time>{days !== null && <em className="due-in">{days === 0 ? "Today" : `in ${days} day${days === 1 ? "" : "s"}`}</em>}</div><div><strong>{contract.title}</strong><small>{label} · {contract.vendor_name ?? "Vendor not identified"}</small></div></button>; })}</div> : <EmptyState icon={CalendarDays} title="No key dates in 90 days" copy="Upcoming renewals and expiries will appear here." />}</article></section>
    <section className="panel recent-panel"><header><div><h2>Recent contracts</h2><p>The latest uploaded or updated contracts.</p></div></header><ContractTable contracts={contracts.slice(0, 5)} onSelect={onSelect} /></section>
  </>;
}

function Kpi({ icon: Icon, value, label, emptyLabel, note, zero, tone = "indigo" }: { icon: LucideIcon; value: ReactNode; label: string; emptyLabel?: string; note?: string; zero?: boolean; tone?: "indigo" | "green" | "amber" | "rose" }) {
  const isZero = zero ?? value === 0;
  return <Card className={`kpi tone-${tone}${isZero ? " zero" : ""}`}><header className="kpi-head"><span className="kpi-icon"><Icon size={20}/></span><p>{isZero && emptyLabel ? emptyLabel : label}</p></header><strong className="kpi-value">{value}</strong>{note && <small className="kpi-note">{note}</small>}</Card>;
}

// Whole-row alert for dates that are close: <=30 days (or past) urgent, <=60 soon.
// Headline currency on the portfolio card; other currencies follow by value.
const HOME_CURRENCY = "SGD";

function daysLeft(value?: string | null) {
  if (!value) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.ceil((new Date(`${value}T00:00:00`).getTime() - today.getTime()) / 86400000);
}

function urgency(days: number | null) {
  if (days === null) return "";
  return days <= 30 ? "urgent" : days <= 60 ? "soon" : "";
}

function EmptyState({ icon: Icon, title, copy }: { icon: LucideIcon; title: string; copy: string }) {
  return <div className="overview-empty"><Icon size={22}/><div><strong>{title}</strong><p>{copy}</p></div></div>;
}

function SectionHeader({ eyebrow, title, copy }: { eyebrow: string; title: string; copy: string }) {
  return <div className="section-heading"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{copy}</p></div>;
}

function ContractsView({ contracts, status, setStatus, search, setSearch, periodMonths, setPeriodMonths, onSelect }: { contracts: Contract[]; status: string; setStatus: (v: string) => void; search: string; setSearch: (v: string) => void; periodMonths: PeriodMonths; setPeriodMonths: (months: PeriodMonths) => void; onSelect: (c: Contract) => void }) {
  const start = new Date(Date.now() - ({ 1: 31, 3: 92, 6: 183, 12: 366 }[periodMonths] * 86400000));
  const scoped = contracts.filter(contract => { const value = contract.project_start_date ?? contract.upload_date; return value ? new Date(value) >= start : false; });
  return <><SectionHeader eyebrow="CONTRACT MANAGEMENT" title="Contracts" copy="Search and organize your contracts by status and start date."/><div className="filter-bar"><label><Search size={17}/><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search vendor, project, owner…" /></label><select value={periodMonths} onChange={e => setPeriodMonths(Number(e.target.value) as PeriodMonths)} aria-label="Contract start date"><option value={1}>Started in last month</option><option value={3}>Started in last quarter</option><option value={6}>Started in last 6 months</option><option value={12}>Started in last year</option></select><select value={status} onChange={e => setStatus(e.target.value)}><option value="">All statuses</option>{contractStatuses.map(item => <option key={item}>{item}</option>)}</select><a className="export-button" href={`${API_BASE}/api/export/contracts.csv?search=${encodeURIComponent(search)}&status=${encodeURIComponent(status)}`}>↓ Export CSV</a></div><section className="panel contracts-panel catalog"><div className="panel-title"><div><h2>{scoped.length} contracts</h2><p>Click any row to inspect and edit extracted fields.</p></div></div><ContractTable contracts={scoped} onSelect={onSelect}/></section></>;
}

function ReviewView({ contracts, onSelect }: { contracts: Contract[]; onSelect: (c: Contract) => void }) {
  return <><SectionHeader eyebrow="HUMAN IN THE LOOP" title="Review queue" copy="Validate AI-extracted fields before records become final."/><div className="review-grid">{contracts.map(contract => <button className="review-card" key={contract.id} onClick={() => onSelect(contract)}><div className="review-card-top"><span className="vendor-logo">{(contract.vendor_name ?? "?")[0]}</span><Status value={contract.validation_status}/></div><h3>{contract.title}</h3><p>{contract.vendor_name ?? "Vendor not identified"} · {contract.project_name ?? "Project not identified"}</p><div className="confidence"><div><span style={{width:`${(contract.extraction_confidence ?? 0) * 100}%`}}/></div><b>{Math.round((contract.extraction_confidence ?? 0) * 100)}% confidence</b></div><footer><span>{contract.missing_fields.length} missing</span><span>{contract.low_confidence_fields.length} low confidence</span><b>Review →</b></footer></button>)}{!contracts.length && <div className="empty-state"><span>✓</span><h3>Queue cleared</h3><p>Every contract has been validated.</p></div>}</div></>;
}

function RenewalView({ contracts, onSelect }: { contracts: Contract[]; onSelect: (c: Contract) => void }) {
  const now = new Date();
  return <><SectionHeader eyebrow="EXPIRY MONITORING" title="Renewals" copy="Prioritize contracts approaching their end dates."/><div className="renewal-list">{contracts.map(contract => { const days = Math.ceil((new Date(`${contract.project_end_date}T00:00:00`).getTime() - now.getTime()) / 86400000); return <button key={contract.id} onClick={() => onSelect(contract)} className={`renewal-row ${urgency(days)}`}><div className={`days ${days < 0 ? "overdue" : urgency(days) || "safe"}`}><strong>{Math.abs(days)}</strong><small>{days < 0 ? "days overdue" : "days left"}</small></div><div><h3>{contract.title}</h3><p>{contract.vendor_name} · {contract.owner ?? "No owner"}</p></div><div className="renewal-date"><small>End date</small><strong>{dateLabel(contract.project_end_date)}</strong></div><Status value={days < 0 ? "Expired" : contract.status}/><b>›</b></button>; })}</div></>;
}

function AuditDetails({ details }: { details?: string }) {
  if (!details) return null;
  const changes = details.split("; ").map(change => {
    const match = change.match(/^(.+?): (.+) → (.+)$/);
    return match ? { field: match[1], before: match[2], after: match[3] } : null;
  });
  if (changes.every(Boolean)) return <div className="audit-changes">{changes.map(change => <div className="audit-change" key={change!.field}><span>{change!.field}</span><div><del>{change!.before}</del><b>→</b><ins>{change!.after}</ins></div></div>)}</div>;
  return <p className="audit-note">{details}</p>;
}

function AuditView({ items }: { items: AuditItem[] }) {
  return <><SectionHeader eyebrow="ACCOUNTABILITY" title="Audit trail" copy="A chronological record of uploads, extraction, edits and validation."/><section className="audit-list">{items.map(item => <article className="audit-row" key={item.id}><span className="audit-dot"/><div className="audit-entry"><header><div><strong>{item.action}</strong>{item.contract_title && <p className="audit-contract">{[item.vendor_name, item.contract_title].filter(Boolean).join(" · ")}</p>}</div><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString("en-SG")}</time></header><AuditDetails details={item.details}/><footer><span className="audit-avatar">{item.actor.slice(0, 1)}</span><span>{item.actor}</span></footer></div></article>)}</section></>;
}

function UploadModal({ onClose, onComplete }: { onClose: () => void; onComplete: (c: Contract) => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null); const [vendor, setVendor] = useState(""); const [project, setProject] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function submit(event: FormEvent) { event.preventDefault(); if (!file) return; setBusy(true); setError(""); const form = new FormData(); form.append("file", file); form.append("related_vendor", vendor); form.append("related_project", project); form.append("uploaded_by", "Maxwell Zhang"); try { await onComplete(await api.upload(form)); } catch (err) { setError(err instanceof Error ? err.message : "Upload failed"); setBusy(false); } }
  return <div className="modal-backdrop" onMouseDown={onClose}><form className="upload-modal" onSubmit={submit} onMouseDown={e => e.stopPropagation()}><header><div><p className="eyebrow">NEW CONTRACT</p><h2>Upload for AI extraction</h2><p>Your file is stored locally, then securely sent to your organization’s Azure OpenAI deployment.</p></div><button type="button" onClick={onClose}>×</button></header><label className={`drop-zone ${file ? "has-file" : ""}`}><input type="file" accept=".pdf,.docx,.png,.jpg,.jpeg,.webp" onChange={e => setFile(e.target.files?.[0] ?? null)} required/><span>{file ? "✓" : "⇧"}</span><strong>{file ? file.name : "Choose a contract document"}</strong><small>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · Ready to extract` : "PDF, DOCX, PNG, JPG or WEBP · Maximum 25 MB"}</small></label><div className="form-grid"><label>Related vendor <input value={vendor} onChange={e => setVendor(e.target.value)} placeholder="Optional hint"/></label><label>Related project <input value={project} onChange={e => setProject(e.target.value)} placeholder="Optional hint"/></label></div>{error && <p className="form-error">{error}</p>}<footer><button type="button" className="secondary-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={!file || busy}>{busy ? <><span className="spinner"/>Extracting with GPT-5.4…</> : "Upload & extract"}</button></footer></form></div>;
}

const editableFields: { key: keyof Contract; label: string; type?: string; wide?: boolean }[] = [
  {key:"title",label:"Contract title",wide:true},{key:"reference_number",label:"Reference number"},{key:"vendor_name",label:"Vendor name"},{key:"project_name",label:"Project / scope",wide:true},{key:"phase",label:"Phase"},{key:"status",label:"Contract status"},{key:"project_start_date",label:"Start date",type:"date"},{key:"project_end_date",label:"End date",type:"date"},{key:"renewal_date",label:"Renewal date",type:"date"},{key:"currency",label:"Currency"},{key:"one_time_amount",label:"One-time amount",type:"number"},{key:"optional_amount",label:"Optional amount",type:"number"},{key:"vendor_contact",label:"Vendor contact"},{key:"internal_contact",label:"Internal contact"},{key:"department",label:"Department"},{key:"owner",label:"Contract owner"},{key:"deliverables",label:"Deliverables / services",wide:true},{key:"payment_terms",label:"Payment terms",wide:true},{key:"notes",label:"Notes",wide:true},
];

function ReviewDrawer({ contract, onClose, onSaved, onDeleted }: { contract: Contract; onClose: () => void; onSaved: (c: Contract) => Promise<void>; onDeleted: () => Promise<void> }) {
  const [form, setForm] = useState<Contract>(contract); const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  const fileExtension = form.file_name?.split(".").pop()?.toLowerCase();
  async function save(validate = false) { setBusy(true); setMessage(""); try { let result = await api.update(contract.id, form); if (validate) result = await api.validate(contract.id); setForm(result); await onSaved(result); setMessage(validate ? "Contract validated." : "Changes saved."); } catch (err) { setMessage(err instanceof Error ? err.message : "Could not save"); } finally { setBusy(false); } }
  async function remove() { if (!window.confirm(`Delete “${form.title}”? The contract record will be removed, while its original uploaded file stays on this device.`)) return; setBusy(true); setMessage(""); try { await api.remove(contract.id); await onDeleted(); } catch (err) { setMessage(err instanceof Error ? err.message : "Could not delete contract"); setBusy(false); } }
  return <div className="drawer-backdrop"><section className="review-drawer"><header><div><p className="eyebrow">CONTRACT REVIEW</p><h2>{form.vendor_name ?? "Unidentified vendor"}</h2><p>{form.file_name ?? form.title}</p></div><button onClick={onClose}>×</button></header><div className="review-layout"><div className="document-pane">{form.file_name ? (fileExtension === "pdf" || form.file_type === "application/pdf" ? <iframe title="Uploaded contract" src={`${API_BASE}/api/contracts/${form.id}/file`}/> : fileExtension === "docx" ? <iframe title="DOCX preview" src={`${API_BASE}/api/contracts/${form.id}/preview`}/> : <div className="file-preview"><span>▤</span><h3>{form.file_name}</h3><p>Preview is available for PDFs and DOCX files. Open the original document in a new tab.</p><a href={`${API_BASE}/api/contracts/${form.id}/file`} target="_blank">Open document ↗</a></div>) : <div className="file-preview"><span>◇</span><h3>Demonstration record</h3><p>This seeded record does not have an uploaded source document.</p></div>}</div><div className="fields-pane"><div className="extraction-summary"><div><span>✦</span><div><strong>AI extraction</strong><p>{form.extraction_note ?? "Review each field before validation."}</p></div></div><b>{Math.round((form.extraction_confidence ?? 0) * 100)}%</b></div><div className="edit-grid">{editableFields.map(field => { const score = form.field_confidences?.[field.key as string]; const low = form.low_confidence_fields?.includes(field.key as string); const missing = form.missing_fields?.includes(field.key as string); const value = form[field.key]; const isLong = ["deliverables","payment_terms","notes"].includes(field.key); return <label key={field.key} className={field.wide ? "wide" : ""}><span>{field.label}{missing ? <em className="field-flag missing">Missing</em> : low ? <em className="field-flag low">Low {score ? `${Math.round(score*100)}%` : "confidence"}</em> : score ? <em className="field-flag good">{Math.round(score*100)}%</em> : null}</span>{field.key === "status" ? <select value={String(value ?? "Quoting")} onChange={e => setForm({...form,status:e.target.value})}>{contractStatuses.map(item => <option key={item}>{item}</option>)}</select> : isLong ? <textarea value={(value as string) ?? ""} onChange={e => setForm({...form,[field.key]:e.target.value})}/> : <input type={field.type ?? "text"} value={(value as string | number) ?? ""} onChange={e => setForm({...form,[field.key]:field.type === "number" ? (e.target.value ? Number(e.target.value) : null) : e.target.value})}/>}</label>; })}</div></div></div><footer><button className="danger-button" onClick={remove} disabled={busy}>Delete contract</button><div>{message && <span className="save-message">{message}</span>}</div><button className="secondary-button" onClick={() => save(false)} disabled={busy}>Update</button><button className="primary-button" onClick={() => save(true)} disabled={busy || form.validation_status === "Validated"}>{form.validation_status === "Validated" ? "✓ Validated" : "Confirm & validate"}</button></footer></section></div>;
}
