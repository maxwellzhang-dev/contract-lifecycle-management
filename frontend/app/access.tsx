"use client";

// Who can see a contract, and sharing it with extra people.

import { useEffect, useState } from "react";
import { Lock, Trash2, UserPlus } from "lucide-react";
import { api, can, type Contract, type DirectoryEntry, type Member, type User } from "./api";
import { errorText, initials, roleLabel } from "./shared";

export function SharingCard({ contract, user }: { contract: Contract; user: User }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [people, setPeople] = useState<DirectoryEntry[]>([]);
  const [choice, setChoice] = useState("");
  const [error, setError] = useState("");
  const manage = can(user, "contract.edit");

  useEffect(() => {
    let cancelled = false;
    api.members(contract.id).then(m => { if (!cancelled) setMembers(m); }).catch(err => { if (!cancelled) setError(errorText(err, "Could not load members")); });
    if (manage) api.directory().then(d => { if (!cancelled) setPeople(d); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [contract.id, manage]);

  async function run(action: () => Promise<Member[]>) {
    setError("");
    try { setMembers(await action()); setChoice(""); } catch (err) { setError(errorText(err, "Could not update access")); }
  }
  const candidates = people.filter(p => !members.some(m => m.id === p.id));
  return <section className="wf-card sharing-card">
    <h3><Lock size={15}/> Who can see this contract</h3>
    <p className="wf-hint">People with access to all contracts{contract.department ? <>, people in <b>{contract.department}</b></> : null}, the owner and uploader, its approvers and signers{members.length ? ", and:" : "."}</p>
    {members.length > 0 && <ul className="sharing-list">{members.map(m => <li key={m.id}>
      <span className="audit-avatar">{initials(m.name)}</span>
      <div><strong>{m.name}</strong><small>{roleLabel(m.role)}{m.department ? ` · ${m.department}` : ""}{m.added_by ? ` · added by ${m.added_by}` : ""}</small></div>
      {manage && <button className="icon-button" aria-label={`Remove ${m.name}`} onClick={() => void run(() => api.removeMember(contract.id, m.id))}><Trash2 size={15}/></button>}
    </li>)}</ul>}
    {manage && <div className="sharing-add">
      <select aria-label="Share with" value={choice} onChange={e => setChoice(e.target.value)}>
        <option value="">Share with someone…</option>
        {candidates.map(p => <option key={p.id} value={p.id}>{p.name} ({roleLabel(p.role)})</option>)}
      </select>
      <button className="secondary-button" disabled={!choice} onClick={() => void run(() => api.addMember(contract.id, Number(choice)))}><UserPlus size={14}/> Add</button>
    </div>}
    {error && <p className="form-error">{error}</p>}
  </section>;
}
