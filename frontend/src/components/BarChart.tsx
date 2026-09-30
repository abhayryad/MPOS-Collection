import { useState, type FocusEvent, type PointerEvent } from "react";
import { useWidth } from "../lib/hooks";
import { dayLabel } from "../lib/format";

export interface Series {
  key: string;
  label: string;
  color: string; // CSS colour, e.g. var(--s1)
  values: number[];
}

interface Props {
  categories: string[]; // ISO dates
  series: Series[];
  mode: "stack" | "group";
  format: (v: number) => string;
  formatShort: (v: number) => string;
  totalLabel?: string; // adds a total row to the tooltip and labels on stacks
  height?: number;
}

interface TipState {
  index: number;
  x: number;
  y: number;
}

const MARGIN = { l: 48, r: 8, t: 18, b: 26 };
const RADIUS = 4;
const GAP = 2; // surface gap between stacked segments / grouped bars

function niceTicks(lo: number, hi: number, count = 4) {
  if (lo === hi) hi = lo + 1;
  const span = hi - lo;
  const mag = Math.pow(10, Math.floor(Math.log10(span / count)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) ?? 10 * mag;
  const ticks: number[] = [];
  for (let t = Math.floor(lo / step) * step; t <= Math.ceil(hi / step) * step + step / 2; t += step) {
    ticks.push(+t.toFixed(10));
  }
  return ticks;
}

/** Bar from baseline y0 to data end y1 with rounded corners on the data end only. */
function barPath(x: number, y0: number, y1: number, w: number, rounded: boolean) {
  const h = Math.abs(y1 - y0);
  if (h <= 0 || w <= 0) return "";
  if (!rounded) return `M${x},${y0}H${x + w}V${y1}H${x}Z`;
  const r = Math.min(RADIUS, h, w / 2);
  const s = y1 < y0 ? 1 : -1; // bar grows up (1) or down (-1)
  return (
    `M${x},${y0}V${y1 + s * r}Q${x},${y1} ${x + r},${y1}` +
    `H${x + w - r}Q${x + w},${y1} ${x + w},${y1 + s * r}V${y0}Z`
  );
}

export function BarChart({ categories, series, mode, format, formatShort, totalLabel, height = 260 }: Props) {
  const [ref, measured] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<TipState | null>(null);

  if (!categories.length) {
    return (
      <div ref={ref} className="chart">
        <div className="empty">No data for this filter</div>
      </div>
    );
  }

  const W = Math.max(280, measured || 600);
  const H = height;
  const iw = W - MARGIN.l - MARGIN.r;
  const ih = H - MARGIN.t - MARGIN.b;
  const total = (i: number) => series.reduce((t, s) => t + s.values[i], 0);

  let lo = 0;
  let hi = 0;
  categories.forEach((_, i) => {
    if (mode === "stack") {
      const pos = series.reduce((t, s) => t + Math.max(0, s.values[i]), 0);
      const neg = series.reduce((t, s) => t + Math.min(0, s.values[i]), 0);
      hi = Math.max(hi, pos);
      lo = Math.min(lo, neg);
    } else {
      series.forEach((s) => {
        hi = Math.max(hi, s.values[i]);
        lo = Math.min(lo, s.values[i]);
      });
    }
  });
  const ticks = niceTicks(lo, hi);
  const [d0, d1] = [ticks[0], ticks[ticks.length - 1]];
  const y = (v: number) => MARGIN.t + ih - ((v - d0) / (d1 - d0)) * ih;

  const band = iw / categories.length;
  const groupW = Math.min(mode === "stack" ? 44 : 56, band * 0.7);
  const labelEvery = Math.ceil(categories.length / Math.max(1, Math.floor(iw / 52)));
  const showTotals = mode === "stack" && !!totalLabel && categories.length <= 16;

  const bars = categories.map((cat, i) => {
    const cx = MARGIN.l + band * i + band / 2;
    const paths: { d: string; color: string; key: string }[] = [];

    if (mode === "stack") {
      const x = cx - groupW / 2;
      for (const sign of [1, -1]) {
        const list = series.filter((s) => sign * s.values[i] > 0);
        let acc = 0;
        list.forEach((s, j) => {
          const v = s.values[i];
          const a = y(acc) - (j === 0 ? 0 : sign * GAP);
          const b = y(acc + v);
          const d = barPath(x, a, b, groupW, j === list.length - 1);
          if (d) paths.push({ d, color: s.color, key: `${s.key}${sign}` });
          acc += v;
        });
      }
    } else {
      const bw = (groupW - GAP * (series.length - 1)) / series.length;
      series.forEach((s, j) => {
        const d = barPath(cx - groupW / 2 + j * (bw + GAP), y(0), y(s.values[i]), bw, true);
        if (d) paths.push({ d, color: s.color, key: s.key });
      });
    }

    const top = y(series.reduce((t, s) => t + Math.max(0, s.values[i]), 0));
    const onMove = (e: PointerEvent<SVGRectElement>) => setTip({ index: i, x: e.clientX, y: e.clientY });
    const onFocus = (e: FocusEvent<SVGRectElement>) => {
      const r = e.currentTarget.getBoundingClientRect();
      setTip({ index: i, x: r.left + r.width / 2, y: r.top + 40 });
    };

    return (
      <g key={cat}>
        {i % labelEvery === 0 && (
          <text className="tick" x={cx} y={H - 8} textAnchor="middle">
            {dayLabel(cat)}
          </text>
        )}
        {paths.map((p) => (
          <path key={p.key} d={p.d} style={{ fill: p.color }} />
        ))}
        {showTotals && total(i) !== 0 && (
          <text className="dlabel" x={cx} y={top - 5} textAnchor="middle">
            {formatShort(total(i))}
          </text>
        )}
        <rect
          className="hit"
          x={MARGIN.l + band * i}
          y={MARGIN.t}
          width={band}
          height={ih}
          tabIndex={0}
          aria-label={`${cat}: ${series.map((s) => `${s.label} ${format(s.values[i])}`).join(", ")}`}
          onPointerMove={onMove}
          onPointerLeave={() => setTip(null)}
          onFocus={onFocus}
          onBlur={() => setTip(null)}
        />
      </g>
    );
  });

  return (
    <div ref={ref} className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} height={H} role="img">
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={MARGIN.l}
              x2={W - MARGIN.r}
              y1={y(t)}
              y2={y(t)}
              style={{ stroke: t === 0 ? "var(--axis)" : "var(--grid)" }}
            />
            <text className="tick" x={MARGIN.l - 8} y={y(t) + 4} textAnchor="end">
              {formatShort(t)}
            </text>
          </g>
        ))}
        {bars}
      </svg>
      {tip && (
        <ChartTooltip
          x={tip.x}
          y={tip.y}
          title={categories[tip.index]}
          rows={series.map((s) => ({ label: s.label, color: s.color, value: format(s.values[tip.index]) }))}
          total={totalLabel ? { label: totalLabel, value: format(total(tip.index)) } : undefined}
        />
      )}
    </div>
  );
}

interface TooltipProps {
  x: number;
  y: number;
  title: string;
  rows: { label: string; color: string; value: string }[];
  total?: { label: string; value: string };
}

function ChartTooltip({ x, y, title, rows, total }: TooltipProps) {
  const left = Math.min(window.innerWidth - 190, Math.max(8, x + 14));
  const top = Math.max(8, y - 24 - rows.length * 22 - (total ? 28 : 0));
  return (
    <div className="tip" role="tooltip" style={{ left, top }}>
      <div className="tip-title">{title}</div>
      {rows.map((r) => (
        <div className="tip-row" key={r.label}>
          <span>
            <i style={{ background: r.color }} />
            {r.label}
          </span>
          <b>{r.value}</b>
        </div>
      ))}
      {total && (
        <div className="tip-row tip-total">
          <span>{total.label}</span>
          <b>{total.value}</b>
        </div>
      )}
    </div>
  );
}

export function Legend({ series }: { series: Pick<Series, "key" | "label" | "color">[] }) {
  return (
    <div className="legend">
      {series.map((s) => (
        <span key={s.key}>
          <i style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}
