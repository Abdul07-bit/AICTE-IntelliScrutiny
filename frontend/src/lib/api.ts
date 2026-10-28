// Thin API client. Auth uses the httpOnly session cookie set by the backend (proxied through
// this origin); state-changing calls send the anti-CSRF header the backend requires.
export const API = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string,
              public details?: Record<string, unknown>) {
    super(message);
  }
}

async function parse(res: Response) {
  if (res.status === 204) return null;
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const e = body?.error ?? {};
    throw new ApiError(res.status, e.code ?? "HTTP_ERROR", e.message ?? res.statusText, e.request_id, e.details);
  }
  return body;
}

export async function api<T = any>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "IntelliScrutiny", ...(init.headers as any) };
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const res = await fetch(`${API}${path}`, { ...init, headers, body, credentials: "same-origin", cache: "no-store" });
  if (res.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
  }
  return parse(res);
}

export const post = <T = any>(path: string, json?: unknown) => api<T>(path, { method: "POST", json: json ?? {} });
export const patch = <T = any>(path: string, json: unknown) => api<T>(path, { method: "PATCH", json });

export function qs(params: Record<string, string | number | boolean | undefined | null>) {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") p.set(k, String(v)); });
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function openDocument(docId: string) {
  const { url } = await api<{ url: string }>(`/documents/${docId}/url`);
  window.open(url, "_blank", "noopener");
}
