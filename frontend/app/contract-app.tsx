"use client";

import { FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import { BarChart3, BookCheck, Building2, FilePlus2, FileStack, CalendarDays, CheckCircle2, SlidersHorizontal, CircleAlert, ClipboardCheck, FileText, History, LayoutDashboard, ListChecks, RefreshCw, Search, ShieldCheck, Sparkles, Stamp, Upload, Users, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table } from "@/components/ui/table";
import { AuthError, api, can, contractStatuses, fileUrl, previewUrl, type AuditItem, type Contract, type Dashboard, type InboxItem, type User } from "./api";
import { ApprovalsPanel, CommentsPanel, HistoryPanel, ObligationsPanel, VersionsPanel } from "./contract-panels";
import { AuditDetails, EmptyState, SectionHeader, Status, dateLabel, errorText } from "./shared";
import { AdvancedFilters, TagEditor, Tags, activeFilterCount, emptyFilters, exportUrl, filterContracts, type Filters } from "./filters";
import { ReportsView, VendorsView } from "./insights";
import { AiPanel, AiSearch, PortfolioView } from "./ai-panel";
import { NewContractModal, TemplatesView } from "./templates";
import { SignaturesPanel } from "./signatures";
import { SharingCard } from "./access";
import { PlaybookView } from "./playbook";
import { FamilyCard } from "./family";
import { SpendPanel } from "./invoices";
import { AccountMenu, ApprovalsView, LoginScreen, NotificationsMenu, ObligationsView, UsersView } from "./views";
import { initLocale, intlLocale, t, tn, useLocale } from "./i18n";

type View = "Overview" | "Contracts" | "AI assistant" | "Approvals" | "Review queue" | "Obligations" | "Renewals" | "Vendors" | "Reports" | "Playbook" | "Templates" | "Audit trail" | "Users";
type PeriodMonths = 0 | 1 | 3 | 6 | 12; // 0 = any start date

const navItems: { name: View; label: string; icon: LucideIcon; visible?: (user: User) => boolean }[] = [
  { name: "Overview", label: "Overview", icon: LayoutDashboard }, { name: "Contracts", label: "Contract management", icon: FileText },
  { name: "AI assistant", label: "AI assistant", icon: Sparkles, visible: user => can(user, "ai.use") },
  { name: "Approvals", label: "Approvals", icon: Stamp }, { name: "Review queue", label: "Review queue", icon: ClipboardCheck },
  { name: "Obligations", label: "Obligations", icon: ListChecks }, { name: "Renewals", label: "Renewals", icon: RefreshCw },
  { name: "Vendors", label: "Vendors", icon: Building2 }, { name: "Reports", label: "Reports", icon: BarChart3 },
  { name: "Playbook", label: "Clause playbook", icon: BookCheck, visible: user => can(user, "ai.use") || can(user, "playbook.manage") },
  { name: "Templates", label: "Templates", icon: FileStack, visible: user => can(user, "template.manage") },
  { name: "Audit trail", label: "Audit trail", icon: History, visible: user => can(user, "audit.read") },
  { name: "Users", label: "Users & roles", icon: Users, visible: user => can(user, "user.manage") },
];

const emptyDashboard: Dashboard = { total: 0, pending_approvals: 0, obligations_overdue: 0, obligations_due_30_days: 0, active: 0, under_review: 0, completed: 0, draft: 0, expired: 0, awaiting_validation: 0, missing_information: 0, expiring_30_days: 0, value_by_currency: [], total_value_by_currency: [], status_distribution: [] };

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
  return `${contract.currency ?? ""} ${contract.one_time_amount.toLocaleString(intlLocale())}`.trim();
}

function dueIn(days: number) {
  return days < 0 ? t("{n}d overdue", { n: -days }) : days === 0 ? t("Today") : t("in {n}d", { n: days });
}

function ContractTable({ contracts, onSelect }: { contracts: Contract[]; onSelect: (item: Contract) => void }) {
  // Hide the owner column until at least one contract has an owner, so an all-dash column does not take space.
  const showOwner = contracts.some(contract => contract.owner);
  return <div className="table-wrap"><Table><thead><tr><th>{t("Vendor & project")}</th>{showOwner && <th>{t("Owner")}</th>}<th>{t("Contract value")}</th><th>{t("Status")}</th><th>{t("Expiry date")}</th><th>{t("Validation")}</th><th /></tr></thead><tbody>
    {contracts.map((contract) => { const days = daysLeft(contract.project_end_date); const level = urgency(days); return <tr key={contract.id} onClick={() => onSelect(contract)} className={`click-row ${level}`}>
      <td><div className="vendor-cell"><span className={`vendor-logo hue-${vendorHue(contract.vendor_name)}`}>{(contract.vendor_name ?? "?").slice(0, 1)}</span><div><strong>{contract.vendor_name ?? t("Vendor not identified")}</strong><small>{contract.project_name ?? contract.title}</small>{(contract.relationship || !!contract.child_count) && <span className="family-mark">{contract.relationship ? `↳ ${t(contract.relationship)}` : t("Main agreement · {n} linked", { n: contract.child_count })}</span>}<Tags tags={contract.tags} /></div></div></td>
      {showOwner && <td>{contract.owner ?? t("Unassigned")}</td>}<td className="nowrap"><strong>{money(contract)}</strong></td><td><Status value={contract.status} plain /></td><td className="nowrap">{dateLabel(contract.project_end_date)}{level && days !== null && <em className="due-in">{dueIn(days)}</em>}</td><td><Status value={contract.validation_status} /></td><td><button className="row-menu" aria-label={t("Open contract")}>›</button></td>
    </tr>; })}
    {!contracts.length && <tr><td colSpan={showOwner ? 7 : 6} className="empty-cell">{t("No contracts match these filters.")}</td></tr>}
  </tbody></Table></div>;
}

