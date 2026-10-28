"use client";
import { motion } from "framer-motion";
import { Clock, Database, FolderKanban, ShieldCheck, Sparkles, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { HBars, StackedBars } from "@/components/charts";
import { StatusBadge } from "@/components/domain";
import { PageHeader } from "@/components/shell";
import { Badge, Button, Card, CardBody, CardHeader, ErrorState, LoadingBlock, Stat } from "@/components/ui/primitives";
import { useApi } from "@/lib/hooks";
import { dt, duration } from "@/lib/utils";

const CATS = ["UNMET_REQUIREMENT", "UNRESOLVED_EVIDENCE", "SUSPECTED_ANOMALY", "MACHINE_OBSERVATION", "INFORMATION"];

export default function Dashboard() {
  const { data: m, error, reload } = useApi<any>("/dashboard/metrics", { poll: 20000 });
  if (error) return <ErrorState error={error} retry={reload} />;
  if (!m) return <LoadingBlock rows={8} />;
  const highOpen = (m.findings.open_by_severity.HIGH ?? 0) + (m.findings.open_by_severity.CRITICAL ?? 0);
  const trend = m.deficiency_trend.slice(-21);
  return (
    <>
      <PageHeader title="Operational overview" sub="Preliminary, machine-assisted scrutiny status across synthetic applications."
        actions={<Button asChild variant="primary"><Link href="/demo"><Sparkles className="h-4 w-4" />Run guided demo</Link></Button>} />
      <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Applications" value={m.applications.total} icon={<FolderKanban className="h-4 w-4" />} sub={`${m.applications.processing} processing`} />
        <Stat label="Pending human review" value={m.applications.pending_review} tone="warn" icon={<TriangleAlert className="h-4 w-4" />} sub="Review required + in progress" />
        <Stat label="Open high-severity items" value={highOpen} tone="danger" sub={`${m.findings.total} findings in total`} />
        <Stat label="Manual issuer confirmations" value={m.verification.manual_queue} tone="accent" icon={<ShieldCheck className="h-4 w-4" />} sub="Awaiting verification officer" />
        <Stat label="Mean processing time" value={duration(m.processing.duration.mean_seconds)} icon={<Clock className="h-4 w-4" />}
          sub={`n = ${m.processing.duration.n} job(s) · ${m.processing.failed} failed`} />
      </motion.div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Findings by category, per day" sub="Last 21 days · advisory machine findings, not formal deficiencies" />
          <CardBody><StackedBars data={trend} keys={CATS} xKey="date" /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Issuer verification outcomes" sub="All connector requests, incl. manual outcomes" />
          <CardBody>
            <HBars data={Object.entries(m.verification.outcomes).map(([name, value]) => ({ name, value: value as number }))
              .sort((a, b) => b.value - a.value)} />
            <p className="mt-2 text-[11px] text-fg-dim">Source unavailable is never treated as evidence of forgery.</p>
          </CardBody>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Recently updated applications" action={<Link href="/applications" className="text-xs text-accent hover:underline">View all</Link>} />
          <ul className="divide-y divide-line">
            {m.recent_applications.map((a: any) => (
              <li key={a.id}>
                <Link href={`/applications/${a.id}`} className="flex flex-wrap items-center gap-3 px-4 py-3 hover:bg-ink-850">
                  <span className="font-mono text-xs text-accent">{a.reference_no}</span>
                  <span className="min-w-0 flex-1 truncate text-sm">{a.institution}</span>
                  <StatusBadge s={a.status} /><StatusBadge s={a.verification_status} />
                  <span className="text-[11px] text-fg-dim">{dt(a.updated_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Mean step duration (ms)" sub="Orchestrator steps across processing jobs" />
            <CardBody><HBars data={Object.entries(m.processing.step_mean_ms).map(([name, value]) => ({ name, value: value as number }))} unit=" ms" /></CardBody>
          </Card>
          {m.index && (
            <Card>
              <CardHeader title="Dual embedding indexes" sub={`${m.index.embedding_model} · ${m.index.embedding_kind} · ${m.index.embedding_dim}d`} />
              <CardBody className="grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-lg border border-line p-3"><Database className="mb-1 h-4 w-4 text-accent" aria-hidden />
                  <div className="text-xs text-fg-dim">Regulatory Knowledge</div><div className="font-semibold tabular-nums">{m.index.regulatory_index.chunks} chunks</div>
                  <div className="text-[11px] text-fg-dim">{m.index.regulatory_index.sources_indexed}/{m.index.regulatory_index.sources} sources</div></div>
                <div className="rounded-lg border border-line p-3"><Database className="mb-1 h-4 w-4 text-ok" aria-hidden />
                  <div className="text-xs text-fg-dim">Institutional Evidence</div><div className="font-semibold tabular-nums">{m.index.evidence_index.chunks} chunks</div>
                  <div className="text-[11px] text-fg-dim">tenant-filtered in SQL</div></div>
                {(m.index.regulatory_index.stale_chunks > 0 || m.index.evidence_index.stale_chunks > 0) && <Badge tone="warn">Stale chunks — re-index required</Badge>}
              </CardBody>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
