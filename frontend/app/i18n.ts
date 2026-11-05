// Interface language (English / 简体中文).
//
// Strings are written in English in the components and wrapped in t(); zh.ts maps each
// English string to Chinese. Placeholders such as {name} are filled from the second argument.
// The language is remembered per browser. Data (contract fields, notes, audit text and messages
// from the API) is shown as stored; AI answers follow the chosen language.

import { useSyncExternalStore } from "react";
import { zh } from "./zh";

export type Locale = "en" | "zh";
export const locales: { value: Locale; label: string }[] = [{ value: "en", label: "English" }, { value: "zh", label: "简体中文" }];
const STORAGE_KEY = "clause.locale";

let current: Locale = "en";
const listeners = new Set<() => void>();

function stored(): Locale | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "zh" || value === "en" ? value : null;
  } catch { return null; }
}

/** Pick the saved language, or the browser's. Call once on the client after mounting. */
export function initLocale() {
  if (typeof window === "undefined") return;
  const next = stored() ?? (navigator.language?.toLowerCase().startsWith("zh") ? "zh" : "en");
  if (next !== current) setLocale(next, false);
  else document.documentElement.lang = langTag(current);
}

export function setLocale(next: Locale, remember = true) {
  current = next;
  if (typeof window !== "undefined") {
    if (remember) { try { window.localStorage.setItem(STORAGE_KEY, next); } catch { /* private mode */ } }
    document.documentElement.lang = langTag(next);
  }
  listeners.forEach(listener => listener());
}

export function getLocale(): Locale { return current; }
const langTag = (locale: Locale) => locale === "zh" ? "zh-CN" : "en";
/** For Intl date and number formatting. */
export const intlLocale = () => current === "zh" ? "zh-CN" : "en-SG";

function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }

/** Re-render when the language changes. Use it once near the root: everything below re-renders with it. */
export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, getLocale, () => "en");
}

type Vars = Record<string, string | number | null | undefined>;

export function t(text: string, vars?: Vars): string {
  const template = current === "zh" ? (zh[text] ?? text) : text;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match: string, key: string) => key in vars ? String(vars[key] ?? "") : match);
}

/** Singular/plural: tn(n, "{n} contract", "{n} contracts"). Chinese uses one form. */
export function tn(count: number, one: string, other: string, vars?: Vars): string {
  return t(count === 1 ? one : other, { n: count, ...vars });
}
