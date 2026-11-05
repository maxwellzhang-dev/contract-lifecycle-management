"use client";

// Invoices against a contract and how much of its value has been used.

import { FormEvent, useEffect, useState } from "react";
import { AlertTriangle, Receipt, Trash2 } from "lucide-react";
import { api, can, invoiceStatuses, type Contract, type Invoice, type Obligation, type Spend, type SpendOverview, type User } from "./api";
import { Status, amountLabel, dateLabel, errorText } from "./shared";

const emptyForm = { invoice_number: "", invoice_date: "", due_date: "", amount: "", status: "Received", obligation_id: "", notes: "" };

export function SpendMeter({ spend }: { spend: Pick<Spend, "budget" | "invoiced" | "paid" | "percent" | "level" | "warn_percent"> }) {
  const used = spend.budget ? Math.min(100, (spend.invoiced / spend.budget) * 100) : spend.invoiced ? 100 : 0;
  const paid = spend.budget ? Math.min(100, (spend.paid / spend.budget) * 100) : 0;
  return <div className={`spend-meter ${spend.level}`} role="img" aria-label={`${spend.percent ?? 0}% of the contract value invoiced`}>
    <span className="spend-invoiced" style={{ width: `${used}%` }} />
    <span className="spend-paid" style={{ width: `${paid}%` }} />
    {spend.budget ? <i style={{ left: `${spend.warn_percent}%` }} title={`Warning at ${spend.warn_percent}%`} /> : null}
  </div>;
}

