import { useState } from "react";
import { compactUsd, shortMonth, usd, useWidth } from "./ui";

/** Ranked horizontal bars: label, thin bar with a rounded data end, value at the tip. */
export function BarList({ items, total }: { items: { name: string; value: number }[]; total?: number }) {
  const peak = Math.max(...items.map((item) => item.value), 0) || 1;
  const sum = total ?? items.reduce((acc, item) => acc + item.value, 0);
  return (
    <div className="barlist" role="list">
      {items.map((item) => (
        <div className="barlist-row" role="listitem" key={item.name} title={`${item.name}: ${usd(item.value)}${sum ? ` (${((item.value / sum) * 100).toFixed(1)}% of total)` : ""}`}>
          <span className="barlist-label">{item.name}</span>
          <span className="barlist-track">
            <span className="barlist-bar" style={{ width: `${Math.max((item.value / peak) * 100, 0.5)}%` }} />
          </span>
          <span className="barlist-value">{compactUsd(item.value)}</span>
        </div>
      ))}
    </div>
  );
}

function niceTicks(max: number, count = 4) {
  const rough = max / count;
  const power = Math.pow(10, Math.floor(Math.log10(rough || 1)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= rough) ?? power;
  const ticks = [];
  for (let value = 0; value <= max + step * 0.01; value += step) ticks.push(value);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

/** One series over months, with a crosshair and tooltip. The title above names the series. */
export function TrendChart({ points, height = 220 }: { points: { period: string; value: number }[]; height?: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { top: 16, right: 64, bottom: 28, left: 52 };
  const ticks = niceTicks(Math.max(...points.map((p) => p.value), 1));
  const top = ticks[ticks.length - 1];
  const compact = top >= 10000;
  const innerW = Math.max(width - pad.left - pad.right, 10);
  const innerH = height - pad.top - pad.bottom;
  const x = (i: number) => pad.left + (points.length > 1 ? (i / (points.length - 1)) * innerW : innerW / 2);
  const y = (value: number) => pad.top + innerH - (value / top) * innerH;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const last = points.length - 1;
  const active = hover ?? last;

  return (
    <div className="chart" ref={ref} style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={`Trend over ${points.length} months`}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(event) => {
            const box = event.currentTarget.getBoundingClientRect();
            const ratio = (event.clientX - box.left - pad.left) / innerW;
            setHover(Math.min(last, Math.max(0, Math.round(ratio * last))));
          }}>
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} className={tick ? "grid" : "axis"} />
              <text x={pad.left - 8} y={y(tick) + 4} textAnchor="end" className="tick">
                {compact && tick ? `$${tick / 1000}K` : `$${tick.toLocaleString("en-US")}`}
              </text>
            </g>
          ))}
          {points.map((p, i) => (
            <text key={p.period} x={x(i)} y={height - 8} textAnchor="middle" className="tick tick-x">{shortMonth(p.period)}</text>
          ))}
          {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + innerH} className="crosshair" />}
          <path d={line} className="line" />
          {points.map((p, i) => (
            <circle key={p.period} cx={x(i)} cy={y(p.value)} r={i === active ? 5 : 4} className="dot" />
          ))}
          {hover === null && points.length > 0 && (
            <text x={x(last) + 10} y={y(points[last].value) + 4} className="end-label">{compactUsd(points[last].value)}</text>
          )}
        </svg>
      )}
      {hover !== null && (
        <div className="tooltip" style={{ left: Math.min(x(hover) + 12, width - 150), top: Math.max(y(points[hover].value) - 48, 0) }}>
          <span>{shortMonth(points[hover].period)} {points[hover].period.slice(0, 4)}</span>
          <strong>{usd(points[hover].value)}</strong>
        </div>
      )}
    </div>
  );
}

export function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <span className="sparkline" />;
  const w = 96, h = 28, max = Math.max(...values), min = Math.min(...values);
  const span = max - min || 1;
  const point = (v: number, i: number) => `${(4 + (i / (values.length - 1)) * (w - 8)).toFixed(1)},${(h - 4 - ((v - min) / span) * (h - 8)).toFixed(1)}`;
  const [lx, ly] = point(values[values.length - 1], values.length - 1).split(",");
  return (
    <svg className="sparkline" width={w} height={h} aria-hidden="true">
      <polyline points={values.map(point).join(" ")} />
      <circle cx={lx} cy={ly} r="3" />
    </svg>
  );
}
