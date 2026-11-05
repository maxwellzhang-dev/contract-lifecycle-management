"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { FilePlus2, Pencil } from "lucide-react";
import { api, type Contract, type Template } from "./api";
import { SectionHeader, dateLabel, errorText } from "./shared";
import { t } from "./i18n";

const MISSING = "[to be confirmed]"; // printed into documents, so not translated

function renderTemplate(body: string, values: Record<string, unknown>) {
  const all: Record<string, unknown> = { ...values, today: new Date().toISOString().slice(0, 10) };
  return body.replace(/\{\{\s*([a-z_]+)\s*\}\}/g, (_, key: string) => {
    const value = all[key];
    if (value === undefined || value === null || value === "") return MISSING;
    if ((key === "one_time_amount" || key === "optional_amount") && typeof value === "number")
      return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return String(value);
  });
}

type FormValues = Record<string, string>;
const fields: { key: string; label: string; type?: string; wide?: boolean; long?: boolean }[] = [
  { key: "title", label: "Contract title *", wide: true }, { key: "vendor_name", label: "Vendor" },
  { key: "reference_number", label: "Reference number" }, { key: "project_name", label: "Project / scope", wide: true },
  { key: "project_start_date", label: "Start date", type: "date" }, { key: "project_end_date", label: "End date", type: "date" },
  { key: "currency", label: "Currency" }, { key: "one_time_amount", label: "One-time amount", type: "number" },
  { key: "optional_amount", label: "Optional amount", type: "number" }, { key: "department", label: "Department" },
  { key: "owner", label: "Contract owner" }, { key: "phase", label: "Phase" },
  { key: "vendor_contact", label: "Vendor contact" }, { key: "internal_contact", label: "Internal contact" },
  { key: "deliverables", label: "Deliverables / services", wide: true, long: true },
  { key: "payment_terms", label: "Payment terms", wide: true, long: true },
];

function toPayload(values: FormValues) {
  const payload: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(values)) {
    if (value === "") continue;
    payload[key] = key.endsWith("_amount") ? Number(value) : value;
  }
  return payload;
}

export function NewContractModal({ onClose, onCreated }: { onClose: () => void; onCreated: (c: Contract) => Promise<void> }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState<number | "">("");
  const [values, setValues] = useState<FormValues>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { api.templates().then(setTemplates).catch(err => setError(errorText(err, "Could not load templates"))); }, []);
  const template = templates.find(t => t.id === templateId);
  const merged = useMemo(() => ({ ...(template?.defaults ?? {}), ...Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "")) }), [template, values]);
  const preview = template ? renderTemplate(template.body, toPayload(merged as FormValues)) : "";

  function choose(id: number | "") {
    setTemplateId(id);
    const next = templates.find(t => t.id === id);
    // Show the template's defaults in the form so people can see and change them.
    setValues(current => ({ ...Object.fromEntries(Object.entries(next?.defaults ?? {}).map(([k, v]) => [k, current[k] || v])), ...Object.fromEntries(Object.entries(current).filter(([, v]) => v)) }));
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { await onCreated(await api.createContract({ ...toPayload(values), ...(templateId ? { template_id: templateId } : {}) })); }
    catch (err) { setError(errorText(err, "Could not create the contract")); setBusy(false); }
  }

  return <div className="modal-backdrop" onMouseDown={onClose}><form className="upload-modal wf-new-contract" onSubmit={submit} onMouseDown={e => e.stopPropagation()}>
    <header><div><p className="eyebrow">{t("NEW CONTRACT")}</p><h2>{t("Create a contract")}</h2><p>{t("Start from a template to generate a Word document, or create a record without a document.")}</p></div><button type="button" onClick={onClose} aria-label={t("Close")}>×</button></header>
    <div className="wf-template-picker">
      <button type="button" className={templateId === "" ? "active" : ""} onClick={() => choose("")}><strong>{t("Blank record")}</strong><small>{t("No document. Upload one later from the Versions tab.")}</small></button>
      {templates.map(tpl => <button type="button" key={tpl.id} className={templateId === tpl.id ? "active" : ""} onClick={() => choose(tpl.id)}><strong>{tpl.name}</strong><small>{tpl.description ?? t("Template")}</small></button>)}
    </div>
    <div className="wf-new-layout">
      <div className="edit-grid">{fields.map(f => <label key={f.key} className={f.wide ? "wide" : ""}><span>{t(f.label)}</span>
        {f.long ? <textarea value={values[f.key] ?? ""} onChange={e => setValues({ ...values, [f.key]: e.target.value })} /> :
          <input type={f.type ?? "text"} required={f.key === "title"} value={values[f.key] ?? ""} onChange={e => setValues({ ...values, [f.key]: e.target.value })} />}</label>)}</div>
      {template && <aside className="wf-preview"><h3>{t("Document preview")}</h3><pre>{preview}</pre></aside>}
    </div>
    {error && <p className="form-error">{error}</p>}
    <footer><button type="button" className="secondary-button" onClick={onClose}>{t("Cancel")}</button><button className="primary-button" disabled={busy || !values.title?.trim()}>{busy ? t("Creating…") : template ? t("Create and generate document") : t("Create contract")}</button></footer>
  </form></div>;
}

