"use client";
import { AnimatePresence, motion } from "framer-motion";
import { CircleCheck, CircleX, ExternalLink, FileText, LoaderCircle, MinusCircle } from "lucide-react";
import { useState } from "react";
import { openDocument, post, qs } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { cn, dt, label } from "@/lib/utils";
import { useSession } from "./shell";
import { Badge, Button, Card, Dialog, EmptyState, Field, Select, Textarea, type Tone } from "./ui/primitives";

// ---------------------------------------------------------------- badges
const STATUS_TONE: Record<string, Tone> = {
  DRAFT: "neutral", SUBMITTED: "accent", PROCESSING: "accent", VALIDATION_COMPLETE: "violet",
  REVIEW_REQUIRED: "danger", REVIEW_IN_PROGRESS: "warn", RECTIFICATION_REQUESTED: "warn",
  READY_FOR_FURTHER_SCRUTINY: "ok",
  VERIFIED: "ok", COMPLETED: "ok", MISMATCH_DETECTED: "danger", EXPIRED: "danger", PENDING: "warn",
  SOURCE_UNAVAILABLE: "warn", MANUAL_REVIEW_REQUIRED: "warn", NOT_APPLICABLE: "neutral",
  AWAITING_MANUAL_CONFIRMATION: "warn", COMPLETED_WITH_EXCEPTIONS: "danger", NOT_STARTED: "neutral",
  PROCESSED: "ok", FAILED: "danger", QUARANTINED: "danger", UPLOADED: "neutral", PURGED: "neutral",
  APPROVED: "ok", PENDING_APPROVAL: "warn", RETIRED: "neutral", ACTIVE: "ok", SUPERSEDED: "neutral", INDEXED: "ok",
  HEALTHY: "ok", DOWN: "danger", UNKNOWN: "neutral", NOT_CONFIGURED: "neutral",
  OPEN: "accent", ACCEPTED: "ok", DISPUTED: "violet", EVIDENCE_REQUESTED: "warn", RESOLVED: "ok",
  INSPECTION_RECOMMENDED: "warn", ISSUER_CONFIRMATION_REQUESTED: "warn",
  PASS: "ok", FAIL: "danger", INDETERMINATE: "warn", SUCCEEDED: "ok", PARTIAL: "warn", RUNNING: "accent", QUEUED: "neutral",
  CANCELLED: "neutral", SKIPPED: "neutral",
};
export const StatusBadge = ({ s, prefix }: { s?: string | null; prefix?: string }) =>
  s ? <Badge tone={STATUS_TONE[s] ?? "neutral"}>{prefix}{label(s)}</Badge> : <span className="text-fg-dim">—</span>;

const SEV_TONE: Record<string, Tone> = { CRITICAL: "danger", HIGH: "danger", MEDIUM: "warn", LOW: "accent", INFO: "neutral" };
export const SeverityBadge = ({ s }: { s: string }) => <Badge tone={SEV_TONE[s] ?? "neutral"}>{s}</Badge>;

const CAT_TONE: Record<string, Tone> = {
  UNMET_REQUIREMENT: "danger", UNRESOLVED_EVIDENCE: "warn", SUSPECTED_ANOMALY: "violet",
  MACHINE_OBSERVATION: "accent", INFORMATION: "neutral",
};
export const CategoryBadge = ({ c }: { c: string }) => <Badge tone={CAT_TONE[c] ?? "neutral"}>{label(c)}</Badge>;

export function SourceBadges({ h }: { h: { is_synthetic?: boolean; is_official?: boolean; source_type?: string; source_status?: string } }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {h.is_synthetic && <Badge tone="warn" title="Fictional demonstration text">SYNTHETIC</Badge>}
      {h.source_type === "INTERNAL_INTERPRETATION" && <Badge tone="violet">Internal interpretation · not official</Badge>}
      {h.is_official && <Badge tone="ok">Official source</Badge>}
      {h.source_status && h.source_status !== "ACTIVE" && <Badge>{label(h.source_status)}</Badge>}
    </span>
  );
}

