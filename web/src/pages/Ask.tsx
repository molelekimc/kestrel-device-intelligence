import { Fragment, useRef, useState, type ReactNode } from "react";
import { ask, type ChatEvent } from "../api";
import { Card } from "../components/ui";
import { useLive } from "../live";

interface Turn {
  role: "user" | "assistant";
  content: string;
  steps?: { name: string; args: Record<string, any>; ok?: boolean }[];
  error?: boolean;
}

const SUGGESTIONS = [
  "Give me the fleet headline for 2026-W41. What changed and how much can I trust it?",
  "Telemetry shows almost no MES crashes at Durban in W41. Did Durban escape the KestrelMES problem?",
  "Did the Windows 11 25H2 upgrade make boot times faster?",
  "Problem management says the 25H2 pause for HP Elite Minis was done. Did it work?",
  "List the hostnames of the ten devices with the most blue screens.",
];

const STEP_TEXT: Record<string, string> = {
  get_kpi: "Reading a KPI", compare_cohorts: "Comparing cohorts", get_coverage: "Checking coverage",
  get_data_quality_status: "Checking data quality", list_threshold_breaches: "Listing threshold breaches",
  get_rollout_status: "Reconciling the rollout", get_top_events: "Finding top crashes / blue screens",
  get_business_impact: "Reading business impact", get_ticket_metrics: "Reading service desk metrics",
  get_change_correlation: "Checking logged changes", get_remediation_status: "Checking remediations",
  get_timeline: "Reading the timeline", get_data_freshness: "Checking freshness",
  list_allowed_values: "Looking up allowed values", get_metric_definition: "Reading a metric definition",
};

// Minimal, safe rendering of the model's Markdown: headings, bullets, paragraphs, **bold**.
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : <Fragment key={i}>{part}</Fragment>);
}

function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) blocks.push(<ul key={`l${blocks.length}`}>{list.map((li, i) => <li key={i}>{inline(li)}</li>)}</ul>);
    list = [];
  };
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (/^[-*]\s+/.test(t)) list.push(t.replace(/^[-*]\s+/, ""));
    else {
      flush();
      if (/^#{1,6}\s/.test(t)) blocks.push(<h3 key={blocks.length}>{inline(t.replace(/^#+\s*/, ""))}</h3>);
      else if (t) blocks.push(<p key={blocks.length}>{inline(t)}</p>);
    }
  }
  flush();
  return <>{blocks}</>;
}

export default function Ask() {
  const { status } = useLive();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  const disabled = status ? !status.ai_enabled : false;
  const send = async (question: string) => {
    const q = question.trim();
    if (!q || busy || disabled) return;
    const history = turns.filter((t) => !t.error).map(({ role, content }) => ({ role, content }));
    setTurns((t) => [...t, { role: "user", content: q }, { role: "assistant", content: "", steps: [] }]);
    setDraft("");
    setBusy(true);
    const update = (fn: (a: Turn) => Turn) =>
      setTurns((t) => [...t.slice(0, -1), fn(t[t.length - 1])]);
    await ask(q, history, (e: ChatEvent) => {
      if (e.type === "tool") update((a) => ({ ...a, steps: [...(a.steps ?? []), { name: e.name, args: e.args }] }));
      else if (e.type === "tool_done")
        update((a) => {
          const steps = [...(a.steps ?? [])];
          const i = steps.map((s) => s.name).lastIndexOf(e.name);
          if (i >= 0) steps[i] = { ...steps[i], ok: e.ok };
          return { ...a, steps };
        });
      else if (e.type === "answer") update((a) => ({ ...a, content: e.text }));
      else if (e.type === "error") update((a) => ({ ...a, content: e.message, error: true }));
      end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    });
    setBusy(false);
  };

  return (
    <Card title="Ask the fleet">
      <p className="note">
        Claude answers using only the governed tools: aggregates, small groups hidden, no device or person identifiers,
        and the data quality and freshness caveats that apply. Explanations are hypotheses, not proven causes.
      </p>
      {status && !status.ai_enabled && (
        <p className="error">Ask the fleet is disabled on this server: set ANTHROPIC_API_KEY. Every other page works without it.</p>
      )}
      <div className="chat">
        {turns.length === 0 && (
          <div className="suggestions">
            {SUGGESTIONS.map((s) => <button key={s} className="ghost" onClick={() => send(s)} disabled={busy || disabled}>{s}</button>)}
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`turn ${t.role}${t.error ? " err" : ""}`}>
            {t.role === "assistant" && (t.steps?.length ?? 0) > 0 && (
              <ol className="steps">
                {t.steps!.map((s, j) => (
                  <li key={j}>
                    <span aria-hidden="true">{s.ok === undefined ? "…" : s.ok ? "✓" : "✕"}</span>{" "}
                    {STEP_TEXT[s.name] ?? s.name}
                    <span className="meta"> {Object.entries(s.args).map(([k, v]) => `${k}=${v}`).join(", ")}</span>
                  </li>
                ))}
              </ol>
            )}
            {t.content ? <Markdown text={t.content} /> : t.role === "assistant" && <p className="note">Working…</p>}
          </div>
        ))}
        <div ref={end} />
      </div>
      <form className="ask" onSubmit={(e) => { e.preventDefault(); send(draft); }}>
        <label htmlFor="q" className="visually-hidden">Question</label>
        <textarea id="q" rows={2} maxLength={2000} value={draft} disabled={disabled}
          placeholder={disabled ? "Disabled: no ANTHROPIC_API_KEY on the server" : "Ask about the fleet…"}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(draft); } }} />
        <button type="submit" disabled={busy || disabled || !draft.trim()}>{busy ? "Thinking…" : "Ask"}</button>
      </form>
    </Card>
  );
}
