"use client";

// A main agreement with its SOWs, amendments, order forms and renewals.

import { useEffect, useState } from "react";
import { GitBranch, Link2, Unlink } from "lucide-react";
import { api, can, contractRelationships, type Contract, type Family, type FamilyNode, type User } from "./api";
import { Status, amountLabel, dateLabel, errorText } from "./shared";

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
    <header className="wf-card-head"><div><h3><GitBranch size={15}/> Related contracts</h3>
      <small>{single ? "Not linked to a main agreement, SOW or amendment." : family ? `${family.count} contract${family.count === 1 ? "" : "s"} in this agreement${family.hidden_count ? ` (${family.hidden_count} you cannot see)` : ""}` : "Loading…"}</small></div>
      {manage && !editing && <button className="secondary-button" onClick={() => setEditing(true)}><Link2 size={14}/> {contract.parent_id ? "Change link" : "Link to main agreement"}</button>}
    </header>
    {editing && <div className="family-edit">
      <select aria-label="Relationship" value={relationship} onChange={e => setRelationship(e.target.value)}>{contractRelationships.map(r => <option key={r}>{r}</option>)}</select>
      <span>of</span>
      <select aria-label="Main agreement" value={parent} onChange={e => setParent(e.target.value)}>
        <option value="">Choose a contract…</option>
        {candidates.map(c => <option key={c.id} value={c.id}>{c.title}{c.vendor_name ? ` · ${c.vendor_name}` : ""}</option>)}
      </select>
      <button className="primary-button" disabled={busy || !parent} onClick={() => void save(Number(parent))}>Link</button>
      {contract.parent_id && <button className="secondary-button" disabled={busy} onClick={() => void save(null)}><Unlink size={14}/> Unlink</button>}
      <button className="link-button" onClick={() => setEditing(false)}>Cancel</button>
    </div>}
    {family && !single && <>
      <div className="family-totals">
        <div><small>Total value</small><strong>{family.value_by_currency.length ? family.value_by_currency.map(v => amountLabel(v.currency, v.value)).join(" · ") : "—"}</strong></div>
        <div><small>Active</small><strong>{family.active_count} of {family.count}</strong></div>
        <div><small>Runs</small><strong>{dateLabel(family.first_start)} → {dateLabel(family.last_end)}</strong></div>
      </div>
      <ul className="family-tree"><FamilyRow node={family.root} current={contract.id} onOpen={onOpen} /></ul>
    </>}
    {error && <p className="form-error">{error}</p>}
  </section>;
}

function FamilyRow({ node, current, onOpen }: { node: FamilyNode; current: number; onOpen: (id: number) => void }) {
  return <li>
    <button className={node.id === current ? "family-node current" : "family-node"} disabled={node.id === current} onClick={() => onOpen(node.id)}>
      <span className="wf-badge">{node.relationship ?? "Main agreement"}</span>
      <div><strong>{node.title}</strong><small>{[node.reference_number, node.vendor_name, node.project_end_date ? `ends ${dateLabel(node.project_end_date)}` : null].filter(Boolean).join(" · ")}</small></div>
      <b>{node.value != null ? amountLabel(node.currency, node.value) : "—"}</b>
      <Status value={node.status} plain />
    </button>
    {node.children.length > 0 && <ul>{node.children.map(child => <FamilyRow key={child.id} node={child} current={current} onOpen={onOpen} />)}</ul>}
  </li>;
}
