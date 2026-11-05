// Lists interface strings that have no Chinese translation in app/zh.ts.
// Run: node scripts/check-i18n.mjs   (exit code 1 when something is missing; --list prints all keys)
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const root = new URL("../app/", import.meta.url).pathname;
const files = [];
(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.(tsx|ts)$/.test(name) && name !== "zh.ts") files.push(path);
  }
})(root);

const literal = String.raw`"((?:[^"\\]|\\.)*)"`;
const patterns = [
  new RegExp(String.raw`\bt\(\s*${literal}`, "g"),                                  // t("…")
  new RegExp(String.raw`\btn\([^,]+,\s*${literal}\s*,\s*${literal}`, "g"),          // tn(n, "…", "…")
  new RegExp(String.raw`errorText\([^,]+,\s*${literal}`, "g"),                      // errorText(err, "…")
  new RegExp(String.raw`\blabel:\s*${literal}`, "g"),                               // { label: "…" } lists
];
const keys = new Map();
for (const file of files) {
  const source = readFileSync(file, "utf8");
  for (const pattern of patterns) for (const match of source.matchAll(pattern))
    for (const group of match.slice(1)) if (group !== undefined) keys.set(JSON.parse(`"${group}"`), file.slice(root.length));
}
// Values shown through t(value): statuses, kinds, categories and other stored labels.
const extra = JSON.parse(readFileSync(new URL("./i18n-values.json", import.meta.url), "utf8"));
for (const value of extra) keys.set(value, "i18n-values.json");

const zhSource = readFileSync(join(root, "zh.ts"), "utf8");
const translated = new Set([...zhSource.matchAll(new RegExp(String.raw`^\s*${literal}\s*:`, "gm"))].map(m => JSON.parse(`"${m[1]}"`)));
if (process.argv.includes("--list")) { for (const [key, file] of keys) console.log(`${file}\t${key}`); process.exit(0); }
const missing = [...keys].filter(([key]) => !translated.has(key));
for (const [key, file] of missing) console.log(`${file}: ${JSON.stringify(key)}`);
const unused = [...translated].filter(key => !keys.has(key));
if (unused.length && process.argv.includes("--unused")) for (const key of unused) console.log(`unused: ${JSON.stringify(key)}`);
console.log(`${keys.size} strings, ${missing.length} without a Chinese translation.`);
process.exit(missing.length ? 1 : 0);
