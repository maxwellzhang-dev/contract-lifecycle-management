const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

type RouteContext = {
  params: Promise<{ path?: string[] }> | { path?: string[] };
};

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

async function pathFromContext(context: RouteContext) {
  const params = await context.params;
  return params.path?.join("/") ?? "";
}

function forwardedHeaders(request: Request) {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });
  return headers;
}

async function proxy(request: Request, context: RouteContext) {
  const incomingUrl = new URL(request.url);
  const path = await pathFromContext(context);
  const targetUrl = new URL(`/${path}${incomingUrl.search}`, BACKEND_API_URL);

  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers: forwardedHeaders(request),
    redirect: "manual",
  };

  if (!["GET", "HEAD"].includes(request.method)) {
    init.body = request.body;
    init.duplex = "half";
  }

  const response = await fetch(targetUrl, init);
  const responseHeaders = new Headers(response.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
