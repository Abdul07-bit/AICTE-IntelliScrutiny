"use client";
import { useState } from "react";
import { HBars } from "@/components/charts";
import { PageHeader, useSession } from "@/components/shell";
import { Badge, Button, Card, CardBody, CardHeader, ErrorState, LoadingBlock, Stat, Table, Td } from "@/components/ui/primitives";
import { post } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { duration, label, pct } from "@/lib/utils";

function PR({ name, m, n }: { name: string; m: any; n: number }) {
  return (
    <tr><Td>{name}</Td><Td className="tabular-nums">{pct(m.precision)}</Td><Td className="tabular-nums">{pct(m.recall)}</Td>
      <Td className="tabular-nums text-xs text-fg-muted">TP {m.tp} · FP {m.fp} · FN {m.fn}{m.tn != null ? ` · TN ${m.tn}` : ""}</Td><Td className="tabular-nums">{n}</Td></tr>
  );
}

export default function Reports() {
  const { can } = useSession();
  const { data: r, error, reload } = useApi<any>("/reports/summary");
  const [jobId, setJobId] = useState<string>();
  const { data: job } = useApi<any>(jobId ? `/jobs/${jobId}` : null, {
    poll: (j: any) => { if (j && !["QUEUED", "RUNNING"].includes(j.status)) { reload(); return false; } return 800; } });
  if (error) return <ErrorState error={error} retry={reload} />;
  if (!r) return <LoadingBlock rows={8} />;
  const ev = r.latest_evaluation;
  return (
    <>
      <PageHeader title="Reports & evaluation" sub={r.label}
        actions={can("evaluation:run") && <Button variant="primary" loading={job && ["QUEUED", "RUNNING"].includes(job.status)}
          onClick={async () => setJobId((await post("/reports/evaluations", { samples: 24 })).id)}>Run fixture evaluation (n=24)</Button>} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Submission → preliminary validation" value={duration(r.submission_to_preliminary_validation.mean_seconds)} sub={`mean · median ${duration(r.submission_to_preliminary_validation.median_seconds)} · n = ${r.submission_to_preliminary_validation.n}`} />
        <Stat label="Manual review time" value={duration(r.manual_review_time.mean_seconds)} sub={`n = ${r.manual_review_time.n} · ${r.manual_review_time.definition}`} />
        <Stat label="Needing additional evidence" value={pct(r.applications_requiring_additional_evidence.rate)} sub={`${r.applications_requiring_additional_evidence.count} of ${r.applications_requiring_additional_evidence.denominator} processed`} />
        <Stat label="Issuer source reachable" value={pct(r.verification.success_rate)} sub={`${r.verification.source_reachable} of ${r.verification.requests} connector requests`} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Prototype evaluation on labelled synthetic fixtures" sub={ev ? `${ev.label} · pipeline ${ev.pipeline_version}` : "No evaluation run yet"} action={<Badge tone="warn">synthetic</Badge>} />
          {ev ? <>
            <Table head={["Metric", "Precision", "Recall", "Counts", "n"]}>
              <tr><Td>OCR / field extraction accuracy</Td><Td className="tabular-nums" colSpan={2}>{pct(ev.metrics.ocr_field_extraction_accuracy)}</Td>
                <Td className="text-xs text-fg-muted">{Object.entries(ev.metrics.per_field_accuracy).map(([k, v]) => `${label(k)} ${pct(v as number)}`).join(" · ")}</Td><Td>{ev.sample_counts.fields} fields</Td></tr>
              <tr><Td>Document classification accuracy</Td><Td className="tabular-nums" colSpan={3}>{pct(ev.metrics.classification_accuracy)}</Td><Td>{ev.sample_counts.documents}</Td></tr>
              <PR name="Required-document (missing) detection" m={ev.metrics.required_document_detection} n={ev.sample_counts.required_document_slots} />
              <PR name="Cross-record name mismatch detection" m={ev.metrics.cross_record_mismatch_detection} n={ev.sample_counts.mismatch_cases} />
              <PR name="Duplicate detection" m={ev.metrics.duplicate_detection} n={ev.sample_counts.duplicate_pairs} />
            </Table>
            <CardBody className="space-y-1 border-t border-line text-[11px] text-fg-dim"><p>{ev.metrics.extraction_method}</p><p className="text-warn">{ev.metrics.caveat}</p></CardBody>
          </> : <CardBody className="text-sm text-fg-muted">Run an evaluation to compute metrics.</CardBody>}
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Anomaly flag review outcomes" sub={`Suspected-anomaly findings (n = ${r.anomaly_flag_review_outcomes.denominator})`} />
            <CardBody><HBars data={Object.entries(r.anomaly_flag_review_outcomes.by_review_status).map(([name, value]) => ({ name, value: value as number }))} /></CardBody>
          </Card>
          <Card>
            <CardHeader title="Verification outcomes by connector" />
            <Table head={["Connector", "Outcomes"]}>
              {Object.entries(r.verification.by_connector).map(([k, v]: any) => (
                <tr key={k}><Td className="font-mono text-xs">{k}</Td><Td className="text-xs">{Object.entries(v).map(([s, n]) => `${label(s)} ${n}`).join(" · ")}</Td></tr>))}
            </Table>
          </Card>
          <Card>
            <CardHeader title="Background job failure rates" />
            <Table head={["Job kind", "Total", "Failed", "Partial", "Failure rate"]}>
              {Object.entries(r.background_jobs).map(([k, v]: any) => (
                <tr key={k}><Td className="text-xs">{label(k)}</Td><Td>{v.total}</Td><Td>{v.failed}</Td><Td>{v.partial}</Td><Td>{pct(v.failure_rate)}</Td></tr>))}
            </Table>
          </Card>
        </div>
      </div>
    </>
  );
}