export function SpendPanel({ contract, user }: { contract: Contract; user: User }) {
  const [spend, setSpend] = useState<Spend | null>(null);
  const [items, setItems] = useState<Invoice[]>([]);
  const [obligations, setObligations] = useState<Obligation[]>([]);
  const [form, setForm] = useState(emptyForm);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [tick, setTick] = useState(0);
  const manage = can(user, "invoice.manage");
  useEffect(() => {
    let cancelled = false;
    api.invoices(contract.id).then(d => { if (!cancelled) { setSpend(d.spend); setItems(d.items); } })
      .catch(err => { if (!cancelled) setMessage(errorText(err, "Could not load invoices")); });
    api.obligations(`?contract_id=${contract.id}`).then(o => { if (!cancelled) setObligations(o.filter(x => x.kind === "Payment")); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [contract.id, contract.one_time_amount, contract.optional_amount, tick]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true); setMessage("");
    try { await action(); setTick(t => t + 1); return true; } catch (err) { setMessage(errorText(err, "Action failed")); return false; } finally { setBusy(false); }
  }
  async function add(event: FormEvent) {
    event.preventDefault();
    const ok = await run(() => api.createInvoice(contract.id, {
      invoice_number: form.invoice_number || null, invoice_date: form.invoice_date || null, due_date: form.due_date || null,
      amount: Number(form.amount), status: form.status as Invoice["status"], notes: form.notes || null,
      obligation_id: form.obligation_id ? Number(form.obligation_id) : null,
    }));
    if (ok) setForm(emptyForm);
  }

  return <div className="wf-panel">
    {spend && <section className="wf-card spend-card">
      <header className="wf-card-head"><div><h3><Receipt size={15}/> Contract value used</h3>
        <small>{spend.budget == null ? "This contract has no value yet, so usage cannot be measured." : `${spend.percent ?? 0}% invoiced · warning at ${spend.warn_percent}%`}</small></div>
        {spend.level === "over" && <span className="spend-flag over"><AlertTriangle size={14}/> Over budget</span>}
        {spend.level === "warning" && <span className="spend-flag warning"><AlertTriangle size={14}/> Nearly used</span>}
      </header>
      <SpendMeter spend={spend} />
      <dl className="spend-figures">
        <div><dt>Contract value</dt><dd>{amountLabel(spend.currency, spend.budget)}</dd></div>
        <div><dt>Invoiced</dt><dd>{amountLabel(spend.currency, spend.invoiced)}</dd></div>
        <div><dt>Paid</dt><dd>{amountLabel(spend.currency, spend.paid)}</dd></div>
        <div><dt>Remaining</dt><dd className={spend.remaining != null && spend.remaining < 0 ? "negative" : ""}>{amountLabel(spend.currency, spend.remaining)}</dd></div>
        {spend.overdue > 0 && <div><dt>Overdue to pay</dt><dd className="negative">{amountLabel(spend.currency, spend.overdue)}</dd></div>}
        {spend.disputed > 0 && <div><dt>Disputed (not counted)</dt><dd>{amountLabel(spend.currency, spend.disputed)}</dd></div>}
      </dl>
    </section>}

    {manage && <form className="wf-card" onSubmit={add}>
      <h3>Record an invoice</h3>
      {!contract.currency && <p className="wf-hint warn">Set the contract currency on the Details tab first. Invoices are kept in the contract currency and never converted.</p>}
      <div className="wf-grid">
        <label className="wf-field">Invoice number<input value={form.invoice_number} onChange={e => setForm({ ...form, invoice_number: e.target.value })} placeholder="e.g. INV-1042" /></label>
        <label className="wf-field">Amount {contract.currency ? `(${contract.currency})` : ""}<input required type="number" min="0.01" step="0.01" value={form.amount} onChange={e => setForm({ ...form, amount: e.target.value })} /></label>
        <label className="wf-field">Invoice date<input type="date" value={form.invoice_date} onChange={e => setForm({ ...form, invoice_date: e.target.value })} /></label>
        <label className="wf-field">Due date<input type="date" value={form.due_date} onChange={e => setForm({ ...form, due_date: e.target.value })} /></label>
        <label className="wf-field">Status<select value={form.status} onChange={e => setForm({ ...form, status: e.target.value })}>{invoiceStatuses.map(s => <option key={s}>{s}</option>)}</select></label>
        {obligations.length > 0 && <label className="wf-field">Settles payment obligation<select value={form.obligation_id} onChange={e => setForm({ ...form, obligation_id: e.target.value })}><option value="">None</option>{obligations.map(o => <option key={o.id} value={o.id}>{o.title}{o.due_date ? ` (${dateLabel(o.due_date)})` : ""}</option>)}</select></label>}
      </div>
      <button className="primary-button" disabled={busy || !form.amount || !contract.currency}>Add invoice</button>
    </form>}
    {message && <p className="form-error">{message}</p>}

    {items.length === 0 ? <p className="wf-hint">No invoices recorded yet.</p> : <ul className="wf-list invoice-list">{items.map(item => <li key={item.id} className={item.overdue ? "overdue" : ""}>
      <span className="wf-badge">{item.invoice_number ?? `#${item.id}`}</span>
      <div><strong>{amountLabel(item.currency, item.amount)}</strong>
        <small>{[item.invoice_date ? `Dated ${dateLabel(item.invoice_date)}` : null, item.due_date ? `due ${dateLabel(item.due_date)}` : null,
          item.overdue ? "overdue" : null, item.paid_at ? `paid ${dateLabel(item.paid_at)}` : null, item.obligation_title ? `settles “${item.obligation_title}”` : null].filter(Boolean).join(" · ") || `Recorded by ${item.created_by}`}</small></div>
      {manage ? <select aria-label="Invoice status" value={item.status} disabled={busy} onChange={e => void run(() => api.updateInvoice(item.id, { status: e.target.value as Invoice["status"] }))}>{invoiceStatuses.map(s => <option key={s}>{s}</option>)}</select>
        : <Status value={item.overdue ? "Overdue" : item.status === "Paid" ? "Done" : item.status} />}
      {manage && <button className="wf-icon-link" aria-label="Delete invoice" disabled={busy} onClick={() => { if (window.confirm("Delete this invoice?")) void run(() => api.deleteInvoice(item.id)); }}><Trash2 size={16}/></button>}
    </li>)}</ul>}
  </div>;
}

/** Budget use across the portfolio, for the Reports page. */
export function SpendOverviewPanel({ onOpen }: { onOpen?: (id: number) => void }) {
  const [data, setData] = useState<SpendOverview | null>(null);
  const [level, setLevel] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    api.spend(level).then(d => { if (!cancelled) setData(d); }).catch(err => { if (!cancelled) setError(errorText(err, "Could not load spending")); });
    return () => { cancelled = true; };
  }, [level]);
  if (!data) return error ? <p className="form-error">{error}</p> : null;
  return <section className="panel spend-overview">
    <div className="panel-title"><div><h2>Contract value used</h2><p>Contracts with invoices, most used first. {data.counts.over ? `${data.counts.over} over budget, ` : ""}{data.counts.warning} above {data.warn_percent}%.</p></div>
      <select aria-label="Show" value={level} onChange={e => setLevel(e.target.value)}><option value="">All with invoices</option><option value="over">Over budget</option><option value="warning">Nearly used</option><option value="ok">Within budget</option></select></div>
    {data.totals.length > 0 && <div className="family-totals">{data.totals.map(t => <div key={t.currency}><small>{t.currency}</small><strong>{t.invoiced.toLocaleString()} of {t.budget.toLocaleString()} invoiced</strong><small>{t.paid.toLocaleString()} paid</small></div>)}</div>}
    <div className="table-wrap"><table className="wf-report">
      <thead><tr><th>Contract</th><th className="num">Value</th><th className="num">Invoiced</th><th className="num">Paid</th><th className="num">Remaining</th><th>Used</th></tr></thead>
      <tbody>{data.items.map(item => <tr key={item.contract_id} className={onOpen ? "click-row" : ""} onClick={() => onOpen?.(item.contract_id)}>
        <td><strong>{item.title}</strong><small className="wf-muted"> {item.vendor_name ?? ""}</small></td>
        <td className="num">{amountLabel(item.currency, item.budget)}</td><td className="num">{item.invoiced.toLocaleString()}</td>
        <td className="num">{item.paid.toLocaleString()}</td><td className={`num${item.remaining != null && item.remaining < 0 ? " negative" : ""}`}>{item.remaining != null ? item.remaining.toLocaleString() : "—"}</td>
        <td className="spend-cell"><SpendMeter spend={{ ...item, warn_percent: data.warn_percent }} /><small>{item.percent != null ? `${item.percent}%` : "no value"}</small></td>
      </tr>)}
      {!data.items.length && <tr><td colSpan={6} className="empty-cell">No invoices recorded{level ? " at this level" : ""}.</td></tr>}</tbody>
    </table></div>
  </section>;
}