const emptyTemplate = { name: "", description: "", body: "", defaults: {} as Record<string, string> };

export function TemplatesView() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [meta, setMeta] = useState<{ placeholders: string[]; default_fields: string[] }>({ placeholders: [], default_fields: [] });
  const [editing, setEditing] = useState<(typeof emptyTemplate & { id?: number }) | null>(null);
  const [message, setMessage] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.templates(true), api.templatePlaceholders()]).then(([t, m]) => { if (!cancelled) { setTemplates(t); setMeta(m); } })
      .catch(err => { if (!cancelled) setMessage(errorText(err, "Could not load templates")); });
    return () => { cancelled = true; };
  }, [tick]);

  async function save(event: FormEvent) {
    event.preventDefault(); if (!editing) return; setMessage("");
    const defaults = Object.fromEntries(Object.entries(editing.defaults).filter(([, v]) => v.trim()));
    const payload = { name: editing.name, description: editing.description, body: editing.body, defaults };
    try {
      if (editing.id) await api.updateTemplate(editing.id, payload); else await api.createTemplate(payload);
      setEditing(null); setTick(n => n + 1); setMessage(t("Template saved."));
    } catch (err) { setMessage(errorText(err, "Could not save")); }
  }
  async function toggle(tpl: Template) {
    try { await api.updateTemplate(tpl.id, { active: !tpl.active }); setTick(x => x + 1); } catch (err) { setMessage(errorText(err, "Could not update")); }
  }
  function insert(name: string) {
    if (editing) setEditing({ ...editing, body: `${editing.body}{{${name}}}` });
  }

  return <>
    <SectionHeader eyebrow={t("TEMPLATES")} title={t("Contract templates")} copy={t("Text with {{placeholders}} that becomes a Word document when someone creates a contract from it.")} />
    {!editing && <button className="primary-button wf-new-template" onClick={() => setEditing({ ...emptyTemplate, defaults: {} })}><FilePlus2 size={16}/> {t("New template")}</button>}
    {message && <p className="save-message">{message}</p>}
    {editing && <form className="panel wf-card" onSubmit={save}>
      <h3>{editing.id ? t("Edit template") : t("New template")}</h3>
      <div className="wf-grid">
        <label className="wf-field">{t("Name")}<input required value={editing.name} onChange={e => setEditing({ ...editing, name: e.target.value })} /></label>
        <label className="wf-field">{t("Description")}<input value={editing.description} onChange={e => setEditing({ ...editing, description: e.target.value })} /></label>
      </div>
      <label className="wf-field">{t("Template text")}<textarea className="wf-template-body" required value={editing.body} onChange={e => setEditing({ ...editing, body: e.target.value })} placeholder={t("# Heading\nParagraph text with {{vendor_name}}…")} /></label>
      <div className="wf-chain"><small className="wf-hint">{t("Insert:")}</small>{meta.placeholders.map(p => <button type="button" key={p} className="wf-chip" onClick={() => insert(p)}>{`{{${p}}}`}</button>)}</div>
      <p className="wf-hint">{t("Start a paragraph with “# ” to make it a heading. Separate paragraphs with a blank line. Empty values print as “{missing}”.", { missing: MISSING })}</p>
      <h4 className="wf-subhead">{t("Default values for new contracts")}</h4>
      <div className="wf-grid">{meta.default_fields.map(f => <label key={f} className="wf-field">{t(f.replace(/_/g, " "))}<input value={editing.defaults[f] ?? ""} onChange={e => setEditing({ ...editing, defaults: { ...editing.defaults, [f]: e.target.value } })} /></label>)}</div>
      <div className="wf-actions"><button type="button" className="secondary-button" onClick={() => setEditing(null)}>{t("Cancel")}</button><button className="primary-button">{t("Save template")}</button></div>
    </form>}
    <section className="panel"><ul className="wf-list">{templates.map(tpl => <li key={tpl.id} className={tpl.active ? "" : "inactive"}>
      <div><strong>{tpl.name}</strong><small>{t("{description} · {n} placeholders · updated {date}", { description: tpl.description ?? t("No description"), n: tpl.placeholders.length, date: dateLabel(tpl.updated_at) })}{tpl.active ? "" : ` · ${t("inactive")}`}</small></div>
      <button className="secondary-button" onClick={() => setEditing({ id: tpl.id, name: tpl.name, description: tpl.description ?? "", body: tpl.body, defaults: { ...tpl.defaults } })}><Pencil size={14}/> {t("Edit")}</button>
      <button className={tpl.active ? "danger-button" : "secondary-button"} onClick={() => void toggle(tpl)}>{tpl.active ? t("Deactivate") : t("Reactivate")}</button>
    </li>)}</ul></section>
  </>;
}
