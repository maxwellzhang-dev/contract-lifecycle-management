"use client";

// Background jobs, for administrators.

import { useEffect, useState } from "react";
import { RotateCcw, ServerCog } from "lucide-react";
import { api, type Job } from "./api";
import { dateTimeLabel, errorText } from "./shared";
import { t, tn } from "./i18n";

export function JobsPanel({ onOpenContract }: { onOpenContract?: (id: number) => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.jobs>> | null>(null);
  const [filter, setFilter] = useState<"" | Job["status"]>("");
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [ocr, setOcr] = useState<{ configured: boolean; scanned_without_text: number } | null>(null);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let cancelled = false;
    api.ocrStatus().then(s => { if (!cancelled) setOcr(s); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [tick]);
  async function backfill() {
    setError(""); setNotice("");
    try { const r = await api.ocrBackfill(); setNotice(tn(r.queued, "{n} scanned document queued for OCR.", "{n} scanned documents queued for OCR.")); setTick(n => n + 1); }
    catch (err) { setError(errorText(err, "Could not start OCR")); }
  }
  useEffect(() => {
    let cancelled = false;
    api.jobs(filter).then(d => { if (!cancelled) { setData(d); setError(""); } }).catch(err => { if (!cancelled) setError(errorText(err, "Could not load jobs")); });
    const timer = window.setTimeout(() => setTick(n => n + 1), 5000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [filter, tick]);
  async function retry(job: Job) {
    try { await api.retryJob(job.id); setTick(n => n + 1); } catch (err) { setError(errorText(err, "Could not retry")); }
  }
  const counts = data?.counts ?? {};
  return <section className="panel wf-card jobs-panel">
    <header className="wf-card-head"><div><h3><ServerCog size={16}/> {t("Background jobs")}</h3>
      <small>{t("AI extraction, OCR, emails, Teams posts and AI reviews run here and are retried automatically.")} {data ? tn(data.workers, "{n} worker in this API process.", "{n} workers in this API process.") : ""}</small></div></header>
    <div className="jobs-filter" role="radiogroup" aria-label={t("Job status")}>{(["", "queued", "running", "failed", "done"] as const).map(s =>
      <button key={s || "all"} role="radio" aria-checked={filter === s} className={filter === s ? "active" : ""} onClick={() => setFilter(s)}>{s ? t(`${s[0].toUpperCase()}${s.slice(1)}`) : t("All")}{s && counts[s] ? ` (${counts[s]})` : ""}</button>)}</div>
    {ocr && <p className="wf-hint ocr-line">{ocr.configured
      ? <>{t("OCR (Document Intelligence) is on.")} {ocr.scanned_without_text ? <>{tn(ocr.scanned_without_text, "{n} scanned document has no text yet.", "{n} scanned documents have no text yet.")} <button className="link-button" onClick={() => void backfill()}>{t("Read them now")}</button></> : t("Every scanned document has been read.")}</>
      : <>{t("OCR is off: scanned documents are only read from page images. Set AZURE_DOCINTEL_ENDPOINT and AZURE_DOCINTEL_KEY to turn it on.")}</>}</p>}
    {notice && <p className="save-message">{notice}</p>}
    {error && <p className="form-error">{error}</p>}
    {data && !data.items.length && <p className="wf-hint">{filter ? t("No jobs with status {status}.", { status: t(`${filter[0].toUpperCase()}${filter.slice(1)}`) }) : t("No jobs yet.")}</p>}
    <ul className="wf-list">{data?.items.map(job => <li key={job.id}>
      <span className={`job-status ${job.status}`}>{t(`${job.status[0].toUpperCase()}${job.status.slice(1)}`)}</span>
      <div><strong>{t(job.label)}{job.contract_id ? (onOpenContract ? <> · <button className="link-button" onClick={() => onOpenContract(job.contract_id!)}>{t("contract #{n}", { n: job.contract_id })}</button></> : ` · ${t("contract #{n}", { n: job.contract_id })}`) : null}{job.target ? ` · ${t("to {who}", { who: job.target })}` : ""}</strong>
        <small>#{job.id} · {t("attempt {n}/{max}", { n: job.attempts, max: job.max_attempts })} · {job.status === "queued" && job.attempts ? t("next try {when}", { when: dateTimeLabel(job.run_after) }) : t("updated {when}", { when: dateTimeLabel(job.updated_at) })}</small>
        {job.last_error && <p className="job-error">{job.last_error}</p>}</div>
      {job.status === "failed" && <button className="secondary-button" onClick={() => void retry(job)}><RotateCcw size={14}/> {t("Retry")}</button>}
    </li>)}</ul>
  </section>;
}
