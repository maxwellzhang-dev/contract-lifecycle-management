"use client";

// Calendar subscription (iCal feed for Outlook / Google) and the Teams channel integration.

import { useEffect, useState } from "react";
import { CalendarPlus, Check, Copy, MessagesSquare, Send } from "lucide-react";
import { api } from "./api";
import { dateTimeLabel, errorText } from "./shared";
import { t } from "./i18n";

export function CalendarModal({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<{ subscribed: boolean; created_at: string | null } | null>(null);
  const [link, setLink] = useState<{ url: string; webcal_url: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    api.calendarStatus().then(s => { if (!cancelled) setStatus(s); }).catch(err => { if (!cancelled) setError(errorText(err, "Could not load")); });
    return () => { cancelled = true; };
  }, []);
  async function create() {
    if (status?.subscribed && !window.confirm(t("Create a new link? Calendars using the current link stop updating until you subscribe again."))) return;
    setBusy(true); setError(""); setCopied(false);
    try { const created = await api.createCalendarLink(); setLink(created); setStatus({ subscribed: true, created_at: new Date().toISOString() }); }
    catch (err) { setError(errorText(err, "Could not create the link")); }
    finally { setBusy(false); }
  }
  async function revoke() {
    if (!window.confirm(t("Turn off the calendar link? Subscribed calendars stop updating."))) return;
    setBusy(true); setError("");
    try { await api.revokeCalendarLink(); setLink(null); setStatus({ subscribed: false, created_at: null }); }
    catch (err) { setError(errorText(err, "Could not turn off the link")); }
    finally { setBusy(false); }
  }
  async function copy() {
    if (!link) return;
    try { await navigator.clipboard.writeText(link.url); setCopied(true); } catch { /* clipboard blocked: the URL is selectable */ }
  }
  const outlook = link ? `https://outlook.office.com/calendar/0/addfromweb?url=${encodeURIComponent(link.url)}&name=Clause` : "";
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="upload-modal calendar-modal" onMouseDown={e => e.stopPropagation()}>
    <header><div><p className="eyebrow">{t("CALENDAR")}</p><h2>{t("Subscribe to key dates")}</h2>
      <p>{t("Contract ends, renewal dates and notice deadlines, open obligations and unpaid invoices for the contracts you can see. Outlook and Google refresh it every few hours.")}</p></div>
      <button type="button" onClick={onClose} aria-label={t("Close")}>×</button></header>
    {link ? <div className="calendar-link">
      <p className="wf-hint warn">{t("This private link works without signing in. Anyone who has it can read these dates, so don’t share it. It is shown only now.")}</p>
      <div className="calendar-url"><input readOnly value={link.url} aria-label={t("Calendar link")} onFocus={e => e.target.select()} />
        <button className="secondary-button" onClick={() => void copy()}>{copied ? <><Check size={14}/> {t("Copied")}</> : <><Copy size={14}/> {t("Copy")}</>}</button></div>
      <div className="wf-actions calendar-actions">
        <a className="primary-button" href={outlook} target="_blank" rel="noreferrer"><CalendarPlus size={15}/> {t("Add to Outlook")}</a>
        <a className="secondary-button" href={link.webcal_url}>{t("Open in calendar app")}</a>
      </div>
      <p className="wf-hint">{t("Google Calendar: Other calendars → From URL, then paste the link.")}</p>
    </div> : <p className="wf-hint">{status?.subscribed ? t("You have a calendar link (created {when}). For safety it can’t be shown again; create a new one to subscribe another calendar.", { when: dateTimeLabel(status.created_at) }) : t("You don’t have a calendar link yet.")}</p>}
    {error && <p className="form-error">{error}</p>}
    <footer>{status?.subscribed && <button className="danger-button" disabled={busy} onClick={() => void revoke()}>{t("Turn off link")}</button>}
      <button className="secondary-button" onClick={onClose}>{t("Close")}</button>
      <button className="primary-button" disabled={busy || !status} onClick={() => void create()}>{status?.subscribed ? t("Create new link") : t("Create calendar link")}</button></footer>
  </section></div>;
}

export function TeamsCard() {
  const [status, setStatus] = useState<{ configured: boolean; events: string[] } | null>(null);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let cancelled = false;
    api.integrations().then(s => { if (!cancelled) setStatus(s.teams); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, []);
  async function test() {
    setMessage("");
    try { await api.teamsTest(); setMessage(t("Test message queued. It appears in the channel within a few seconds.")); }
    catch (err) { setMessage(errorText(err, "Could not send")); }
  }
  if (!status) return null;
  return <section className="panel wf-card">
    <header className="wf-card-head"><div><h3><MessagesSquare size={16}/> Microsoft Teams</h3>
      <small>{status.configured ? t("Posting to a channel: {events}.", { events: status.events.join(", ") }) : t("Off. Set TEAMS_WEBHOOK_URL to a Teams Workflows webhook to post approvals, reminders and alerts to a channel.")}</small></div>
      {status.configured && <button className="secondary-button" onClick={() => void test()}><Send size={14}/> {t("Send test message")}</button>}</header>
    {status.configured && <p className="wf-hint">{t("Everyone in the channel sees the contract titles in these posts, whatever their access in Clause.")}</p>}
    {message && <p className="wf-hint">{message}</p>}
  </section>;
}
