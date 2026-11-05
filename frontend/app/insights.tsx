"use client";

import { useEffect, useState } from "react";
import { Building2, Download } from "lucide-react";
import { API_BASE, api, type CurrencyValue, type Report, type VendorSummary } from "./api";
import { EmptyState, SectionHeader, dateLabel, errorText } from "./shared";
import { SpendOverviewPanel } from "./invoices";

function Values({ values }: { values: CurrencyValue[] }) {
  if (!values.length) return <span className="wf-muted">—</span>;
  return <span className="wf-values">{values.map(v => <span key={v.currency}>{v.currency} {v.value.toLocaleString()}</span>)}</span>;
}

export function VendorsView({ onOpenVendor }: { onOpenVendor: (vendor: string) => void }) {
  const [vendors, setVendors] = useState<VendorSummary[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    let cancelled = false;
    api.vendors().then(v => { if (!cancelled) setVendors(v); }).catch(err => { if (!cancelled) setError(errorText(err, "Could not load vendors")); });
    return () => { cancelled = true; };
  }, []);
  const shown = (vendors ?? []).filter(v => v.vendor_name.toLowerCase().includes(query.trim().toLowerCase()));
  return <>
    <SectionHeader eyebrow="VENDORS" title="Vendors" copy="Every vendor with its contracts, spend per currency, next end date and open obligations. Open one to see its contracts." />
    <div className="filter-bar"><label><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Find a vendor…" aria-label="Find a vendor" /></label></div>
    {error && <p className="form-error">{error}</p>}
    {vendors && !shown.length ? <section className="panel"><EmptyState icon={Building2} title="No vendors" copy="Vendors appear here once contracts name them." /></section> :
      <div className="wf-vendor-grid">{shown.map(v => {
        const clickable = v.vendor_name !== "(none)";
        return <button key={v.vendor_name} className="wf-vendor" disabled={!clickable} onClick={() => onOpenVendor(v.vendor_name)}>
          <header><strong>{clickable ? v.vendor_name : "Vendor not identified"}</strong><small>{v.contract_count} contract{v.contract_count === 1 ? "" : "s"} · {v.active_count} active</small></header>
          <Values values={v.value_by_currency} />
          <dl>
            <div><dt>Next end date</dt><dd>{dateLabel(v.next_end_date)}</dd></div>
            <div><dt>Open obligations</dt><dd className={v.overdue_obligations ? "warn" : ""}>{v.open_obligations}{v.overdue_obligations ? ` (${v.overdue_obligations} overdue)` : ""}</dd></div>
            <div><dt>Departments</dt><dd>{v.departments.join(", ") || "—"}</dd></div>
          </dl>
          <footer>{Object.entries(v.statuses).map(([status, count]) => <span key={status} className="wf-chip">{status} {count}</span>)}</footer>
        </button>;
      })}</div>}
  </>;
}

const groupOptions: { value: string; label: string }[] = [
  { value: "department", label: "Department" }, { value: "vendor", label: "Vendor" }, { value: "status", label: "Status" },
  { value: "owner", label: "Owner" }, { value: "tag", label: "Tag" }, { value: "start_month", label: "Start month" },
  { value: "end_month", label: "End month" },
];

export function ReportsView({ onOpen }: { onOpen?: (id: number) => void }) {
  const [groupBy, setGroupBy] = useState("department");
  const [dateField, setDateField] = useState("start");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    api.report(groupBy, dateField, start, end).then(r => { if (!cancelled) { setReport(r); setError(""); } })
      .catch(err => { if (!cancelled) setError(errorText(err, "Could not load the report")); });
    return () => { cancelled = true; };
  }, [groupBy, dateField, start, end]);
  const currencies = [...new Set(report?.rows.flatMap(r => r.value_by_currency.map(v => v.currency)) ?? [])].sort();
  const params = new URLSearchParams({ date_field: dateField, ...(start ? { start } : {}), ...(end ? { end } : {}) });
  const label = groupOptions.find(g => g.value === groupBy)?.label ?? "Group";
  return <>
    <SectionHeader eyebrow="REPORTS" title="Portfolio reports" copy="Contract counts and value per currency, grouped the way you need. Values are never converted between currencies." />
    <div className="wf-filters panel">
      <label className="wf-field">Group by<select value={groupBy} onChange={e => setGroupBy(e.target.value)}>{groupOptions.map(g => <option key={g.value} value={g.value}>{g.label}</option>)}</select></label>
      <label className="wf-field">Date range applies to<select value={dateField} onChange={e => setDateField(e.target.value)}><option value="start">Start date</option><option value="end">End date</option></select></label>
      <label className="wf-field">From<input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
      <label className="wf-field">To<input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label>
      <a className="primary-button wf-download" href={`${API_BASE}/api/export/report.xlsx?${params}`}><Download size={16}/> Download Excel</a>
    </div>
    {error && <p className="form-error">{error}</p>}
    {report && <section className="panel">
      <div className="panel-title"><div><h2>{report.contract_count} contract{report.contract_count === 1 ? "" : "s"}{start || end ? " in range" : ""}</h2><p><Values values={report.value_by_currency} /></p></div></div>
      <div className="table-wrap"><table className="wf-report">
        <thead><tr><th>{label}</th><th className="num">Contracts</th><th className="num">Active</th><th className="num">Completed</th>{currencies.map(c => <th key={c} className="num">Total {c}</th>)}</tr></thead>
        <tbody>{report.rows.map(row => { const values = Object.fromEntries(row.value_by_currency.map(v => [v.currency, v.value])); return <tr key={row.group}>
          <td>{row.group === "(none)" ? <span className="wf-muted">Not set</span> : row.group}</td><td className="num">{row.count}</td><td className="num">{row.active}</td><td className="num">{row.completed}</td>
          {currencies.map(c => <td key={c} className="num">{values[c] ? values[c].toLocaleString() : "—"}</td>)}
        </tr>; })}
        {!report.rows.length && <tr><td colSpan={4 + currencies.length} className="empty-cell">No contracts in this range.</td></tr>}</tbody>
      </table></div>
      {groupBy === "tag" && <p className="wf-hint">A contract with several tags is counted under each of them.</p>}
    </section>}
    <SpendOverviewPanel onOpen={onOpen} />
  </>;
}
