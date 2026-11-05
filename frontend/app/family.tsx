"use client";

// A main agreement with its SOWs, amendments, order forms and renewals.

import { useEffect, useState } from "react";
import { GitBranch, Link2, Unlink } from "lucide-react";
import { api, can, contractRelationships, type Contract, type Family, type FamilyNode, type User } from "./api";
import { Status, amountLabel, dateLabel, errorText } from "./shared";
import { t, tn } from "./i18n";

function descendants(node: FamilyNode, into = new Set<number>()) {
  for (const child of node.children) { into.add(child.id); descendants(child, into); }
  return into;
}

function find(node: FamilyNode, id: number): FamilyNode | null {
  if (node.id === id) return node;
  for (const child of node.children) { const hit = find(child, id); if (hit) return hit; }
  return null;
}

export function FamilyCard({ contract, user, contracts, onChanged, onOpen }: {
  contract: Contract; user: User; contracts: Contract[]; onChanged: (c: Contract) => Promise<void>; onOpen: (id: number) => void;
}) {
  const [family, setFamily] = useState<Family | null>(null);
  const [editing, setEditing] = useState(false);
  const [parent, setParent] = useState("");
  const [relationship, setRelationship] = useState<string>(contract.relationship ?? "SOW");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const manage = can(user, "contract.edit");

  useEffect(() => {
    let cancelled = false;
    api.family(contract.id).then(f => { if (!cancelled) setFamily(f); }).catch(err => { if (!cancelled) setError(errorText(err, "Could not load related contracts")); });
    return () => { cancelled = true; };
  }, [contract.id, contract.parent_id, contract.relationship, contract.child_count]);

  async function save(parentId: number | null) {
    setBusy(true); setError("");
    try { await onChanged(await api.update(contract.id, { parent_id: parentId, relationship: parentId ? relationship : null })); setEditing(false); setParent(""); }
    catch (err) { setError(errorText(err, "Could not link the contract")); }
    finally { setBusy(false); }
  }

  const self = family ? find(family.root, contract.id) : null;
  const blocked = self ? descendants(self) : new Set<number>();
  const candidates = contracts.filter(c => c.id !== contract.id && !blocked.has(c.id));
  const single = family && family.count === 1 && !family.hidden_count;
  return <section className="wf-card family-card">
    <header className="wf-card-head"><div><h3><GitBranch size={15}/> {t("Related contracts")}</h3>
      <small>{single ? t("Not linked to a main agreement, SOW or amendment.") : family ? tn(family.count, "{n} contract in this agreement", "{n} contracts in this agreement") + (family.hidden_count ? ` ${t("({n} you cannot see)", { n: family.hidden_count })}` : "") : t("Loading…")}</small></div>
      {manage && !editing && <button className="secondary-button" onClick={() => setEditing(true)}><Link2 size={14}/> {contract.parent_id ? t("Change link") : t("Link to main agreement")}</button>}
    </header>
    {editing && <div className="family-edit">
      <select aria-label={t("Relationship")} value={relationship} onChange={e => setRelationship(e.target.value)}>{contractRelationships.map(r => <option key={r} value={r}>{t(r)}</option>)}</select>
      <span>{t("of")}</span>
      <select aria-label={t("Main agreement")} value={parent} onChange={e => setParent(e.target.value)}>
        <option value="">{t("Choose a contract…")}</option>
        {candidates.map(c => <option key={c.id} value={c.id}>{c.title}{c.vendor_name ? ` · ${c.vendor_name}` : ""}</option>)}
      </select>
      <button className="primary-button" disabled={busy || !parent} onClick={() => void save(Number(parent))}>{t("Link")}</button>
      {contract.parent_id && <button className="secondary-button" disabled={busy} onClick={() => void save(null)}><Unlink size={14}/> {t("Unlink")}</button>}
      <button className="link-button" onClick={() => setEditing(false)}>{t("Cancel")}</button>
    </div>}
    {family && !single && <>
      <div className="family-totals">
        <div><small>{t("Total value")}</small><strong>{family.value_by_currency.length ? family.value_by_currency.map(v => amountLabel(v.currency, v.value)).join(" · ") : "—"}</strong></div>
        {family.invoiced_by_currency.length > 0 && <div><small>{t("Invoiced")}</small><strong>{family.invoiced_by_currency.map(v => amountLabel(v.currency, v.value)).join(" · ")}</strong></div>}
        <div><small>{t("Active")}</small><strong>{t("{active} of {n}", { active: family.active_count, n: family.count })}</strong></div>
        <div><small>{t("Runs")}</small><strong>{dateLabel(family.first_start)} → {dateLabel(family.last_end)}</strong></div>
      </div>
      <ul className="family-tree"><FamilyRow node={family.root} current={contract.id} onOpen={onOpen} /></ul>
    </>}
    {error && <p className="form-error">{error}</p>}
  </section>;
}

function FamilyRow({ node, current, onOpen }: { node: FamilyNode; current: number; onOpen: (id: number) => void }) {
  return <li>
    <button className={node.id === current ? "family-node current" : "family-node"} disabled={node.id === current} onClick={() => onOpen(node.id)}>
      <span className="wf-badge">{t(node.relationship ?? "Main agreement")}</span>
      <div><strong>{node.title}</strong><small>{[node.reference_number, node.vendor_name, node.project_end_date ? t("ends {date}", { date: dateLabel(node.project_end_date) }) : null].filter(Boolean).join(" · ")}</small></div>
      <b>{node.value != null ? amountLabel(node.currency, node.value) : "—"}</b>
      <Status value={node.status} plain />
    </button>
    {node.children.length > 0 && <ul>{node.children.map(child => <FamilyRow key={child.id} node={child} current={current} onOpen={onOpen} />)}</ul>}
  </li>;
}
