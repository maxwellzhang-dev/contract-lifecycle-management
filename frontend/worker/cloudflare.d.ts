// Minimal Cloudflare runtime types for the optional Worker entry point (worker/, db/).
// The app itself runs on Node; these keep `tsc` clean without @cloudflare/workers-types,
// whose globals clash with the DOM library.

interface Fetcher {
  fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>;
}

interface D1Database {
  prepare(query: string): unknown;
  batch(statements: unknown[]): Promise<unknown[]>;
  exec(query: string): Promise<unknown>;
}

declare module "cloudflare:workers" {
  export const env: { DB?: D1Database } & Record<string, unknown>;
}
