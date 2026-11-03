"use client";

// Background jobs, for administrators.

import { useEffect, useState } from "react";
import { RotateCcw, ServerCog } from "lucide-react";
import { api, type Job } from "./api";
import { dateTimeLabel, errorText } from "./shared";

export function JobsPanel({ onOpenContract }: { onOpenContract?: (id: number) => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.jobs>> | null>(null);
  const [filter, setFilter] = useState<"" | Job["status"]>("");
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    api.jobs(filter).then(d => { if (!cancelled) { setData(d); setError(""); } }).catch(err => { if (!cancelled) setError(errorText(err, "Could not load jobs")); });
    const timer = window.setTimeout(() => setTick(t => t + 1), 5000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [filter, tick]);
  async function retry(job: Job) {
    try { await api.retryJob(job.id); setTick(t => t + 1); } catch (err) { setError(errorText(err, "Could not retry")); }
  }
  const counts = data?.counts ?? {};
  return <section className="panel wf-card jobs-panel">
    <header className="wf-card-head"><div><h3><ServerCog size={16}/> Background jobs</h3>
      <small>AI extraction, OCR, emails and AI reviews run here and are retried automatically. {data ? `${data.workers} worker${data.workers === 1 ? "" : "s"} in this API process.` : ""}</small></div></header>
    <div className="jobs-filter" role="radiogroup" aria-label="Job status">{(["", "queued", "running", "failed", "done"] as const).map(s =>
      <button key={s || "all"} role="radio" aria-checked={filter === s} className={filter === s ? "active" : ""} onClick={() => setFilter(s)}>{s ? `${s[0].toUpperCase()}${s.slice(1)}` : "All"}{s && counts[s] ? ` (${counts[s]})` : ""}</button>)}</div>
    {error && <p className="form-error">{error}</p>}
    {data && !data.items.length && <p className="wf-hint">No jobs{filter ? ` with status ${filter}` : " yet"}.</p>}
    <ul className="wf-list">{data?.items.map(job => <li key={job.id}>
      <span className={`job-status ${job.status}`}>{job.status}</span>
      <div><strong>{job.label}{job.contract_id ? <> · <button className="link-button" onClick={() => onOpenContract?.(job.contract_id!)}>contract #{job.contract_id}</button></> : null}{job.target ? ` · to ${job.target}` : ""}</strong>
        <small>#{job.id} · attempt {job.attempts}/{job.max_attempts} · {job.status === "queued" && job.attempts ? `next try ${dateTimeLabel(job.run_after)}` : `updated ${dateTimeLabel(job.updated_at)}`}</small>
        {job.last_error && <p className="job-error">{job.last_error}</p>}</div>
      {job.status === "failed" && <button className="secondary-button" onClick={() => void retry(job)}><RotateCcw size={14}/> Retry</button>}
    </li>)}</ul>
  </section>;
}
