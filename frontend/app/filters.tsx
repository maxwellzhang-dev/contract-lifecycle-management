"use client";

import { KeyboardEvent, useState } from "react";
import { X } from "lucide-react";
import { API_BASE, type Contract } from "./api";
import { t } from "./i18n";

export type Filters = {
  status: string;
  vendor: string;
  department: string;
  owner: string;
  tag: string;
  currency: string;
  minValue: string;
  maxValue: string;
  endFrom: string;
  endTo: string;
};

export const emptyFilters: Filters = {
  status: "", vendor: "", department: "", owner: "", tag: "", currency: "", minValue: "", maxValue: "", endFrom: "", endTo: "",
};

export const totalValue = (c: Contract) => (c.one_time_amount ?? 0) + (c.optional_amount ?? 0);

/** Client-side mirror of the API's contract filters, so the table and the CSV export agree. */
export function filterContracts(contracts: Contract[], search: string, f: Filters) {
  const needle = search.trim().toLowerCase();
  const min = f.minValue === "" ? null : Number(f.minValue);
  const max = f.maxValue === "" ? null : Number(f.maxValue);
  return contracts.filter(c => {
    if (needle) {
      const text = `${c.vendor_name} ${c.project_name} ${c.title} ${c.reference_number} ${c.owner} ${(c.tags ?? []).join(" ")}`.toLowerCase();
      if (!text.includes(needle)) return false;
    }
    if (f.status && c.status !== f.status) return false;
    if (f.vendor && c.vendor_name !== f.vendor) return false;
    if (f.department && c.department !== f.department) return false;
    if (f.owner && c.owner !== f.owner) return false;
    if (f.currency && c.currency !== f.currency) return false;
    if (f.tag && !(c.tags ?? []).some(tag => tag.toLowerCase() === f.tag.toLowerCase())) return false;
    if (min !== null && totalValue(c) < min) return false;
    if (max !== null && totalValue(c) > max) return false;
    if (f.endFrom && (!c.project_end_date || c.project_end_date < f.endFrom)) return false;
    if (f.endTo && (!c.project_end_date || c.project_end_date > f.endTo)) return false;
    return true;
  });
}

export function exportUrl(search: string, f: Filters) {
  const params = new URLSearchParams();
  const add = (key: string, value: string) => { if (value) params.set(key, value); };
  add("search", search.trim()); add("status", f.status); add("vendor", f.vendor); add("department", f.department);
  add("owner", f.owner); add("tag", f.tag); add("currency", f.currency); add("min_value", f.minValue);
  add("max_value", f.maxValue); add("end_from", f.endFrom); add("end_to", f.endTo);
  const query = params.toString();
  return `${API_BASE}/api/export/contracts.csv${query ? `?${query}` : ""}`;
}

function distinct(values: (string | null | undefined)[]) {
  return [...new Set(values.filter((v): v is string => !!v))].sort((a, b) => a.localeCompare(b));
}

export function activeFilterCount(f: Filters) {
  return Object.values(f).filter(Boolean).length;
}

export function AdvancedFilters({ contracts, filters, setFilters }: { contracts: Contract[]; filters: Filters; setFilters: (f: Filters) => void }) {
  const set = (key: keyof Filters) => (value: string) => setFilters({ ...filters, [key]: value });
  const options = {
    vendor: distinct(contracts.map(c => c.vendor_name)),
    department: distinct(contracts.map(c => c.department)),
    owner: distinct(contracts.map(c => c.owner)),
    tag: distinct(contracts.flatMap(c => c.tags ?? [])),
    currency: distinct(contracts.map(c => c.currency)),
  };
  const select = (key: "vendor" | "department" | "owner" | "tag" | "currency", label: string) =>
    <label className="wf-field">{t(label)}<select value={filters[key]} onChange={e => set(key)(e.target.value)}><option value="">{t("Any")}</option>{options[key].map(v => <option key={v}>{v}</option>)}</select></label>;
  return <div className="wf-filters panel">
    {select("vendor", "Vendor")}
    {select("department", "Department")}
    {select("owner", "Owner")}
    {select("tag", "Tag")}
    {select("currency", "Currency")}
    <label className="wf-field">{t("Total value from")}<input type="number" min="0" inputMode="decimal" value={filters.minValue} onChange={e => set("minValue")(e.target.value)} /></label>
    <label className="wf-field">{t("Total value to")}<input type="number" min="0" inputMode="decimal" value={filters.maxValue} onChange={e => set("maxValue")(e.target.value)} /></label>
    <label className="wf-field">{t("Ends on or after")}<input type="date" value={filters.endFrom} onChange={e => set("endFrom")(e.target.value)} /></label>
    <label className="wf-field">{t("Ends on or before")}<input type="date" value={filters.endTo} onChange={e => set("endTo")(e.target.value)} /></label>
    <button type="button" className="secondary-button" disabled={!activeFilterCount(filters)} onClick={() => setFilters(emptyFilters)}>{t("Clear filters")}</button>
  </div>;
}

export function Tags({ tags }: { tags?: string[] }) {
  if (!tags?.length) return null;
  return <span className="wf-tags">{tags.map(tag => <span key={tag} className="wf-tag">{tag}</span>)}</span>;
}

export function TagEditor({ value, onChange, disabled, suggestions }: { value: string[]; onChange: (tags: string[]) => void; disabled?: boolean; suggestions: string[] }) {
  const [draft, setDraft] = useState("");
  function add(raw: string) {
    const tag = raw.trim().replace(/\s+/g, " ").slice(0, 30);
    if (tag && !value.some(v => v.toLowerCase() === tag.toLowerCase()) && value.length < 10) onChange([...value, tag]);
    setDraft("");
  }
  function key(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") { event.preventDefault(); add(draft); }
    if (event.key === "Backspace" && !draft && value.length) onChange(value.slice(0, -1));
  }
  return <div className="wf-tag-editor">
    {value.map(tag => <span key={tag} className="wf-tag">{tag}{!disabled && <button type="button" aria-label={t("Remove tag {tag}", { tag })} onClick={() => onChange(value.filter(x => x !== tag))}><X size={12}/></button>}</span>)}
    {!disabled && value.length < 10 && <input list="tag-suggestions" value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={key} onBlur={() => draft && add(draft)} placeholder={value.length ? t("Add tag") : t("Add tags, press Enter")} aria-label={t("Add tag")} />}
    <datalist id="tag-suggestions">{suggestions.filter(s => !value.includes(s)).map(s => <option key={s} value={s} />)}</datalist>
  </div>;
}