// ---------------------------------------------------------------- processing timeline
export function ProcessingTimeline({ job }: { job: any }) {
  if (!job) return null;
  return (
    <ol className="space-y-1.5" aria-label="Processing steps">
      {job.steps.map((s: any, i: number) => {
        const Icon = s.status === "SUCCEEDED" ? CircleCheck : s.status === "FAILED" ? CircleX
          : s.status === "RUNNING" ? LoaderCircle : MinusCircle;
        return (
          <motion.li key={s.key} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}
            className={cn("flex items-start gap-3 rounded-lg border px-3 py-2",
              s.status === "RUNNING" ? "border-accent/40 bg-accent/5" : "border-line bg-ink-850/60")}>
            <Icon aria-hidden className={cn("mt-0.5 h-4 w-4 shrink-0", s.status === "SUCCEEDED" && "text-ok",
              s.status === "FAILED" && "text-danger", s.status === "RUNNING" && "animate-spin text-accent",
              ["PENDING", "SKIPPED"].includes(s.status) && "text-fg-dim")} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>{s.label}</span>
                <span className="text-[11px] tabular-nums text-fg-dim">
                  {s.duration_ms != null ? `${s.duration_ms} ms` : label(s.status)}{s.attempts > 1 ? ` · ${s.attempts} attempts` : ""}
                </span>
              </div>
              {(s.detail || s.error) && <p className={cn("mt-0.5 text-xs", s.error ? "text-danger" : "text-fg-muted")}>{s.error || s.detail}</p>}
            </div>
          </motion.li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------- citations & evidence
export function CitationCard({ c, compact }: { c: any; compact?: boolean }) {
  return (
    <div className="rounded-lg border border-line bg-ink-850/70 p-3">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="font-medium text-fg">{c.title}</span>
        <span className="text-fg-dim">· {c.edition}</span>
        {c.clause_ref && <Badge tone="accent">Clause {c.clause_ref}</Badge>}
        {c.page && <Badge>p.{c.page}</Badge>}
        <SourceBadges h={c} />
        {c.match && <Badge tone="ok">{c.match}</Badge>}
      </div>
      {c.heading && <p className="mt-1 text-[11px] text-fg-dim">{c.heading}</p>}
      <p className={cn("mt-1.5 whitespace-pre-line text-xs leading-relaxed text-fg-muted", compact && "line-clamp-4")}>{c.text}</p>
      {(c.vector_score != null || c.keyword_score != null) && (
        <p className="mt-1.5 font-mono text-[10px] text-fg-dim">
          vector {c.vector_score ?? "—"} · keyword {c.keyword_score ?? "—"} · fused {c.score}
          {c.effective_from && ` · effective ${c.effective_from}${c.effective_to ? `→${c.effective_to}` : "→"}`}
        </p>
      )}
    </div>
  );
}

export function EvidenceCard({ e }: { e: any }) {
  return (
    <div className="rounded-lg border border-line bg-ink-850/70 p-3">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="font-medium">{e.source_label}</span>
        <Badge tone={e.source_kind === "RECORD" ? "violet" : "accent"}>{e.source_kind === "RECORD" ? "Prior authorized record" : "Submitted document"}</Badge>
        {e.page && <Badge>p.{e.page}</Badge>}
        {e.document_id && <button className="text-accent hover:underline" onClick={() => openDocument(e.document_id)}>open</button>}
      </div>
      <p className="mt-1.5 line-clamp-5 whitespace-pre-line text-xs leading-relaxed text-fg-muted">{e.text}</p>
      <p className="mt-1.5 font-mono text-[10px] text-fg-dim">vector {e.vector_score ?? "—"} · keyword {e.keyword_score ?? "—"} · v{e.version}</p>
    </div>
  );
}

// ---------------------------------------------------------------- page facsimile (page-level evidence)
export function PageFacsimile({ page, highlight }: { page: any; highlight?: number[] | null }) {
  const ratio = page.width && page.height ? page.height / page.width : 1.414;
  return (
    <div className="relative w-full overflow-hidden rounded-lg border border-line bg-[#f5f3ee]" style={{ paddingTop: `${ratio * 100}%` }}
      aria-label={`Reconstructed text layer of page ${page.page_no}`}>
      {page.lines.map((ln: any, i: number) => (
        <div key={i} className="absolute truncate font-sans text-[#1b1f27]"
          style={{ left: `${ln.bbox[0] * 100}%`, top: `${ln.bbox[1] * 100}%`, width: `${(ln.bbox[2] - ln.bbox[0]) * 100}%`,
                   fontSize: "clamp(6px, 0.75vw, 11px)", lineHeight: 1.1 }}>
          {ln.text}
        </div>
      ))}
      {highlight && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="absolute rounded-sm ring-2 ring-accent"
          style={{ left: `${highlight[0] * 100 - 0.5}%`, top: `${highlight[1] * 100 - 0.4}%`,
                   width: `${(highlight[2] - highlight[0]) * 100 + 1}%`, height: `${Math.max(1.6, (highlight[3] - highlight[1]) * 100 + 0.8)}%`,
                   background: "rgba(34,211,238,0.18)" }} />
      )}
      {!page.lines.length && <div className="absolute inset-0 grid place-items-center text-xs text-[#555]">No text layer ({page.method})</div>}
      <span className="absolute bottom-1 right-2 rounded bg-black/60 px-1.5 text-[10px] text-white">p.{page.page_no} · {page.method}</span>
    </div>
  );
}

// ---------------------------------------------------------------- findings
const ACTIONS = [
  ["ACCEPT", "Accept observation"], ["DISPUTE", "Dispute observation"], ["NOTE", "Add note"],
  ["REQUEST_EVIDENCE", "Request additional evidence"], ["MANUAL_ISSUER_CONFIRMATION", "Send for manual issuer confirmation"],
  ["RECOMMEND_INSPECTION", "Recommend physical inspection"], ["RESOLVE", "Mark resolved"], ["REOPEN", "Reopen"],
];

export function FindingDetail({ id, onChanged }: { id: string; onChanged?: () => void }) {
  const { can } = useSession();
  const { data: f, reload } = useApi<any>(`/findings/${id}`);
  const [action, setAction] = useState("ACCEPT");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>();
  if (!f) return <div className="skeleton h-40" />;
  const submit = async () => {
    setBusy(true); setErr(undefined);
    try { await post(`/findings/${id}/actions`, { action, note: note || null }); setNote(""); await reload(); onChanged?.(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap gap-1.5"><SeverityBadge s={f.severity} /><CategoryBadge c={f.category} /><Badge>{f.agent}</Badge>
        <StatusBadge s={f.review_status} prefix="Review: " />{f.is_machine_generated && <Badge tone="accent">Machine-generated · advisory</Badge>}</div>
      <section><h3 className="text-xs font-semibold uppercase tracking-wide text-fg-dim">Reason</h3><p className="mt-1">{f.reason}</p></section>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-line p-3"><div className="text-[11px] text-fg-dim">Observed value</div><div className="mt-1 break-words font-mono text-xs">{f.observed_value ?? "—"}</div></div>
        <div className="rounded-lg border border-line p-3"><div className="text-[11px] text-fg-dim">Expected value / rule</div><div className="mt-1 break-words font-mono text-xs">{f.expected_value ?? "—"}</div></div>
      </div>
      {f.rule_code && <p className="text-xs text-fg-muted">Rule <span className="font-mono text-accent">{f.rule_code} v{f.rule_version}</span> (version pinned at evaluation time)</p>}
      {f.explanation && (
        <section className="rounded-lg border border-accent/20 bg-accent/5 p-3">
          <h3 className="text-xs font-semibold text-accent">Grounded explanation <span className="font-normal text-fg-dim">({f.explanation_method})</span></h3>
          <p className="mt-1 text-xs leading-relaxed text-fg-muted">{f.explanation}</p>
        </section>
      )}
      {f.regulatory_citations?.length > 0 && (
        <section className="space-y-2"><h3 className="text-xs font-semibold uppercase tracking-wide text-fg-dim">Regulatory Knowledge Index · citations</h3>
          {f.regulatory_citations.map((c: any, i: number) => <CitationCard key={i} c={c} compact />)}</section>
      )}
      {f.evidence_refs?.length > 0 && (
        <section className="space-y-2"><h3 className="text-xs font-semibold uppercase tracking-wide text-fg-dim">Evidence</h3>
          {f.evidence_refs.map((e: any, i: number) => e.index === "INSTITUTIONAL_EVIDENCE" ? <EvidenceCard key={i} e={e} /> : (
            <div key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-line px-3 py-2 text-xs">
              <FileText className="h-3.5 w-3.5 text-fg-dim" aria-hidden />
              <span>{e.filename ?? e.label ?? label(e.kind)}</span>{e.page && <Badge>p.{e.page}</Badge>}
              {e.doc_type && <Badge>{label(e.doc_type)}</Badge>}{e.connector && <Badge tone="accent">{e.connector} · {e.request_ref}</Badge>}
              {e.document_id && <button className="ml-auto inline-flex items-center gap-1 text-accent hover:underline" onClick={() => openDocument(e.document_id)}>Open original <ExternalLink className="h-3 w-3" /></button>}
            </div>
          ))}
        </section>
      )}
      <section className="rounded-lg border border-line bg-ink-850/60 p-3">
        <h3 className="text-xs font-semibold">Recommended next step</h3><p className="mt-1 text-xs text-fg-muted">{f.recommended_action}</p>
      </section>
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-dim">Review history</h3>
        {!f.review_history?.length && <p className="mt-1 text-xs text-fg-dim">No human action yet. The original automated result above is preserved unchanged.</p>}
        <ol className="mt-2 space-y-1.5">{f.review_history?.map((a: any) => (
          <li key={a.id} className="rounded-md border border-line px-3 py-2 text-xs"><span className="font-medium">{label(a.action)}</span>
            <span className="text-fg-dim"> by {a.actor.full_name} · {dt(a.created_at)} · {label(a.previous_status)} → {label(a.new_status)}</span>
            {a.note && <p className="mt-0.5 text-fg-muted">{a.note}</p>}</li>))}</ol>
      </section>
      {can("finding:review") && (
        <section className="space-y-2 border-t border-line pt-3">
          <h3 className="text-xs font-semibold">Record a human review action</h3>
          <Select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Action">
            {ACTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          <Textarea placeholder="Explanatory note (required for dispute, evidence request, inspection, resolve)" value={note} onChange={(e) => setNote(e.target.value)} />
          {err && <p role="alert" className="text-xs text-danger">{err}</p>}
          <Button variant="primary" onClick={submit} loading={busy}>Record action</Button>
        </section>
      )}
    </div>
  );
}

export function FindingsPanel({ applicationId, filter, empty }: { applicationId: string; filter?: Record<string, string>; empty?: string }) {
  const [cat, setCat] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const { data, reload } = useApi<any>(`/findings${qs({ application_id: applicationId, page_size: 100, category: cat, ...filter })}`);
  const items: any[] = data?.items ?? [];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={cat} onChange={(e) => setCat(e.target.value)} className="w-56" aria-label="Filter by category">
          <option value="">All categories</option>
          {["UNMET_REQUIREMENT", "UNRESOLVED_EVIDENCE", "SUSPECTED_ANOMALY", "MACHINE_OBSERVATION", "INFORMATION"].map((c) => <option key={c} value={c}>{label(c)}</option>)}
        </Select>
        <span className="text-xs text-fg-dim">{data?.total ?? "…"} finding(s) from the latest validation run</span>
      </div>
      {data && !items.length && <Card><EmptyState title={empty ?? "No findings"} body="Process the application to generate evidence-linked findings." /></Card>}
      <ul className="space-y-2">
        <AnimatePresence initial={false}>
          {items.map((f) => (
            <motion.li key={f.id} layout initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}>
              <button onClick={() => setOpen(f.id)} className="w-full rounded-lg border border-line bg-ink-900/70 p-3 text-left transition-colors hover:border-accent/40">
                <div className="flex flex-wrap items-center gap-1.5"><SeverityBadge s={f.severity} /><CategoryBadge c={f.category} />
                  <Badge>{f.agent}</Badge>{f.rule_code && <Badge tone="accent">{f.rule_code} v{f.rule_version}</Badge>}
                  <span className="ml-auto"><StatusBadge s={f.review_status} /></span></div>
                <p className="mt-1.5 text-sm font-medium">{f.title}</p>
                <p className="mt-0.5 line-clamp-2 text-xs text-fg-muted">{f.reason}</p>
                <p className="mt-1 text-[11px] text-fg-dim">{f.document_filename ?? "Application-level"}{f.page_no ? ` · page ${f.page_no}` : ""}
                  {f.regulatory_citations?.length ? ` · ${f.regulatory_citations.length} citation(s)` : ""}</p>
              </button>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)} title={items.find((i) => i.id === open)?.title ?? "Finding"}
        description="Evidence-linked, advisory observation. Inspect the evidence before recording an action." wide>
        {open && <FindingDetail id={open} onChanged={reload} />}
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------- application picker
export function ApplicationPicker({ value, onChange }: { value?: string; onChange: (id: string) => void }) {
  const { data } = useApi<any>("/applications?page_size=100");
  return (
    <Select value={value ?? ""} onChange={(e) => onChange(e.target.value)} className="w-full sm:w-96" aria-label="Application">
      <option value="">Select an application…</option>
      {data?.items.map((a: any) => <option key={a.id} value={a.id}>{a.reference_no} — {a.institution.name} ({label(a.status)})</option>)}
    </Select>
  );
}
