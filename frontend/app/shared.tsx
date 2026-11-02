import type { LucideIcon } from "lucide-react";

export function dateLabel(value?: string | null) {
  if (!value) return "—";
  const date = value.length <= 10 ? new Date(`${value}T00:00:00`) : new Date(value);
  return new Intl.DateTimeFormat("en-SG", { day: "2-digit", month: "short", year: "numeric" }).format(date);
}

export function dateTimeLabel(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-SG", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function amountLabel(currency?: string | null, amount?: number | null) {
  if (amount == null) return "—";
  return `${currency ?? ""} ${amount.toLocaleString()}`.trim();
}

export function tone(status: string) {
  if (["Cleared to Start", "In Progress", "Completed", "Validated", "Approved", "Done"].includes(status)) return "green";
  if (["Under Review", "Pending", "Open"].includes(status)) return "amber";
  if (["Expired", "Rejected", "Overdue"].includes(status)) return "red";
  return "slate";
}

export function Status({ value, plain = false }: { value: string; plain?: boolean }) {
  // plain: a dot and text, for the business status; the filled pill is kept for validation so the two columns read differently.
  return <span className={`status ${tone(value)}${plain ? " plain" : ""}`}><i />{value}</span>;
}

export function SectionHeader({ eyebrow, title, copy }: { eyebrow: string; title: string; copy: string }) {
  return <div className="section-heading"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{copy}</p></div>;
}

export function EmptyState({ icon: Icon, title, copy }: { icon: LucideIcon; title: string; copy: string }) {
  return <div className="overview-empty"><Icon size={22}/><div><strong>{title}</strong><p>{copy}</p></div></div>;
}

export function errorText(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : fallback;
}

export function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]!.toUpperCase()).join("");
}

export function roleLabel(role: string) {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

export function AuditDetails({ details }: { details?: string }) {
  if (!details) return null;
  const changes = details.split("; ").map(change => {
    const match = change.match(/^(.+?): (.+) → (.+)$/);
    return match ? { field: match[1], before: match[2], after: match[3] } : null;
  });
  if (changes.every(Boolean)) return <div className="audit-changes">{changes.map(change => <div className="audit-change" key={change!.field}><span>{change!.field}</span><div><del>{change!.before}</del><b>→</b><ins>{change!.after}</ins></div></div>)}</div>;
  return <p className="audit-note">{details}</p>;
}