export default function ContractApp() {
  useLocale(); // re-render everything when the language changes
  const [user, setUser] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    initLocale();
    let cancelled = false;
    api.me().then(me => { if (!cancelled) setUser(me); }).catch(() => undefined).finally(() => { if (!cancelled) setChecked(true); });
    return () => { cancelled = true; };
  }, []);
  if (!checked) return <div className="loading-state"><span /><p>{t("Loading…")}</p></div>;
  if (!user) return <LoginScreen onSignedIn={setUser} />;
  return <Workspace key={user.id} user={user} onSignedOut={() => setUser(null)} />;
}

type Loaded = { dashboard: Dashboard; contracts: Contract[]; audit: AuditItem[]; inbox: InboxItem[] };

function loadAll(user: User, months: PeriodMonths): Promise<Loaded> {
  return Promise.all([
    api.dashboard(months || 12), api.contracts("?limit=1000"), can(user, "audit.read") ? api.audit() : Promise.resolve([]), api.inbox(),
  ]).then(([dashboard, contracts, audit, inbox]) => ({ dashboard, contracts, audit, inbox }));
}

function Workspace({ user, onSignedOut }: { user: User; onSignedOut: () => void }) {
  const [view, setView] = useState<View>("Overview");
  const [dashboard, setDashboard] = useState(emptyDashboard);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [inbox, setInbox] = useState<InboxItem[]>([]);
  const [selected, setSelected] = useState<Contract | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [periodMonths, setPeriodMonths] = useState<PeriodMonths>(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  function apply(data: Loaded) {
    setDashboard(data.dashboard); setContracts(data.contracts); setAudit(data.audit); setInbox(data.inbox);
  }
  function fail(err: unknown) {
    if (err instanceof AuthError) { onSignedOut(); return; }
    setError(errorText(err, "Could not connect to the local API."));
  }

  async function refresh() {
    try { setError(""); apply(await loadAll(user, periodMonths)); setRefreshKey(k => k + 1); }
    catch (err) { fail(err); }
    finally { setLoading(false); }
  }

  useEffect(() => {
    let cancelled = false;
    loadAll(user, periodMonths)
      .then(data => { if (!cancelled) apply(data); })
      .catch(err => { if (!cancelled) fail(err); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [periodMonths, user]);

  // Links from emails, Teams and calendars open a contract: /?contract=<id>
  useEffect(() => {
    const id = Number(new URLSearchParams(window.location.search).get("contract"));
    if (!id) return;
    window.history.replaceState(null, "", window.location.pathname);
    api.contract(id).then(c => setSelected(c)).catch(err => fail(err));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reviewQueue = useMemo(() => contracts.filter(c => c.validation_status !== "Validated"), [contracts]);
  const renewals = useMemo(() => contracts.filter(c => c.project_end_date && !["Completed", "Cancelled"].includes(c.status)).sort((a, b) => (a.project_end_date ?? "").localeCompare(b.project_end_date ?? "")), [contracts]);
  const filtered = useMemo(() => filterContracts(contracts, search, filters), [contracts, search, filters]);
  const allTags = useMemo(() => [...new Set(contracts.flatMap(c => c.tags ?? []))].sort(), [contracts]);
  const visibleNav = navItems.filter(item => !item.visible || item.visible(user));

  function navigate(next: View) { setView(next); setSelected(null); }
  async function openContract(id: number) {
    try { setSelected(contracts.find(c => c.id === id) ?? await api.contract(id)); }
    catch (err) { fail(err); }
  }
  async function signOut() { await api.logout().catch(() => undefined); onSignedOut(); }
  const badge = (name: View) => name === "Review queue" ? reviewQueue.length : name === "Approvals" ? inbox.length : 0;

  return <main className="app-shell">
    <aside className="sidebar">
      <div className="brand"><Mark /><div><strong>Clause</strong><small>{t("Contract intelligence")}</small></div></div>
      <nav aria-label={t("Main navigation")}>{visibleNav.map(item => { const Icon = item.icon; const count = badge(item.name); return <button onClick={() => navigate(item.name)} className={view === item.name ? "nav-item active" : "nav-item"} key={item.name}><Icon size={18}/>{t(item.label)}{count > 0 && <em>{count}</em>}</button>; })}</nav>
      <div className="sidebar-foot"><AccountMenu user={user} onSignOut={signOut} /></div>
    </aside>

    <section className="workspace">
      <header className="topbar">
        <div className="search"><Search size={18}/><input value={search} onChange={e => setSearch(e.target.value)} onFocus={() => setView("Contracts")} aria-label={t("Search contracts")} placeholder={t("Search contracts, vendors, owners…")} /><kbd>⌘ K</kbd></div>
        <div className="sync-status"><ShieldCheck size={16}/> {t("Signed in as {name}", { name: user.name })}</div>
        <NotificationsMenu refreshKey={refreshKey} onOpenContract={id => void openContract(id)} />
        {can(user, "contract.create") && <><button className="secondary-button wf-new-button" onClick={() => setCreateOpen(true)}><FilePlus2 size={16}/> {t("New")}</button><Button onClick={() => setUploadOpen(true)}><Upload size={16}/> {t("Upload contract")}</Button></>}
      </header>
      <div className="content">
        {error && <div className="error-banner"><strong>{t("Local API unavailable.")}</strong> {error} <button onClick={refresh}>{t("Retry")}</button></div>}
        {loading ? <div className="loading-state"><span /><p>{t("Loading your contract portfolio…")}</p></div> : <>
          {view === "Overview" && <Overview dashboard={dashboard} contracts={contracts} inbox={inbox} onSelect={setSelected} onNavigate={navigate} />}
          {view === "Contracts" && <ContractsView user={user} contracts={filtered} allContracts={contracts} filters={filters} setFilters={setFilters} search={search} setSearch={setSearch} periodMonths={periodMonths} setPeriodMonths={setPeriodMonths} onSelect={setSelected} />}
          {view === "AI assistant" && <PortfolioView onOpen={id => void openContract(id)} />}
          {view === "Approvals" && <ApprovalsView items={inbox} onOpen={id => void openContract(id)} />}
          {view === "Review queue" && <ReviewView contracts={reviewQueue} onSelect={setSelected} />}
          {view === "Obligations" && <ObligationsView key={refreshKey} user={user} onOpen={id => void openContract(id)} />}
          {view === "Renewals" && <RenewalView contracts={renewals} onSelect={setSelected} />}
          {view === "Vendors" && <VendorsView onOpenVendor={vendor => { setFilters({ ...emptyFilters, vendor }); setSearch(""); setView("Contracts"); }} />}
          {view === "Reports" && <ReportsView onOpen={id => void openContract(id)} />}
          {view === "Playbook" && <PlaybookView user={user} />}
          {view === "Templates" && <TemplatesView />}
          {view === "Audit trail" && <AuditView items={audit} />}
          {view === "Users" && <UsersView user={user} />}
        </>}
      </div>
    </section>
    {createOpen && <NewContractModal onClose={() => setCreateOpen(false)} onCreated={async contract => { setCreateOpen(false); await refresh(); setSelected(contract); }} />}
    {uploadOpen && <UploadModal onClose={() => setUploadOpen(false)} onComplete={async contract => { setUploadOpen(false); await refresh(); setSelected(contract); }} />}
    {selected && <ContractDrawer key={selected.id} contract={selected} user={user} allTags={allTags} contracts={contracts} onOpenContract={id => void openContract(id)} onClose={() => setSelected(null)} onSaved={async contract => { setSelected(contract); await refresh(); }} onDeleted={async () => { setSelected(null); await refresh(); }} />}
  </main>;
}

function Overview({ dashboard, contracts, inbox, onSelect, onNavigate }: { dashboard: Dashboard; contracts: Contract[]; inbox: InboxItem[]; onSelect: (c: Contract) => void; onNavigate: (v: View) => void }) {
  const today = new Date();
  const daysUntil = (value?: string | null) => value ? Math.ceil((new Date(`${value}T00:00:00`).getTime() - today.getTime()) / 86400000) : null;
  const active = contracts.filter(contract => contract.status === "In Progress").length;
  const needsValidation = contracts.filter(contract => contract.validation_status !== "Validated").length;
  const totalValue = dashboard.total_value_by_currency.length
    ? <span className="kpi-currencies">{[...dashboard.total_value_by_currency].sort((a, b) => (b.currency === HOME_CURRENCY ? 1 : 0) - (a.currency === HOME_CURRENCY ? 1 : 0) || b.value - a.value).map(item => <span key={item.currency}>{item.currency} {item.value.toLocaleString(intlLocale())}</span>)}</span>
    : t("No value data");
  const expiring = contracts.filter(contract => { const days = daysUntil(contract.project_end_date); return days !== null && days >= 0 && days <= 30; });
  const approvalActions = inbox.flatMap(item => { const contract = contracts.find(c => c.id === item.contract_id); return contract ? [{ contract, kind: t("Approval"), copy: t("{role} sign-off", { role: t(item.role.charAt(0).toUpperCase() + item.role.slice(1)) }), urgent: true }] : []; });
  const actions = [
    ...approvalActions,
    ...expiring.map(contract => ({ contract, kind: t("Expiry"), copy: tn(daysUntil(contract.project_end_date) ?? 0, "Ends in {n} day", "Ends in {n} days"), urgent: true })),
    ...contracts.filter(contract => contract.validation_status !== "Validated").map(contract => ({ contract, kind: t("Review"), copy: tn(contract.missing_fields.length, "{n} missing field", "{n} missing fields"), urgent: false })),
  ].slice(0, 6);
  const summary = [
    { text: needsValidation ? tn(needsValidation, "{n} contract to validate", "{n} contracts to validate") : t("All contracts validated"), tone: needsValidation ? "amber" : "green" },
    { text: expiring.length ? t("{n} expiring within 30 days", { n: expiring.length }) : t("Nothing expiring in 30 days"), tone: expiring.length ? "rose" : "green" },
    { text: t("{n} active", { n: active }), tone: active ? "green" : "slate" },
    ...(inbox.length ? [{ text: tn(inbox.length, "{n} approval waiting for you", "{n} approvals waiting for you"), tone: "amber" }] : []),
    ...(dashboard.over_budget ? [{ text: t("{n} over budget", { n: dashboard.over_budget }), tone: "rose" }] : []),
    ...(dashboard.obligations_overdue ? [{ text: tn(dashboard.obligations_overdue, "{n} overdue obligation", "{n} overdue obligations"), tone: "rose" }] : []),
  ];
  const keyDates = contracts.flatMap(contract => [
    { contract, label: t("Renewal"), date: contract.renewal_date }, { contract, label: t("Expiry"), date: contract.project_end_date },
  ]).filter(item => { const days = daysUntil(item.date); return days !== null && days >= 0 && days <= 90; }).sort((a, b) => (a.date ?? "").localeCompare(b.date ?? "")).slice(0, 6);
  return <>
    <div className="overview-heading"><div><h1>{t("Overview")}</h1><p className="overview-summary">{summary.map(part => <span key={part.text} className={`summary-chip ${part.tone}`}>{part.text}</span>)}</p></div></div>
    <section className="kpi-grid"><article className="hero-card"><div className="hero-top"><span className="hero-icon"><FileText size={20}/></span><span>{t("Portfolio value")}</span></div><div className="hero-value">{totalValue}<small>{t("Kept per currency, never converted.")}</small></div><footer><span>{tn(dashboard.total, "{n} contract", "{n} contracts")}</span><button onClick={() => onNavigate("Contracts")}>{t("Open contracts →")}</button></footer></article><Kpi tone="green" icon={CalendarDays} value={active} label={t("Active")} emptyLabel={t("No active contracts")} note={t("of {n} contracts", { n: dashboard.total })} /><Kpi tone={needsValidation ? "amber" : "green"} icon={needsValidation ? ClipboardCheck : CheckCircle2} value={needsValidation} label={t("Needs validation")} emptyLabel={t("All contracts validated")} note={needsValidation ? t("Waiting in the review queue") : t("Review queue is clear")} /><Kpi tone={expiring.length ? "rose" : "green"} icon={expiring.length ? CircleAlert : CheckCircle2} value={expiring.length} label={t("Expiring in 30 days")} emptyLabel={t("Nothing expiring")} note={t("End dates in the next 30 days")} /></section>
    <section className="overview-main"><article className="panel action-panel"><header><div><h2>{t("Action list")}</h2><p>{t("Prioritized work that needs a decision or review.")}</p></div>{actions.length > 0 && <button onClick={() => onNavigate("Review queue")}>{t("Open queue")}</button>}</header>{actions.length ? <div className="action-list">{actions.map(({ contract, kind, copy, urgent }) => <button key={`${kind}-${contract.id}`} className={urgent ? "urgent" : undefined} onClick={() => onSelect(contract)}><span className={urgent ? "action-symbol urgent" : "action-symbol"}>{urgent ? <CircleAlert size={18}/> : <ClipboardCheck size={18}/>}</span><div><strong>{contract.title}</strong><small>{contract.vendor_name ?? t("Vendor not identified")} · {kind}</small></div><span className={urgent ? "action-meta urgent" : contract.missing_fields.length ? "action-meta warn" : "action-meta"}>{copy}</span></button>)}</div> : <EmptyState icon={CheckCircle2} title={t("You’re all caught up")} copy={t("There are no expiring contracts or pending reviews.")} />}</article>
      <article className="panel dates-panel"><header><div><h2>{t("Upcoming key dates")}</h2><p>{t("Renewals and expiries in the next 90 days.")}</p></div></header>{keyDates.length ? <div className="date-list">{keyDates.map(({ contract, label, date }) => { const days = daysUntil(date); return <button key={`${label}-${contract.id}`} className={`date-row ${urgency(days)}`} onClick={() => onSelect(contract)}><div className="date-when"><time>{dateLabel(date)}</time>{days !== null && <em className="due-in">{days === 0 ? t("Today") : tn(days, "in {n} day", "in {n} days")}</em>}</div><div><strong>{contract.title}</strong><small>{label} · {contract.vendor_name ?? t("Vendor not identified")}</small></div></button>; })}</div> : <EmptyState icon={CalendarDays} title={t("No key dates in 90 days")} copy={t("Upcoming renewals and expiries will appear here.")} />}</article></section>
    <section className="panel recent-panel"><header><div><h2>{t("Recent contracts")}</h2><p>{t("The latest uploaded or updated contracts.")}</p></div></header><ContractTable contracts={contracts.slice(0, 5)} onSelect={onSelect} /></section>
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


function ContractsView({ user, contracts, allContracts, filters, setFilters, search, setSearch, periodMonths, setPeriodMonths, onSelect }: { user: User; contracts: Contract[]; allContracts: Contract[]; filters: Filters; setFilters: (f: Filters) => void; search: string; setSearch: (v: string) => void; periodMonths: PeriodMonths; setPeriodMonths: (months: PeriodMonths) => void; onSelect: (c: Contract) => void }) {
  const [now] = useState(() => Date.now());
  const [showFilters, setShowFilters] = useState(() => activeFilterCount(filters) > 0);
  const [aiNote, setAiNote] = useState("");
  const start = periodMonths ? new Date(now - ({ 1: 31, 3: 92, 6: 183, 12: 366 }[periodMonths] * 86400000)) : null;
  const scoped = start ? contracts.filter(contract => { const value = contract.project_start_date ?? contract.upload_date; return value ? new Date(value) >= start : false; }) : contracts;
  const count = activeFilterCount(filters);
  return <><SectionHeader eyebrow={t("CONTRACT MANAGEMENT")} title={t("Contracts")} copy={t("Search, filter and organize your contracts.")}/>
    {can(user, "ai.use") && <AiSearch onApply={(next, text, explanation) => { setFilters(next); setSearch(text); setShowFilters(true); setAiNote(explanation); }} />}
    {aiNote && <p className="wf-hint ai-note"><b>AI:</b> {aiNote} <button className="wf-link" onClick={() => { setFilters(emptyFilters); setSearch(""); setAiNote(""); }}>{t("Clear")}</button></p>}
    <div className="filter-bar"><label><Search size={17}/><input value={search} onChange={e => setSearch(e.target.value)} placeholder={t("Search vendor, project, owner, tag…")} /></label>
      <select value={periodMonths} onChange={e => setPeriodMonths(Number(e.target.value) as PeriodMonths)} aria-label={t("Contract start date")}><option value={0}>{t("Any start date")}</option><option value={1}>{t("Started in last month")}</option><option value={3}>{t("Started in last quarter")}</option><option value={6}>{t("Started in last 6 months")}</option><option value={12}>{t("Started in last year")}</option></select>
      <select value={filters.status} onChange={e => setFilters({ ...filters, status: e.target.value })} aria-label={t("Status")}><option value="">{t("All statuses")}</option>{contractStatuses.map(item => <option key={item} value={item}>{t(item)}</option>)}</select>
      <button type="button" className={`secondary-button wf-filter-toggle${count ? " on" : ""}`} aria-expanded={showFilters} onClick={() => setShowFilters(!showFilters)}><SlidersHorizontal size={16}/> {t("Filters")}{count ? ` (${count})` : ""}</button>
      <a className="export-button" href={exportUrl(search, filters)}>↓ {t("Export CSV")}</a></div>
    {showFilters && <AdvancedFilters contracts={allContracts} filters={filters} setFilters={setFilters} />}
    <section className="panel contracts-panel catalog"><div className="panel-title"><div><h2>{tn(scoped.length, "{n} contract", "{n} contracts")}</h2><p>{t("Click any row to inspect and edit extracted fields.")}</p></div></div><ContractTable contracts={scoped} onSelect={onSelect}/></section></>;
}

function ReviewView({ contracts, onSelect }: { contracts: Contract[]; onSelect: (c: Contract) => void }) {
  return <><SectionHeader eyebrow={t("HUMAN IN THE LOOP")} title={t("Review queue")} copy={t("Validate AI-extracted fields before records become final.")}/><div className="review-grid">{contracts.map(contract => <button className="review-card" key={contract.id} onClick={() => onSelect(contract)}><div className="review-card-top"><span className="vendor-logo">{(contract.vendor_name ?? "?")[0]}</span><Status value={contract.validation_status}/></div><h3>{contract.title}</h3><p>{contract.vendor_name ?? t("Vendor not identified")} · {contract.project_name ?? t("Project not identified")}</p><div className="confidence"><div><span style={{width:`${(contract.extraction_confidence ?? 0) * 100}%`}}/></div><b>{t("{n}% confidence", { n: Math.round((contract.extraction_confidence ?? 0) * 100) })}</b></div><footer><span>{t("{n} missing", { n: contract.missing_fields.length })}</span><span>{t("{n} low confidence", { n: contract.low_confidence_fields.length })}</span><b>{t("Review →")}</b></footer></button>)}{!contracts.length && <div className="empty-state"><span>✓</span><h3>{t("Queue cleared")}</h3><p>{t("Every contract has been validated.")}</p></div>}</div></>;
}

function RenewalView({ contracts, onSelect }: { contracts: Contract[]; onSelect: (c: Contract) => void }) {
  const now = new Date();
  return <><SectionHeader eyebrow={t("EXPIRY MONITORING")} title={t("Renewals")} copy={t("Prioritize contracts approaching their end dates.")}/><div className="renewal-list">{contracts.map(contract => { const days = Math.ceil((new Date(`${contract.project_end_date}T00:00:00`).getTime() - now.getTime()) / 86400000); return <button key={contract.id} onClick={() => onSelect(contract)} className={`renewal-row ${urgency(days)}`}><div className={`days ${days < 0 ? "overdue" : urgency(days) || "safe"}`}><strong>{Math.abs(days)}</strong><small>{days < 0 ? t("days overdue") : t("days left")}</small></div><div><h3>{contract.title}</h3><p>{contract.vendor_name} · {contract.owner ?? t("No owner")}</p></div><div className="renewal-date"><small>{t("End date")}</small><strong>{dateLabel(contract.project_end_date)}</strong></div><Status value={days < 0 ? "Expired" : contract.status}/><b>›</b></button>; })}</div></>;
}


function AuditView({ items }: { items: AuditItem[] }) {
  return <><SectionHeader eyebrow={t("ACCOUNTABILITY")} title={t("Audit trail")} copy={t("A chronological record of uploads, extraction, edits and validation.")}/><section className="audit-list">{items.map(item => <article className="audit-row" key={item.id}><span className="audit-dot"/><div className="audit-entry"><header><div><strong>{t(item.action)}</strong>{item.contract_title && <p className="audit-contract">{[item.vendor_name, item.contract_title].filter(Boolean).join(" · ")}</p>}</div><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString(intlLocale())}</time></header><AuditDetails details={item.details}/><footer><span className="audit-avatar">{item.actor.slice(0, 1)}</span><span>{item.actor}</span></footer></div></article>)}</section></>;
}

function UploadModal({ onClose, onComplete }: { onClose: () => void; onComplete: (c: Contract) => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null); const [vendor, setVendor] = useState(""); const [project, setProject] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function submit(event: FormEvent) { event.preventDefault(); if (!file) return; setBusy(true); setError(""); const form = new FormData(); form.append("file", file); form.append("related_vendor", vendor); form.append("related_project", project); try { await onComplete(await api.upload(form)); } catch (err) { setError(err instanceof Error ? err.message : t("Upload failed")); setBusy(false); } }
  return <div className="modal-backdrop" onMouseDown={onClose}><form className="upload-modal" onSubmit={submit} onMouseDown={e => e.stopPropagation()}><header><div><p className="eyebrow">{t("NEW CONTRACT")}</p><h2>{t("Upload for AI extraction")}</h2><p>{t("Your file is stored locally, then securely sent to your organization’s Azure OpenAI deployment.")}</p></div><button type="button" onClick={onClose} aria-label={t("Close")}>×</button></header><label className={`drop-zone ${file ? "has-file" : ""}`}><input type="file" accept=".pdf,.docx,.png,.jpg,.jpeg,.webp" onChange={e => setFile(e.target.files?.[0] ?? null)} required/><span>{file ? "✓" : "⇧"}</span><strong>{file ? file.name : t("Choose a contract document")}</strong><small>{file ? t("{size} MB · Ready to extract", { size: (file.size / 1024 / 1024).toFixed(2) }) : t("PDF, DOCX, PNG, JPG or WEBP · Maximum 25 MB")}</small></label><div className="form-grid"><label>{t("Related vendor")} <input value={vendor} onChange={e => setVendor(e.target.value)} placeholder={t("Optional hint")}/></label><label>{t("Related project")} <input value={project} onChange={e => setProject(e.target.value)} placeholder={t("Optional hint")}/></label></div>{error && <p className="form-error">{error}</p>}<footer><button type="button" className="secondary-button" onClick={onClose}>{t("Cancel")}</button><button className="primary-button" disabled={!file || busy}>{busy ? <><span className="spinner"/>{t("Uploading…")}</> : t("Upload & extract")}</button></footer></form></div>;
}

const editableFields: { key: keyof Contract; label: string; type?: string; wide?: boolean }[] = [
  {key:"title",label:"Contract title",wide:true},{key:"reference_number",label:"Reference number"},{key:"vendor_name",label:"Vendor name"},{key:"project_name",label:"Project / scope",wide:true},{key:"phase",label:"Phase"},{key:"status",label:"Contract status"},{key:"project_start_date",label:"Start date",type:"date"},{key:"project_end_date",label:"End date",type:"date"},{key:"renewal_date",label:"Renewal date",type:"date"},{key:"currency",label:"Currency"},{key:"one_time_amount",label:"One-time amount",type:"number"},{key:"optional_amount",label:"Optional amount",type:"number"},{key:"vendor_contact",label:"Vendor contact"},{key:"internal_contact",label:"Internal contact"},{key:"department",label:"Department"},{key:"owner",label:"Contract owner"},{key:"deliverables",label:"Deliverables / services",wide:true},{key:"payment_terms",label:"Payment terms",wide:true},{key:"notes",label:"Notes",wide:true},
];

type DrawerTab = "Details" | "AI assistant" | "Approvals" | "Signatures" | "Comments" | "Versions" | "Obligations" | "Spend" | "History";
const drawerTabs: DrawerTab[] = ["Details", "AI assistant", "Approvals", "Signatures", "Comments", "Versions", "Obligations", "Spend", "History"];

function ContractDrawer({ contract, user, allTags, contracts, onOpenContract, onClose, onSaved, onDeleted }: { contract: Contract; user: User; allTags: string[]; contracts: Contract[]; onOpenContract: (id: number) => void; onClose: () => void; onSaved: (c: Contract) => Promise<void>; onDeleted: () => Promise<void> }) {
  const [form, setForm] = useState<Contract>(contract); const [saved, setSaved] = useState<Contract>(contract); const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  const [tab, setTab] = useState<DrawerTab>(contract.pending_approval_id ? "Approvals" : "Details");
  const fileExtension = saved.file_name?.split(".").pop()?.toLowerCase();
  const canEdit = can(user, "contract.edit");
  const locked = !!saved.pending_approval_id;
  // While an approval is pending only notes (and status, for hold/cancel) can change.
  const fieldDisabled = (key: keyof Contract) => !canEdit || (locked && !["notes", "status", "tags"].includes(key as string));
  const statusOptions = [saved.status, ...saved.allowed_transitions];
  const extracting = saved.extraction_status === "Queued" || saved.extraction_status === "Running";
  const [pollTick, setPollTick] = useState(0);
  // While the AI reads the document in the background, check back and fill in the form,
  // keeping anything the user has typed in the meantime.
  useEffect(() => {
    if (!extracting) return;
    const timer = window.setTimeout(async () => {
      try {
        const fresh = await api.contract(contract.id);
        if (fresh.extraction_status === "Queued" || fresh.extraction_status === "Running") { setPollTick(t => t + 1); return; }
        setForm(current => {
          const merged = { ...fresh } as Record<string, unknown>;
          for (const key of Object.keys(current) as (keyof Contract)[]) if (current[key] !== saved[key]) merged[key] = current[key];
          return merged as Contract;
        });
        setSaved(fresh);
        await onSaved(fresh);
      } catch { setPollTick(t => t + 1); }
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [extracting, pollTick, contract.id, saved, onSaved]);

  async function changed(next: Contract) { setForm(next); setSaved(next); await onSaved(next); }
  async function save(validate = false) { setBusy(true); setMessage(""); try { let result = await api.update(contract.id, form); if (validate) result = await api.validate(contract.id); await changed(result); setMessage(validate ? t("Contract validated.") : t("Changes saved.")); } catch (err) { setMessage(errorText(err, "Could not save")); } finally { setBusy(false); } }
  async function remove() { if (!window.confirm(t("Delete “{title}”? The contract record will be removed, while its uploaded files stay on this device.", { title: form.title }))) return; setBusy(true); setMessage(""); try { await api.remove(contract.id); await onDeleted(); } catch (err) { setMessage(errorText(err, "Could not delete contract")); setBusy(false); } }

  const panelProps = { contract: saved, user, onChanged: changed };
  return <div className="drawer-backdrop"><section className="review-drawer"><header><div><p className="eyebrow">{t("CONTRACT")} · <Status value={saved.status} plain /></p><h2>{saved.vendor_name ?? t("Unidentified vendor")}</h2><p>{saved.title}{saved.file_name ? ` · ${saved.file_name} (v${saved.document_version})` : ""}</p></div><button onClick={onClose} aria-label={t("Close")}>×</button></header>
    <div className="review-layout"><div className="document-pane">{saved.file_name ? (fileExtension === "pdf" || saved.file_type === "application/pdf" ? <iframe key={saved.document_version} title={t("Uploaded contract")} src={fileUrl(saved.id)}/> : fileExtension === "docx" ? <iframe key={saved.document_version} title={t("DOCX preview")} src={previewUrl(saved.id)}/> : <div className="file-preview"><span>▤</span><h3>{saved.file_name}</h3><p>{t("Preview is available for PDFs and DOCX files. Open the original document in a new tab.")}</p><a href={fileUrl(saved.id)} target="_blank" rel="noreferrer">{t("Open document ↗")}</a></div>) : <div className="file-preview"><span>◇</span><h3>{t("No document yet")}</h3><p>{t("Upload a document from the Versions tab.")}</p></div>}</div>
      <div className="fields-pane">
        <div className="wf-tabs" role="tablist">{drawerTabs.map(name => <button key={name} role="tab" aria-selected={tab === name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>{t(name)}{name === "Approvals" && saved.pending_approval_id ? <i className="wf-dot"/> : null}</button>)}</div>
        {tab === "Details" && <>
          {extracting && <p className="wf-hint extracting"><span className="spinner"/> {t("The AI is reading the document. Details fill in automatically; you can keep working meanwhile.")}</p>}
          {saved.extraction_status === "Failed" && <p className="wf-hint warn">{saved.extraction_note}</p>}
          {locked && <p className="wf-hint warn">{t("Awaiting approval: terms are locked. Withdraw the request on the Approvals tab to edit them.")}</p>}
          {!canEdit && <p className="wf-hint">{t("Your role has read-only access.")}</p>}
          {extracting ? null : form.extraction_note?.startsWith("Created") ? <div className="extraction-summary"><div><span>✎</span><div><strong>{t("Entered by hand")}</strong><p>{form.extraction_note}</p></div></div></div> : <div className="extraction-summary"><div><span>✦</span><div><strong>{t("AI extraction")}</strong><p>{form.extraction_note ?? t("Review each field before validation.")}</p></div></div><b>{Math.round((form.extraction_confidence ?? 0) * 100)}%</b></div>}
          <div className="wf-field wf-tags-field">{t("Tags")}<TagEditor value={form.tags ?? []} onChange={tags => setForm({ ...form, tags })} disabled={fieldDisabled("tags")} suggestions={allTags} /></div>
          <div className="edit-grid">{editableFields.map(field => { const score = form.field_confidences?.[field.key as string]; const low = form.low_confidence_fields?.includes(field.key as string); const missing = form.missing_fields?.includes(field.key as string); const value = form[field.key]; const isLong = ["deliverables","payment_terms","notes"].includes(field.key); const disabled = fieldDisabled(field.key); return <label key={field.key} className={field.wide ? "wide" : ""}><span>{t(field.label)}{missing ? <em className="field-flag missing">{t("Missing")}</em> : low ? <em className="field-flag low">{score ? t("Low {n}%", { n: Math.round(score*100) }) : t("Low confidence")}</em> : score ? <em className="field-flag good">{Math.round(score*100)}%</em> : null}</span>{field.key === "status" ? <select disabled={disabled} value={String(value ?? saved.status)} onChange={e => setForm({...form,status:e.target.value})}>{contractStatuses.filter(item => statusOptions.includes(item)).map(item => <option key={item} value={item}>{t(item)}</option>)}</select> : isLong ? <textarea disabled={disabled} value={(value as string) ?? ""} onChange={e => setForm({...form,[field.key]:e.target.value})}/> : <input disabled={disabled} type={field.type ?? "text"} value={(value as string | number) ?? ""} onChange={e => setForm({...form,[field.key]:field.type === "number" ? (e.target.value ? Number(e.target.value) : null) : e.target.value})}/>}</label>; })}</div>
          {saved.status === "Quoting" && <p className="wf-hint">{t("To move past Quoting, validate the details and submit the contract on the Approvals tab.")}</p>}
          <FamilyCard contract={saved} user={user} contracts={contracts} onChanged={changed} onOpen={onOpenContract} />
          <SharingCard contract={saved} user={user} />
        </>}
        {tab === "AI assistant" && <AiPanel {...panelProps} />}
        {tab === "Approvals" && <ApprovalsPanel {...panelProps} />}
        {tab === "Signatures" && <SignaturesPanel {...panelProps} />}
        {tab === "Comments" && <CommentsPanel {...panelProps} />}
        {tab === "Versions" && <VersionsPanel {...panelProps} />}
        {tab === "Obligations" && <ObligationsPanel {...panelProps} />}
        {tab === "Spend" && <SpendPanel contract={saved} user={user} />}
        {tab === "History" && <HistoryPanel {...panelProps} />}
      </div></div>
    <footer>{can(user, "contract.delete") && <button className="danger-button" onClick={remove} disabled={busy}>{t("Delete contract")}</button>}<div>{message && <span className="save-message">{message}</span>}</div>
      {tab === "Details" && canEdit && <button className="secondary-button" onClick={() => save(false)} disabled={busy}>{t("Save changes")}</button>}
      {tab === "Details" && can(user, "contract.validate") && <button className="primary-button" onClick={() => save(true)} disabled={busy || saved.validation_status === "Validated" || locked || extracting}>{saved.validation_status === "Validated" ? t("✓ Validated") : t("Confirm & validate")}</button>}
    </footer></section></div>;
}
