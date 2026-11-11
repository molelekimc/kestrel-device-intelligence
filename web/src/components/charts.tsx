// Small SVG charts following the dataviz rules used by the static report: reference palette
// slots 1-3, one axis, thin marks with 4px rounded data ends, 2px surface gaps, selective
// labels, native tooltips, and a table view next to each chart.
import { num, shortWeek } from "../format";

const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];
type Series = { name: string; values: (number | string | null | undefined)[] };
const n = (v: unknown) => (typeof v === "number" ? v : null);

export function Legend({ names }: { names: string[] }) {
  if (names.length < 2) return null;
  return (
    <div className="legend">
      {names.map((name, i) => (
        <span key={name}>
          <i style={{ background: SERIES[i] }} />
          {name}
        </span>
      ))}
    </div>
  );
}

export function Columns({ labels, series, highlight, unit = "" }: {
  labels: string[]; series: Series[]; highlight?: string | null; unit?: string;
}) {
  const W = 640, H = 180, L = 44, B = 24, T = 12;
  const clean = series.map((s) => ({ ...s, values: s.values.map(n) }));
  const band = (W - L) / Math.max(1, labels.length);
  const bar = Math.min(24, band * 0.5);
  const totals = labels.map((_, i) => clean.reduce((a, s) => a + (s.values[i] ?? 0), 0));
  const rawTop = Math.max(1, ...totals);
  const step = 10 ** Math.max(0, Math.floor(Math.log10(rawTop)));
  const top = (Math.floor(rawTop / step) + 1) * step;
  const y = (v: number) => T + (H - T - B) * (1 - v / top);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img">
      {[0, top / 2, top].map((t) => (
        <g key={t}>
          <line x1={L} x2={W} y1={y(t)} y2={y(t)} className="grid" />
          <text x={L - 6} y={y(t) + 4} className="tick" textAnchor="end">{num(t)}</text>
        </g>
      ))}
      {labels.map((lab, i) => {
        const x = L + band * i + (band - bar) / 2;
        let base = 0;
        const dim = highlight && highlight !== lab ? 0.45 : 1;
        const marks = clean.map((s, k) => {
          const v = s.values[i] ?? 0;
          if (v <= 0) return null;
          const gap = base > 0 ? 2 : 0;
          const y0 = y(base) - gap, y1 = y(base + v);
          const isTop = clean.slice(k + 1).every((o) => (o.values[i] ?? 0) <= 0);
          const r = isTop && y0 - y1 > 4 ? 4 : 0;
          base += v;
          return (
            <path key={k} fill={SERIES[k]} opacity={dim}
              d={`M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + bar - r} Q${x + bar},${y1} ${x + bar},${y1 + r} V${y0} Z`}>
              <title>{`${lab} · ${s.name}: ${num(v)}${unit}`}</title>
            </path>
          );
        });
        return (
          <g key={lab}>
            {marks}
            <text x={x + bar / 2} y={H - 6} className="tick" textAnchor="middle">{shortWeek(lab)}</text>
            {lab === highlight && (
              <text x={x + bar / 2} y={y(totals[i]) - 6} className="value" textAnchor="middle">{num(totals[i])}</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function Lines({ labels, series, format, min = 0, max = 1, shade = {} }: {
  labels: string[]; series: Series[]; format: (v: number) => string; min?: number; max?: number;
  shade?: Record<string, string>;
}) {
  const W = 640, H = 180, L = 44, R = 96, B = 24, T = 12;
  const xs = labels.map((_, i) => L + ((W - L - R) * i) / Math.max(1, labels.length - 1));
  const y = (v: number) => T + (H - T - B) * (1 - (v - min) / (max - min));
  const half = labels.length > 1 ? (xs[1] - xs[0]) / 2 : 10;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img">
      {labels.map((lab, i) => shade[lab] ? (
        <rect key={lab} x={xs[i] - half} y={T} width={2 * half} height={H - T - B} className="shade">
          <title>{shade[lab]}</title>
        </rect>
      ) : null)}
      {[min, (min + max) / 2, max].map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" />
          <text x={L - 6} y={y(t) + 4} className="tick" textAnchor="end">{format(t)}</text>
        </g>
      ))}
      {labels.map((lab, i) => (
        <text key={lab} x={xs[i]} y={H - 6} className="tick" textAnchor="middle">{shortWeek(lab)}</text>
      ))}
      {series.map((s, k) => {
        const pts = s.values.map((v, i) => [xs[i], n(v)] as const).filter(([, v]) => v !== null) as [number, number][];
        if (!pts.length) return null;
        const d = pts.map(([x, v], j) => `${j ? "L" : "M"}${x},${y(v)}`).join(" ");
        const [lx, lv] = pts[pts.length - 1];
        return (
          <g key={s.name}>
            <path d={d} fill="none" stroke={SERIES[k]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {pts.map(([x, v], j) => (
              <circle key={j} cx={x} cy={y(v)} r={4} fill={SERIES[k]} className="dot">
                <title>{`${labels[xs.indexOf(x)]} · ${s.name}: ${format(v)}`}</title>
              </circle>
            ))}
            <text x={lx + 8} y={y(lv) + 4} className="value">{`${s.name} ${format(lv)}`}</text>
          </g>
        );
      })}
    </svg>
  );
}
