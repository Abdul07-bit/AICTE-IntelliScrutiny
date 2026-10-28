"use client";
import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { label } from "@/lib/utils";

// Categorical slots (fixed order, validated for the dark surface #0a1020: CVD ΔE ≥ 8.4, contrast ≥ 3:1).
export const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"];
const SURFACE = "#0a1020";
const AXIS = { stroke: "#6b7891", fontSize: 11, tickLine: false, axisLine: false } as const;

function TooltipBox({ active, payload, label: l }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-ink-900 px-3 py-2 text-xs shadow-xl">
      <div className="mb-1 font-medium text-fg">{l}</div>
      {payload.filter((p: any) => p.value).map((p: any) => (
        <div key={p.dataKey} className="flex items-center gap-2 text-fg-muted">
          <span className="h-2 w-2 rounded-sm" style={{ background: p.color ?? p.fill }} />
          <span>{label(p.name)}</span><span className="ml-auto pl-3 tabular-nums text-fg">{p.value}</span>
        </div>
      ))}
    </div>
  );
}

function Legend({ keys }: { keys: string[] }) {
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-fg-muted" aria-label="Legend">
      {keys.map((k, i) => (
        <li key={k} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: SERIES[i] }} />{label(k)}</li>
      ))}
    </ul>
  );
}

function DataTable({ rows, cols }: { rows: Record<string, any>[]; cols: string[] }) {
  return (
    <details className="mt-2 text-xs text-fg-muted">
      <summary className="cursor-pointer text-fg-dim hover:text-fg">Show data table</summary>
      <div className="mt-2 max-h-48 overflow-auto scroll-thin">
        <table className="w-full"><thead><tr>{cols.map((c) => <th key={c} className="px-2 py-1 text-left font-medium">{label(c)}</th>)}</tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-line/60">{cols.map((c) => <td key={c} className="px-2 py-1 tabular-nums">{r[c] ?? 0}</td>)}</tr>)}</tbody></table>
      </div>
    </details>
  );
}

export function StackedBars({ data, keys, xKey, height = 220 }: { data: any[]; keys: string[]; xKey: string; height?: number }) {
  return (
    <div>
      <Legend keys={keys} />
      <div style={{ height }} role="img" aria-label={`Stacked bar chart of ${keys.map(label).join(", ")} by ${xKey}`}>
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 4, right: 4, left: -24, bottom: 0 }} barCategoryGap={3}>
            <CartesianGrid vertical={false} stroke="#1f2b45" strokeDasharray="0" />
            <XAxis dataKey={xKey} {...AXIS} tickFormatter={(v: string) => v.slice(5)} minTickGap={16} />
            <YAxis {...AXIS} allowDecimals={false} />
            <Tooltip content={<TooltipBox />} cursor={{ fill: "rgba(148,163,184,0.06)" }} />
            {keys.map((k, i) => (
              <Bar key={k} dataKey={k} name={k} stackId="s" fill={SERIES[i]} stroke={SURFACE} strokeWidth={2}
                radius={i === keys.length - 1 ? [4, 4, 0, 0] : 0} isAnimationActive={false} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <DataTable rows={data} cols={[xKey, ...keys]} />
    </div>
  );
}

export function HBars({ data, height, unit = "" }: { data: { name: string; value: number }[]; height?: number; unit?: string }) {
  const h = height ?? Math.max(80, data.length * 30 + 10);
  return (
    <div>
      <div style={{ height: h }} role="img" aria-label={data.map((d) => `${label(d.name)}: ${d.value}${unit}`).join("; ")}>
        <ResponsiveContainer>
          <BarChart data={data} layout="vertical" margin={{ top: 0, right: 48, left: 8, bottom: 0 }} barCategoryGap={6}>
            <XAxis type="number" hide />
            <YAxis type="category" dataKey="name" width={150} {...AXIS} tick={{ fill: "#9aa7bd", fontSize: 11 }} tickFormatter={(v: string) => label(v)} />
            <Tooltip content={<TooltipBox />} cursor={{ fill: "rgba(148,163,184,0.06)" }} />
            <Bar dataKey="value" name="count" fill={SERIES[0]} radius={[0, 4, 4, 0]} isAnimationActive={false} maxBarSize={16}>
              <LabelList dataKey="value" position="right" fill="#9aa7bd" fontSize={11} formatter={(v: any) => `${v}${unit}`} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
