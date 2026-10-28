import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));

export const label = (s?: string | null) =>
  (s ?? "—").replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

export function dt(v?: string | null, withTime = true) {
  if (!v) return "—";
  const d = new Date(v);
  return withTime
    ? d.toLocaleString(undefined, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

export function duration(seconds?: number | null) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

export const pct = (v?: number | null) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
