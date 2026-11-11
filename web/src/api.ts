// Client for the governed reporting API. Every data call goes through /api/tools/{name},
// i.e. through the same FleetTools guardrails as Claude Desktop.

export type Row = Record<string, any>;

export interface Warning {
  week: string;
  scope: string;
  code: string;
  severity: "critical" | "warning" | "info";
  message: string;
}

export interface FreshnessSummary {
  status: "fresh" | "late" | "stale" | "missing" | "unknown";
  checked_as_of?: string;
  behind_sla?: string[];
}

export interface Envelope {
  period?: Row;
  rows: Row[];
  warnings: Warning[];
  data_quality: { status: string; critical: number; warning: number };
  data_freshness: FreshnessSummary;
  notes: string[];
  [key: string]: any;
}

export interface Status {
  version: string;
  as_of: string;
  replay: boolean;
  weeks: string[];
  data_to: string | null;
  freshness: FreshnessSummary;
  ai_enabled: boolean;
  last_refresh: Row | null;
  public?: boolean;               // read-only public snapshot (no manual refresh)
  default_week?: string | null;   // week to open on without a ?week= link
  chat_passcode?: boolean;        // Ask the fleet needs a passcode
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Params = Record<string, string | number | boolean | undefined | null>;

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, detail);
  }
  return r.json() as Promise<T>;
}

export function tool(name: string, params: Params = {}): Promise<Envelope> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  return getJson<Envelope>(`/api/tools/${name}${q.size ? `?${q}` : ""}`);
}

export const getStatus = () => getJson<Status>("/api/status");
export const refreshHistory = () => getJson<{ runs: Row[] }>("/api/refresh/history?limit=20");

export async function requestRefresh(): Promise<void> {
  const r = await fetch("/api/refresh", { method: "POST" });
  if (!r.ok) throw new ApiError(r.status, "Refresh request failed");
}

/** Calls onRefresh whenever the server announces a new warehouse version. Returns an unsubscribe. */
export function subscribe(onRefresh: () => void, onConnection?: (live: boolean) => void): () => void {
  const es = new EventSource("/api/events");
  es.addEventListener("refreshed", () => onRefresh());
  es.onopen = () => onConnection?.(true);
  es.onerror = () => onConnection?.(false);
  return () => es.close();
}

export type ChatEvent =
  | { type: "tool"; name: string; args: Row }
  | { type: "tool_done"; name: string; ok: boolean }
  | { type: "answer"; text: string; model: string; tool_calls: number }
  | { type: "error"; message: string };

/** POST /api/ask and parse the server-sent event stream (EventSource cannot POST). */
export async function ask(
  question: string,
  history: { role: "user" | "assistant"; content: string }[],
  onEvent: (e: ChatEvent) => void,
  passcode?: string,
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (passcode) headers["X-Chat-Passcode"] = passcode;
  const r = await fetch("/api/ask", { method: "POST", headers, body: JSON.stringify({ question, history }) });
  if (r.status === 401) {
    onEvent({ type: "error", message: "That passcode isn't right. Ask the person who shared this demo for it." });
    return;
  }
  if (!r.ok || !r.body) {
    onEvent({ type: "error", message: `The assistant could not be reached (${r.status}).` });
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const data = frame
        .split(/\r?\n/)
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trim())
        .join("");
      if (data) onEvent(JSON.parse(data) as ChatEvent);
    }
  }
}
