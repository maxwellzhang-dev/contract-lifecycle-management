import { Languages, type LucideIcon } from "lucide-react";
import { getLocale, intlLocale, locales, setLocale, t, type Locale } from "./i18n";

export function dateLabel(value?: string | null) {
  if (!value) return "—";
  const date = value.length <= 10 ? new Date(`${value}T00:00:00`) : new Date(value);
  return new Intl.DateTimeFormat(intlLocale(), { day: "2-digit", month: "short", year: "numeric" }).format(date);
}

export function dateTimeLabel(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString(intlLocale(), { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function amountLabel(currency?: string | null, amount?: number | null) {
  if (amount == null) return "—";
  return `${currency ?? ""} ${amount.toLocaleString(intlLocale())}`.trim();
}

export function tone(status: string) {
  if (["Cleared to Start", "In Progress", "Completed", "Validated", "Approved", "Done", "Paid", "Signed"].includes(status)) return "green";
  if (["Under Review", "Pending", "Open", "Received", "Sent"].includes(status)) return "amber";
  if (["Expired", "Rejected", "Overdue", "Disputed", "Declined"].includes(status)) return "red";
  return "slate";
}

export function Status({ value, plain = false }: { value: string; plain?: boolean }) {
  // plain: a dot and text, for the business status; the filled pill is kept for validation so the two columns read differently.
  return <span className={`status ${tone(value)}${plain ? " plain" : ""}`}><i />{t(value)}</span>;
}

export function SectionHeader({ eyebrow, title, copy }: { eyebrow: string; title: string; copy: string }) {
  return <div className="section-heading"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{copy}</p></div>;
}

export function EmptyState({ icon: Icon, title, copy }: { icon: LucideIcon; title: string; copy: string }) {
  return <div className="overview-empty"><Icon size={22}/><div><strong>{title}</strong><p>{copy}</p></div></div>;
}

export function errorText(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : t(fallback);
}

export function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]!.toUpperCase()).join("");
}

export function roleLabel(role: string) {
  return t(role.charAt(0).toUpperCase() + role.slice(1));
}

/** The label of a stored value (status, kind, category…) in the interface language. */
export const label = (value: string | null | undefined) => value ? t(value) : "";

export function AuditDetails({ details }: { details?: string }) {
  if (!details) return null;
  const changes = details.split("; ").map(change => {
    const match = change.match(/^(.+?): (.+) → (.+)$/);
    return match ? { field: match[1], before: match[2], after: match[3] } : null;
  });
  if (changes.every(Boolean)) return <div className="audit-changes">{changes.map(change => <div className="audit-change" key={change!.field}><span>{change!.field}</span><div><del>{change!.before}</del><b>→</b><ins>{change!.after}</ins></div></div>)}</div>;
  return <p className="audit-note">{details}</p>;
}

export function LanguagePicker({ compact = false }: { compact?: boolean }) {
  return <label className={compact ? "language-picker compact" : "language-picker"}><Languages size={15} aria-hidden="true"/>
    <span className="sr-only">{t("Language")}</span>
    <select aria-label={t("Language")} value={getLocale()} onChange={e => setLocale(e.target.value as Locale)}>
      {locales.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
    </select></label>;
}

