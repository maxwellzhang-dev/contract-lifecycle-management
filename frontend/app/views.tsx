"use client";

import { FormEvent, useEffect, useState } from "react";
import { Bell, CalendarClock, CalendarPlus, CheckCircle2, ClipboardCheck, KeyRound, LogOut, UserPlus } from "lucide-react";
import {
  accessScopes, api, can, obligationStatuses, roles, ssoLoginUrl, type AccessScope,
  type InboxItem, type Notification, type Obligation, type User,
} from "./api";
import { JobsPanel } from "./system";
import { CalendarModal, TeamsCard } from "./calendar";
import { EmptyState, LanguagePicker, SectionHeader, Status, amountLabel, dateLabel, dateTimeLabel, errorText, initials, roleLabel } from "./shared";
import { t } from "./i18n";

// --- sign in -------------------------------------------------------------------------

type AuthConfig = { sso_enabled: boolean; sso_label: string; password_login: boolean };

function MicrosoftMark() {
  return <svg width="16" height="16" viewBox="0 0 21 21" aria-hidden="true"><rect x="1" y="1" width="9" height="9" fill="#f25022"/><rect x="11" y="1" width="9" height="9" fill="#7fba00"/><rect x="1" y="11" width="9" height="9" fill="#00a4ef"/><rect x="11" y="11" width="9" height="9" fill="#ffb900"/></svg>;
}

