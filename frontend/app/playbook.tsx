"use client";

import { FormEvent, useEffect, useState } from "react";
import { BookCheck, Check, Copy, Pencil, Plus, Trash2 } from "lucide-react";
import { api, can, type PlaybookCheck, type PlaybookCounts, type PlaybookRule, type PlaybookStatus, type Severity, type User } from "./api";
import { SectionHeader, dateLabel, errorText } from "./shared";

export const playbookStatusLabels: Record<PlaybookStatus, string> = {
  meets: "Meets standard", fallback: "Fallback only", deviates: "Deviates", missing: "Missing", not_applicable: "Not applicable",
};

type Draft = { id?: number; title: string; category: string; severity: Severity; standard: string; fallback: string; guidance: string };
const emptyDraft: Draft = { title: "", category: "Liability", severity: "medium", standard: "", fallback: "", guidance: "" };

export function PlaybookView({ user }: { user: User }) {
  const [rules, setRules] = useState<PlaybookRule[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [maxActive, setMaxActive] = useState(60);
  const [editing, setEditing] = useState<Draft | null>(null);
  const [message, setMessage] = useState("");
  const [tick, setTick] = useState(0);
  const manage = can(user, "playbook.manage");
  useEffect(() => {
    let cancelled = false;
    api.playbook().then(data => { if (!cancelled) { setRules(data.rules); setCategories(data.categories); setMaxActive(data.max_active); } })
      .catch(err => { if (!cancelled) setMessage(errorText(err, "Could not load the playbook")); });
    return () => { cancelled = true; };
  }, [tick]);

  async function save(event: FormEvent) {
    event.preventDefault(); if (!editing) return; setMessage("");
    const { id, ...values } = editing;
    try {
      if (id) await api.updateRule(id, values); else await api.createRule(values);
      setEditing(null); setTick(t => t + 1); setMessage("Rule saved. The next AI review checks contracts against it.");
    } catch (err) { setMessage(errorText(err, "Could not save the rule")); }
  }
  async function toggle(rule: PlaybookRule) {
    setMessage("");
    try { await api.updateRule(rule.id, { active: !rule.active }); setTick(t => t + 1); } catch (err) { setMessage(errorText(err, "Could not update the rule")); }
  }
  async function remove(rule: PlaybookRule) {
    if (!window.confirm(`Delete the rule “${rule.title}”? Past reviews keep their results.`)) return;
    try { await api.deleteRule(rule.id); setTick(t => t + 1); } catch (err) { setMessage(errorText(err, "Could not delete the rule")); }
  }
  const active = rules.filter(r => r.active).length;

  return <>
    <SectionHeader eyebrow="LEGAL PLAYBOOK" title="Clause playbook" copy="Your standard positions. Every AI review checks the contract against each active rule, flags deviations and proposes wording." />
    {manage && !editing && <button className="primary-button wf-new-template" onClick={() => setEditing({ ...emptyDraft })}><Plus size={16}/> New rule</button>}
    {message && <p className="save-message">{message}</p>}
    {editing && <form className="panel wf-card" onSubmit={save}>
      <h3>{editing.id ? "Edit rule" : "New rule"}</h3>
      <div className="wf-grid">
        <label className="wf-field">Rule<input required maxLength={120} value={editing.title} onChange={e => setEditing({ ...editing, title: e.target.value })} placeholder="e.g. Liability cap" /></label>
        <label className="wf-field">Category<select value={editing.category} onChange={e => setEditing({ ...editing, category: e.target.value })}>{categories.map(c => <option key={c}>{c}</option>)}</select></label>
        <label className="wf-field">Importance<select value={editing.severity} onChange={e => setEditing({ ...editing, severity: e.target.value as Severity })}><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
      </div>
      <label className="wf-field">Standard position<textarea required maxLength={1500} value={editing.standard} onChange={e => setEditing({ ...editing, standard: e.target.value })} placeholder="What we expect, e.g. the supplier's liability is capped at no less than 12 months of fees." /></label>
      <label className="wf-field">Acceptable fallback (optional)<textarea maxLength={1000} value={editing.fallback} onChange={e => setEditing({ ...editing, fallback: e.target.value })} placeholder="What we can still accept, and when." /></label>
      <label className="wf-field">Preferred wording (optional)<textarea maxLength={1500} value={editing.guidance} onChange={e => setEditing({ ...editing, guidance: e.target.value })} placeholder="Clause text the AI should base its suggested changes on." /></label>
      <div className="wf-actions"><button type="button" className="secondary-button" onClick={() => setEditing(null)}>Cancel</button><button className="primary-button">Save rule</button></div>
    </form>}
    <p className="wf-hint">{active} of {rules.length} rule{rules.length === 1 ? "" : "s"} active{active >= maxActive ? ` (the limit is ${maxActive})` : ""}.</p>
    <section className="playbook-list">{rules.map(rule => <article key={rule.id} className={`panel playbook-rule ${rule.active ? "" : "inactive"}`}>
      <header><div><span className={`ai-sev ${rule.severity}`}>{rule.severity}</span><span className="wf-badge">{rule.category}</span><h3>{rule.title}</h3></div>
        {manage && <div className="playbook-actions">
          <button className="secondary-button" onClick={() => setEditing({ id: rule.id, title: rule.title, category: rule.category, severity: rule.severity, standard: rule.standard, fallback: rule.fallback ?? "", guidance: rule.guidance ?? "" })}><Pencil size={14}/> Edit</button>
          <button className="secondary-button" onClick={() => void toggle(rule)}>{rule.active ? "Turn off" : "Turn on"}</button>
          <button className="icon-button" aria-label={`Delete ${rule.title}`} onClick={() => void remove(rule)}><Trash2 size={15}/></button>
        </div>}</header>
      <dl className="playbook-terms">
        <div><dt>Standard</dt><dd>{rule.standard}</dd></div>
        {rule.fallback && <div><dt>Fallback</dt><dd>{rule.fallback}</dd></div>}
        {rule.guidance && <div><dt>Preferred wording</dt><dd className="playbook-wording">{rule.guidance}</dd></div>}
      </dl>
      <small className="wf-hint">{rule.active ? "Active" : "Off"} · updated {dateLabel(rule.updated_at)}{rule.created_by ? ` · added by ${rule.created_by}` : ""}</small>
    </article>)}</section>
  </>;
}

/** Playbook results inside an AI review. */
export function PlaybookResults({ checks }: { checks: PlaybookCheck[] }) {
  const [copied, setCopied] = useState<number | null>(null);
  const issues = checks.filter(c => c.status === "deviates" || c.status === "missing");
  async function copy(check: PlaybookCheck) {
    try { await navigator.clipboard.writeText(check.suggested_language ?? ""); setCopied(check.rule_id); } catch { /* clipboard blocked */ }
  }
  return <>
    <h4 className="wf-subhead"><BookCheck size={15}/> Playbook check ({issues.length ? `${issues.length} to negotiate` : "no deviations"})</h4>
    <PlaybookSummary counts={countChecks(checks)} />
    <ul className="playbook-checks">{checks.map(check => <li key={check.rule_id} className={check.status}>
      <span className={`playbook-status ${check.status}`}>{playbookStatusLabels[check.status]}</span>
      <div>
        <strong>{check.title}</strong> <span className={`ai-sev ${check.severity}`}>{check.severity}</span>
        <p>{check.finding}</p>
        {check.quote && <blockquote className="ai-quote">“{check.quote}”</blockquote>}
        {(check.status === "deviates" || check.status === "missing") && <details><summary>Our standard</summary><p>{check.standard}</p>{check.fallback && <p><b>Fallback:</b> {check.fallback}</p>}</details>}
        {check.suggested_language && <div className="playbook-suggestion"><small>Suggested wording</small><p>{check.suggested_language}</p>
          <button className="link-button" onClick={() => void copy(check)}>{copied === check.rule_id ? <><Check size={13}/> Copied</> : <><Copy size={13}/> Copy</>}</button></div>}
      </div>
    </li>)}</ul>
  </>;
}

export function countChecks(checks: PlaybookCheck[]): PlaybookCounts {
  const counts: PlaybookCounts = { meets: 0, fallback: 0, deviates: 0, missing: 0, not_applicable: 0 };
  for (const check of checks) counts[check.status] += 1;
  return counts;
}

export function PlaybookSummary({ counts }: { counts: PlaybookCounts }) {
  return <div className="playbook-summary">{(["deviates", "missing", "fallback", "meets", "not_applicable"] as PlaybookStatus[]).filter(s => counts[s]).map(s =>
    <span key={s} className={`playbook-status ${s}`}>{counts[s]} {playbookStatusLabels[s].toLowerCase()}</span>)}</div>;
}
