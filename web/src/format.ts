// Number and date formatting. Suppressed values arrive as text such as "<10" and pass through.

type Val = number | string | null | undefined;

const shown = (v: Val) => (typeof v === "string" ? v : null);

export function zar(v: Val): string {
  if (v === null || v === undefined) return "–";
  if (shown(v)) return v as string;
  const n = v as number;
  if (Math.abs(n) >= 1e6) return `R${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `R${(n / 1e3).toFixed(1)}K`;
  return `R${Math.round(n).toLocaleString("en-ZA")}`;
}

export function num(v: Val, digits = 0): string {
  if (v === null || v === undefined) return "–";
  if (shown(v)) return v as string;
  return (v as number).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function pct(v: Val, digits = 0): string {
  if (v === null || v === undefined) return "–";
  if (shown(v)) return v as string;
  return `${((v as number) * 100).toFixed(digits)}%`;
}

export function signedPct(v: Val): string {
  if (typeof v !== "number") return "–";
  return `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(0)}%`;
}

export const shortWeek = (w: string) => w.split("-")[1] ?? w;

export function weekRange(r: string | null | undefined): string {
  if (!r) return "";
  const [a, b] = r.split("..");
  return !b || a === b ? shortWeek(a) : `${shortWeek(a)}–${shortWeek(b)}`;
}

export function niceDate(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
}

export function niceDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" })} ${d
    .toTimeString()
    .slice(0, 5)}`;
}

/** "3 min ago" for a UTC ISO timestamp, relative to `now`. */
export function ago(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "never";
  const s = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export const METRIC_NAME: Record<string, string> = {
  app_crash_rate: "app crash rate",
  bsod_rate: "blue screen rate",
  coverage: "telemetry coverage",
  tickets_per_1k: "incidents per device",
  it_downtime_per_100: "IT line-stop time",
  boot_time_p50: "median boot time",
};