export function LoginScreen({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [config, setConfig] = useState<AuthConfig>({ sso_enabled: false, sso_label: "", password_login: true });
  useEffect(() => {
    let cancelled = false;
    api.authConfig().then(c => { if (!cancelled) setConfig(c); }).catch(() => undefined);
    // A failed single sign-on comes back as ?sso_error=…; show it once and tidy the address bar.
    const params = new URLSearchParams(window.location.search);
    const ssoError = params.get("sso_error");
    if (ssoError) {
      queueMicrotask(() => { if (!cancelled) setError(ssoError); });
      window.history.replaceState(null, "", window.location.pathname);
    }
    return () => { cancelled = true; };
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { onSignedIn((await api.login(email, password)).user); }
    catch (err) { setError(errorText(err, "Could not sign in")); setBusy(false); }
  }
  return <main className="login-shell">
    <form className="login-card" onSubmit={submit}>
      <div className="login-top"><div className="brand"><div className="brand-mark" aria-hidden="true"><span /><span /><span /></div><div><strong>Clause</strong><small>{t("Contract intelligence")}</small></div></div><LanguagePicker compact /></div>
      <h1>{t("Sign in")}</h1>
      {config.sso_enabled && <a className="sso-button" href={ssoLoginUrl}><MicrosoftMark />{config.sso_label}</a>}
      {config.sso_enabled && config.password_login && <div className="login-divider"><span>{t("or use a password")}</span></div>}
      {config.password_login && <>
        <label className="wf-field">{t("Work email")}<input type="email" autoComplete="username" required value={email} onChange={e => setEmail(e.target.value)} /></label>
        <label className="wf-field">{t("Password")}<input type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} /></label>
      </>}
      {error && <p className="form-error">{error}</p>}
      {config.password_login && <button className="primary-button" disabled={busy}>{busy ? t("Signing in…") : t("Sign in")}</button>}
      {config.password_login && !config.sso_enabled && <p className="wf-hint">{t("Local demo accounts are listed in the README.")}</p>}
    </form>
  </main>;
}

// --- account + notifications (top bar) -----------------------------------------------

export function AccountMenu({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const [open, setOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [message, setMessage] = useState("");
  const [calendarOpen, setCalendarOpen] = useState(false);
  async function change(event: FormEvent) {
    event.preventDefault(); setMessage("");
    try { await api.changePassword(current, next); setCurrent(""); setNext(""); setChanging(false); setMessage(t("Password changed.")); }
    catch (err) { setMessage(errorText(err, "Could not change password")); }
  }
  return <div className="profile wf-account">
    <div className="avatar">{initials(user.name)}</div>
    <div><strong>{user.name}</strong><small>{roleLabel(user.role)}</small></div>
    <button className="wf-icon-link" aria-label={t("Account menu")} aria-expanded={open} onClick={() => setOpen(!open)}>⋯</button>
    {open && <div className="wf-popover wf-account-menu">
      <small>{user.email}</small>
      <LanguagePicker />
      {changing ? <form onSubmit={change}>
        <label className="wf-field">{t("Current password")}<input type="password" required value={current} onChange={e => setCurrent(e.target.value)} /></label>
        <label className="wf-field">{t("New password (10+ characters)")}<input type="password" minLength={10} required value={next} onChange={e => setNext(e.target.value)} /></label>
        <button className="primary-button">{t("Save password")}</button>
      </form> : <button className="wf-menu-item" onClick={() => setChanging(true)}><KeyRound size={16}/> {t("Change password")}</button>}
      <button className="wf-menu-item" onClick={() => { setCalendarOpen(true); setOpen(false); }}><CalendarPlus size={16}/> {t("Calendar subscription")}</button>
      {message && <p className="wf-hint">{message}</p>}
      <button className="wf-menu-item" onClick={onSignOut}><LogOut size={16}/> {t("Sign out")}</button>
    </div>}
    {calendarOpen && <CalendarModal onClose={() => setCalendarOpen(false)} />}
  </div>;
}

export function NotificationsMenu({ onOpenContract, refreshKey }: { onOpenContract: (id: number) => void; refreshKey: number }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<{ unread: number; items: Notification[] }>({ unread: 0, items: [] });
  useEffect(() => {
    let cancelled = false;
    const load = () => api.notifications().then(result => { if (!cancelled) setData(result); }).catch(() => undefined);
    void load();
    const timer = window.setInterval(load, 60_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [refreshKey]);
  async function select(item: Notification) {
    if (!item.read_at) await api.readNotification(item.id).catch(() => undefined);
    setData(current => ({ unread: Math.max(0, current.unread - (item.read_at ? 0 : 1)), items: current.items.map(n => n.id === item.id ? { ...n, read_at: n.read_at ?? new Date().toISOString() } : n) }));
    setOpen(false);
    if (item.contract_id) onOpenContract(item.contract_id);
  }
  async function readAll() {
    await api.readAllNotifications().catch(() => undefined);
    setData(current => ({ unread: 0, items: current.items.map(n => ({ ...n, read_at: n.read_at ?? new Date().toISOString() })) }));
  }
  return <div className="wf-bell">
    <button className="icon-button" aria-label={data.unread ? t("Notifications ({n} unread)", { n: data.unread }) : t("Notifications")} aria-expanded={open} onClick={() => setOpen(!open)}><Bell size={18}/>{data.unread > 0 && <em>{data.unread > 99 ? "99+" : data.unread}</em>}</button>
    {open && <div className="wf-popover wf-notifications">
      <header><strong>{t("Notifications")}</strong>{data.unread > 0 && <button onClick={readAll}>{t("Mark all read")}</button>}</header>
      {data.items.length ? <ul>{data.items.map(item => <li key={item.id}><button className={item.read_at ? "" : "unread"} onClick={() => void select(item)}>
        <strong>{item.title}</strong>{item.body && <span>{item.body}</span>}<small>{dateTimeLabel(item.created_at)}</small>
      </button></li>)}</ul> : <p className="wf-hint">{t("Nothing yet. Approval requests and due-date reminders appear here.")}</p>}
    </div>}
  </div>;
}

// --- approvals inbox -----------------------------------------------------------------

export function ApprovalsView({ items, onOpen }: { items: InboxItem[]; onOpen: (contractId: number) => void }) {
  return <>
    <SectionHeader eyebrow={t("APPROVALS")} title={t("Waiting for you")} copy={t("Contracts at an approval step your role can decide. Open one to review the document and approve or reject.")} />
    {items.length ? <div className="wf-inbox">{items.map(item => { const playbook = item.ai_playbook_counts ? item.ai_playbook_counts.deviates + item.ai_playbook_counts.missing : 0; return <button key={item.step_id} className="wf-inbox-row" onClick={() => onOpen(item.contract_id)}>
      <span className="wf-badge">{t("Step {step}/{total}", { step: item.step_order, total: item.total_steps })}</span>
      <div><strong>{item.title}</strong><small>{t("{vendor} · {role} approval · submitted by {name} {date}", { vendor: item.vendor_name ?? t("Vendor not identified"), role: roleLabel(item.role), name: item.submitted_by_name, date: dateLabel(item.submitted_at) })}</small>{item.note && <p>“{item.note}”</p>}</div>
      {item.ai_risk_counts ? <span className={item.ai_risk_counts.high ? "ai-sev high" : "ai-sev low"} title={t("Risks flagged by the AI review")}>{t("AI: {high} high · {medium} med", { high: item.ai_risk_counts.high, medium: item.ai_risk_counts.medium })}{playbook ? ` · ${t("{n} playbook", { n: playbook })}` : ""}</span> : <span />}
      <b>{amountLabel(item.currency, (item.one_time_amount ?? 0) + (item.optional_amount ?? 0))}</b>
      <span className="wf-go">{t("Review →")}</span>
    </button>; })}</div> : <section className="panel"><EmptyState icon={ClipboardCheck} title={t("No approvals waiting")} copy={t("When a contract reaches a step for your role, it appears here and in your notifications.")} /></section>}
  </>;
}

// --- obligations ---------------------------------------------------------------------

export function ObligationsView({ user, onOpen }: { user: User; onOpen: (contractId: number) => void }) {
  const [filter, setFilter] = useState<"open" | "30" | "overdue" | "all">("open");
  const [items, setItems] = useState<Obligation[] | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const query = filter === "all" ? "" : filter === "30" ? "?status=Open&due_within=30" : "?status=Open";
    api.obligations(query).then(result => { if (!cancelled) { setItems(filter === "overdue" ? result.filter(o => o.overdue) : result); setError(""); } })
      .catch(err => { if (!cancelled) setError(errorText(err, "Could not load obligations")); });
    return () => { cancelled = true; };
  }, [filter, tick]);
  const manage = can(user, "obligation.manage");
  async function setStatus(item: Obligation, status: string) {
    try { await api.updateObligation(item.id, { status }); setTick(n => n + 1); } catch (err) { setError(errorText(err, "Could not update")); }
  }
  return <>
    <SectionHeader eyebrow={t("OBLIGATIONS")} title={t("Obligations & milestones")} copy={t("Deliverables, payments and milestones across all contracts. Owners get reminders 7 days before and when overdue.")} />
    <div className="filter-bar">
      <select value={filter} onChange={e => setFilter(e.target.value as typeof filter)} aria-label={t("Filter obligations")}>
        <option value="open">{t("All open")}</option><option value="30">{t("Open, due within 30 days")}</option><option value="overdue">{t("Overdue")}</option><option value="all">{t("Everything")}</option>
      </select>
    </div>
    {error && <p className="form-error">{error}</p>}
    <section className="panel">{items && !items.length ? <EmptyState icon={CheckCircle2} title={t("Nothing here")} copy={t("No obligations match this filter.")} /> :
      <ul className="wf-list">{items?.map(item => <li key={item.id} className={item.overdue ? "overdue" : ""}>
        <span className="wf-badge">{t(item.kind)}</span>
        <div><strong>{item.title}</strong><small><button className="wf-link" onClick={() => onOpen(item.contract_id)}>{item.contract_title}</button>{item.vendor_name ? ` · ${item.vendor_name}` : ""}{item.owner ? ` · ${item.owner}` : ""}{item.amount != null ? ` · ${amountLabel(item.currency, item.amount)}` : ""}</small></div>
        <div className="wf-due"><CalendarClock size={15}/>{item.due_date ? dateLabel(item.due_date) : t("No date")}{item.overdue && <em className="due-in">{t("{n}d overdue", { n: -item.days_until_due! })}</em>}</div>
        {manage ? <select aria-label={t("Obligation status")} value={item.status} onChange={e => void setStatus(item, e.target.value)}>{obligationStatuses.map(s => <option key={s} value={s}>{t(s)}</option>)}</select> : <Status value={item.overdue ? "Overdue" : item.status} />}
      </li>)}</ul>}
    </section>
  </>;
}

// --- users (admin) -------------------------------------------------------------------

export function UsersView({ user }: { user: User }) {
  const [users, setUsers] = useState<User[]>([]);
  const blankForm = { name: "", email: "", role: "viewer", password: "", department: "", access_scope: "all" as AccessScope };
  const [form, setForm] = useState(blankForm);
  const [message, setMessage] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    api.users().then(result => { if (!cancelled) setUsers(result); }).catch(err => { if (!cancelled) setMessage(errorText(err, "Could not load users")); });
    return () => { cancelled = true; };
  }, [tick]);
  async function run(action: () => Promise<unknown>, done: string) {
    setMessage("");
    try { await action(); setTick(n => n + 1); setMessage(done); } catch (err) { setMessage(errorText(err, "Action failed")); }
  }
  async function create(event: FormEvent) {
    event.preventDefault();
    await run(() => api.createUser(form), t("Created {name}.", { name: form.name }));
    setForm(blankForm);
  }
  function changeScope(target: User, scope: AccessScope) {
    let department = target.department ?? "";
    if (scope === "department") {
      const answer = window.prompt(t("Department for {name} (must match the contracts' department):", { name: target.name }), department);
      if (answer === null || !answer.trim()) return;
      department = answer.trim();
    }
    const scopeLabel = t(accessScopes.find(a => a.value === scope)!.label);
    void run(() => api.updateUser(target.id, { access_scope: scope, department: department || null }),
      scope === "department" ? t("{name} now sees: {scope} ({department}).", { name: target.name, scope: scopeLabel, department }) : t("{name} now sees: {scope}.", { name: target.name, scope: scopeLabel }));
  }
  function resetPassword(target: User) {
    const password = window.prompt(t("New password for {name} (at least 10 characters):", { name: target.name }));
    if (password) void run(() => api.updateUser(target.id, { password }), t("Password reset for {name}. Their sessions were signed out.", { name: target.name }));
  }
  return <>
    <SectionHeader eyebrow={t("ADMINISTRATION")} title={t("Users & roles")} copy={t("Roles decide what people can do; “Can see” decides which contracts they see. Involved means owner, uploader, approver, signer or added member.")} />
    <section className="panel wf-roles">
      <p>{t("Admin: everything, including deleting contracts and managing users · Legal: upload, edit, validate, submit, approve legal steps and manage the playbook · Procurement: upload, edit, validate, submit and record invoices · Finance: edit, manage obligations and invoices, approve finance steps · Viewer: read only.")}</p>
    </section>
    <form className="panel wf-card wf-user-form" onSubmit={create}>
      <h3><UserPlus size={17}/> {t("Add user")}</h3>
      <div className="wf-grid">
        <label className="wf-field">{t("Name")}<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
        <label className="wf-field">{t("Email")}<input type="email" required value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></label>
        <label className="wf-field">{t("Role")}<select value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}>{roles.map(r => <option key={r} value={r}>{roleLabel(r)}</option>)}</select></label>
        <label className="wf-field">{t("Temporary password")}<input type="password" minLength={10} required value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label>
        <label className="wf-field">{t("Can see")}<select value={form.access_scope} onChange={e => setForm({ ...form, access_scope: e.target.value as AccessScope })}>{accessScopes.map(a => <option key={a.value} value={a.value}>{t(a.label)}</option>)}</select></label>
        <label className="wf-field">{form.access_scope === "department" ? t("Department") : t("Department (optional)")}<input required={form.access_scope === "department"} value={form.department} onChange={e => setForm({ ...form, department: e.target.value })} placeholder={t("e.g. Finance")} /></label>
      </div>
      <button className="primary-button">{t("Add user")}</button>
    </form>
    {message && <p className="save-message">{message}</p>}
    <section className="panel"><ul className="wf-list">{users.map(item => <li key={item.id} className={item.active ? "" : "inactive"}>
      <span className="audit-avatar">{initials(item.name)}</span>
      <div><strong>{item.name}{item.id === user.id ? ` ${t("(you)")}` : ""}</strong><small>{item.email}{item.department ? ` · ${item.department}` : ""}{item.active ? "" : ` · ${t("deactivated")}`}</small></div>
      <select aria-label={t("Contracts {name} can see", { name: item.name })} value={item.access_scope} disabled={item.role === "admin"} title={item.role === "admin" ? t("Admins always see every contract") : undefined} onChange={e => changeScope(item, e.target.value as AccessScope)}>{accessScopes.map(a => <option key={a.value} value={a.value}>{t(a.label)}</option>)}</select>
      <select aria-label={t("Role for {name}", { name: item.name })} value={item.role} disabled={item.id === user.id} onChange={e => void run(() => api.updateUser(item.id, { role: e.target.value }), t("{name} is now {role}.", { name: item.name, role: roleLabel(e.target.value) }))}>{roles.map(r => <option key={r} value={r}>{roleLabel(r)}</option>)}</select>
      <button className="secondary-button" onClick={() => resetPassword(item)}>{t("Reset password")}</button>
      {item.id !== user.id && <button className={item.active ? "danger-button" : "secondary-button"} onClick={() => void run(() => api.updateUser(item.id, { active: !item.active }), item.active ? t("{name} deactivated.", { name: item.name }) : t("{name} reactivated.", { name: item.name }))}>{item.active ? t("Deactivate") : t("Reactivate")}</button>}
    </li>)}</ul></section>
    <TeamsCard />
    <JobsPanel />
  </>;
}

