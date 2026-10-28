"use client";
import { ExternalLink, FileUp, Play, RefreshCw, Send } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useRef, useState } from "react";
import { FindingDetail, FindingsPanel, ProcessingTimeline, StatusBadge } from "@/components/domain";
import { PageHeader, useSession } from "@/components/shell";
import {
  Badge, Button, Card, CardBody, CardHeader, Dialog, EmptyState, ErrorState, Field, LoadingBlock, Select, Table, Tabs,
  TabsContent, TabsList, Td, Textarea,
} from "@/components/ui/primitives";
import { api, openDocument, post } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { dt, label } from "@/lib/utils";

const DOC_TYPES = ["APPLICATION_FORM", "FIRE_SAFETY_CERTIFICATE", "STRUCTURAL_STABILITY_CERTIFICATE", "OCCUPANCY_CERTIFICATE",
  "ACCREDITATION_CERTIFICATE", "AFFIDAVIT", "BUILDING_PLAN", "INFRASTRUCTURE_PHOTO", "OTHER"];

function Upload({ appId, onDone }: { appId: string; onDone: () => void }) {
  const [type, setType] = useState("APPLICATION_FORM");
  const [msg, setMsg] = useState<string>();
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const send = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true); setMsg(undefined);
    const results: string[] = [];
    for (const f of Array.from(files)) {
      const fd = new FormData();
      fd.append("file", f); fd.append("declared_type", type); fd.append("description", f.name);
      try { const d = await api(`/applications/${appId}/documents`, { method: "POST", body: fd }); results.push(`${f.name}: ${label(d.status)} (scan ${d.malware_scan_status})`); }
      catch (e) { results.push(`${f.name}: ${(e as Error).message}`); }
    }
    setMsg(results.join(" · ")); setBusy(false); onDone();
    if (input.current) input.current.value = "";
  };
  return (
    <div className="space-y-2 rounded-lg border border-dashed border-accent/40 bg-accent/5 p-4"
      onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); send(e.dataTransfer.files); }}>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Declared document type"><Select value={type} onChange={(e) => setType(e.target.value)} className="w-72">
          {DOC_TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}</Select></Field>
        <input ref={input} type="file" accept="application/pdf,image/png,image/jpeg" multiple className="sr-only" id="upload" onChange={(e) => send(e.target.files)} />
        <Button asChild variant="primary"><label htmlFor="upload" className="cursor-pointer">{busy ? "Uploading…" : <><FileUp className="h-4 w-4" />Choose files</>}</label></Button>
        <a href="/api/v1/demo/sample-pack" className="text-xs text-accent hover:underline">Download synthetic sample pack (.zip)</a>
      </div>
      <p className="text-[11px] text-fg-dim">PDF, PNG or JPEG, up to 20 MB. Files are content-type checked, hashed (SHA-256) and malware-scanned on upload. Drag and drop is supported.</p>
      {msg && <p role="status" className="text-xs text-fg-muted">{msg}</p>}
    </div>
  );
}

function OutcomeDialog({ app, open, onOpenChange, onDone }: { app: any; open: boolean; onOpenChange: (o: boolean) => void; onDone: () => void }) {
  const [outcome, setOutcome] = useState("RECTIFICATION_REQUESTED");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string>();
  const save = async () => {
    try { await post(`/applications/${app.id}/review-outcome`, { outcome, note }); onOpenChange(false); onDone(); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Record human review outcome"
      description="A scrutiny-stage outcome recorded by an authorized reviewer. It is not an approval or rejection and not a formal AICTE decision.">
      <div className="space-y-3">
        <Field label="Outcome"><Select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
          <option value="RECTIFICATION_REQUESTED">Request rectification from institution</option>
          <option value="READY_FOR_FURTHER_SCRUTINY">Ready for further scrutiny</option></Select></Field>
        <Field label="Reviewer note (required)"><Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Summarise the basis for this outcome" /></Field>
        {err && <p role="alert" className="text-sm text-danger">{err}</p>}
        <Button variant="primary" onClick={save} disabled={note.trim().length < 3}>Record outcome</Button>
      </div>
    </Dialog>
  );
}

export default function ApplicationWorkspace() {
  const { id } = useParams<{ id: string }>();
  const sp = useSearchParams();
  const router = useRouter();
  const { can } = useSession();
  const [tab, setTab] = useState(sp.get("tab") ?? "overview");
  const [fixtures, setFixtures] = useState(true);
  const [outcomeOpen, setOutcomeOpen] = useState(false);
  const [deepFinding, setDeepFinding] = useState<string | null>(sp.get("finding"));
  const [actionErr, setActionErr] = useState<string>();
  const { data: app, error, reload } = useApi<any>(`/applications/${id}`, {
    poll: (d: any) => (d?.status === "PROCESSING" || d?.latest_job?.status === "RUNNING" || d?.latest_job?.status === "QUEUED" ? 1200 : false),
  });
  const { data: docs, reload: reloadDocs } = useApi<any[]>(`/applications/${id}/documents`);
  const { data: vrs } = useApi<any>(tab === "verification" ? `/verification/requests?application_id=${id}&page_size=100` : null);
  const { data: activity, reload: reloadActivity } = useApi<any>(tab === "audit" || tab === "comments" ? `/applications/${id}/activity` : null);
  const { data: runs } = useApi<any[]>(tab === "runs" ? `/applications/${id}/runs` : null);
  const [comment, setComment] = useState("");

  if (error) return <ErrorState error={error} retry={reload} />;
  if (!app) return <LoadingBlock rows={8} />;
  const run = async (fn: () => Promise<unknown>) => {
    setActionErr(undefined);
    try { await fn(); await reload(); reloadDocs(); } catch (e) { setActionErr((e as Error).message); }
  };
  const editable = ["DRAFT", "RECTIFICATION_REQUESTED"].includes(app.status);
  const summary = app.preliminary_summary;
  const job = app.latest_job;

  return (
    <>
      <PageHeader title={app.reference_no}
        sub={<>{app.institution.name} · {app.category} · {app.academic_year} {app.is_synthetic && <Badge tone="warn" className="ml-1">synthetic</Badge>}</>}
        actions={<>
          <StatusBadge s={app.status} prefix="Workflow: " /><StatusBadge s={app.verification_status} prefix="Verification: " />
          {can("application:submit") && editable && <Button variant="primary" onClick={() => run(() => post(`/applications/${id}/submit`))}><Send className="h-4 w-4" />Submit</Button>}
          {can("application:process") && ["SUBMITTED", "VALIDATION_COMPLETE", "REVIEW_REQUIRED", "REVIEW_IN_PROGRESS", "READY_FOR_FURTHER_SCRUTINY"].includes(app.status) && <>
            <label className="flex items-center gap-1.5 text-xs text-fg-muted" title="Use labelled synthetic fixture text for pages without a text layer (e.g. the scanned affidavit) when OCR is unavailable">
              <input type="checkbox" checked={fixtures} onChange={(e) => setFixtures(e.target.checked)} /> fixture text</label>
            <Button variant="primary" onClick={() => run(() => post(`/applications/${id}/process`, { use_fixtures: fixtures }))}>
              {app.latest_run ? <><RefreshCw className="h-4 w-4" />Re-run validation</> : <><Play className="h-4 w-4" />Process</>}</Button></>}
          {can("review:outcome") && ["REVIEW_REQUIRED", "REVIEW_IN_PROGRESS", "VALIDATION_COMPLETE"].includes(app.status) &&
            <Button onClick={() => setOutcomeOpen(true)}>Record outcome</Button>}
        </>} />
      {actionErr && <p role="alert" className="mb-3 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">{actionErr}</p>}

      <Tabs value={tab} onValueChange={(v) => { setTab(v); router.replace(`/applications/${id}?tab=${v}`, { scroll: false }); }}>
        <TabsList items={[{ value: "overview", label: "Overview" }, { value: "documents", label: `Documents (${docs?.length ?? 0})` },
          { value: "findings", label: "Findings" }, { value: "verification", label: "Verification" }, { value: "comments", label: "Comments" },
          { value: "audit", label: "Audit history" }, { value: "runs", label: "Validation runs" }]} />

        <TabsContent value="overview" className="grid gap-4 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader title="Preliminary summary" sub={summary ? `Generated ${dt(summary.generated_at)}` : "Not yet processed"} />
            <CardBody className="space-y-3">
              {!summary ? <EmptyState title="No preliminary summary yet" body={app.status === "DRAFT" ? "Upload documents and submit the application, then process it." : "Process the application to run the agents."} /> : <>
                <p className="text-sm">{summary.headline}</p>
                <div className="flex flex-wrap gap-1.5">{Object.entries(summary.by_severity ?? {}).map(([k, v]) => <Badge key={k}>{k}: {v as number}</Badge>)}</div>
                <ul className="space-y-1.5">{summary.top_items?.map((t: any) => (
                  <li key={t.id}><button onClick={() => setDeepFinding(t.id)} className="flex w-full items-center gap-2 rounded-md border border-line px-3 py-2 text-left text-xs hover:border-accent/40">
                    <Badge tone={t.severity === "HIGH" ? "danger" : "warn"}>{t.severity}</Badge><span className="truncate">{t.title}</span></button></li>))}</ul>
                {summary.partial_failures?.length > 0 && <Badge tone="danger">Partial failure in: {summary.partial_failures.join(", ")}</Badge>}
                <p className="rounded-md bg-ink-850 px-3 py-2 text-[11px] text-fg-dim">{summary.disclaimer}</p></>}
              {app.latest_run && <p className="text-[11px] text-fg-dim">Run {app.latest_run.trigger.toLowerCase()} · pipeline {app.latest_run.pipeline_version} · embeddings {app.latest_run.embedding_model} · explainer {app.latest_run.explainer} · rules as of {app.latest_run.as_of_date}</p>}
            </CardBody>
          </Card>
          <Card className="lg:col-span-2">
            <CardHeader title="Orchestrator (LangGraph) timeline" sub={job ? `Job ${label(job.status)} · ${dt(job.created_at)}` : "No processing job yet"} />
            <CardBody>{job ? <ProcessingTimeline job={job} /> : <EmptyState title="Not processed" />}
              {job && ["QUEUED", "RUNNING"].includes(job.status) && can("application:process") &&
                <Button size="sm" variant="danger" className="mt-3" onClick={() => run(() => post(`/jobs/${job.id}/cancel`))}>Cancel at next safe point</Button>}
              {job && ["FAILED", "PARTIAL"].includes(job.status) && can("application:process") &&
                <Button size="sm" className="mt-3" onClick={() => run(() => post(`/jobs/${job.id}/retry`))}>Retry job</Button>}
              {job?.error && <p className="mt-2 text-xs text-danger">{job.error}</p>}
            </CardBody>
          </Card>
          <Card className="lg:col-span-5">
            <CardHeader title="Compliance checklist" sub="Applicable approved rule versions evaluated by the deterministic engine"
              action={<Link className="text-xs text-accent hover:underline" href={`/compliance?application=${id}`}>Open dual-index view</Link>} />
            <ChecklistTable appId={id} />
          </Card>
        </TabsContent>

        <TabsContent value="documents" className="space-y-3">
          {editable && can("document:upload") && <Upload appId={id} onDone={reloadDocs} />}
          <Card>{!docs ? <LoadingBlock /> : !docs.length ? <EmptyState title="No documents uploaded" /> : (
            <Table head={["File", "Declared → classified", "Status", "Extraction", "Pages", "SHA-256", ""]}>
              {docs.map((d) => (
                <tr key={d.id}>
                  <Td><div className="font-medium">{d.filename}</div><div className="text-[11px] text-fg-dim">{d.description}</div></Td>
                  <Td className="text-xs">{label(d.declared_type)} → <span className={d.doc_type && d.doc_type !== d.declared_type ? "text-warn" : ""}>{label(d.doc_type)}</span>
                    {d.classification_confidence != null && <span className="text-fg-dim"> ({(d.classification_confidence * 100).toFixed(0)}%)</span>}</Td>
                  <Td><StatusBadge s={d.status} /><div className="mt-1 text-[10px] text-fg-dim">scan {label(d.malware_scan_status)}</div></Td>
                  <Td className="text-xs">{d.extraction_method ? <><Badge tone={d.extraction_method === "synthetic-fixture" ? "warn" : d.extraction_method === "none" ? "danger" : "accent"}>{d.extraction_method}</Badge>
                    <div className="mt-1 text-fg-dim">{d.field_count} fields · conf {d.extraction_confidence?.toFixed(2) ?? "—"}</div></> : "—"}</Td>
                  <Td className="tabular-nums">{d.page_count ?? "—"}</Td>
                  <Td className="font-mono text-[10px] text-fg-dim">{d.sha256.slice(0, 12)}…</Td>
                  <Td className="whitespace-nowrap text-xs">
                    <Link href={`/document-intelligence?doc=${d.id}`} className="text-accent hover:underline">Evidence</Link>
                    {d.status !== "QUARANTINED" && <button className="ml-3 inline-flex items-center gap-1 text-accent hover:underline" onClick={() => openDocument(d.id)}>Original<ExternalLink className="h-3 w-3" /></button>}
                  </Td>
                </tr>))}
            </Table>)}</Card>
        </TabsContent>

        <TabsContent value="findings"><FindingsPanel applicationId={id} /></TabsContent>

        <TabsContent value="verification">
          <Card>{!vrs ? <LoadingBlock /> : !vrs.items.length ? <EmptyState title="No verification requests yet" /> : (
            <Table head={["Document", "Connector", "Status", "Reference", "Summary", "Time"]}>
              {vrs.items.map((v: any) => (
                <tr key={v.id}>
                  <Td className="text-xs">{v.document_filename ?? "—"}</Td>
                  <Td className="text-xs">{v.connector_key}{v.is_simulated && <Badge className="ml-1">simulated</Badge>}</Td>
                  <Td><StatusBadge s={v.effective_status} />{v.manual_outcome && <div className="mt-1 text-[10px] text-fg-dim">manual (was {label(v.status)})</div>}</Td>
                  <Td className="font-mono text-[11px]">{v.request_ref}</Td>
                  <Td className="max-w-md text-xs text-fg-muted">{v.manual_note ?? v.response_meta?.summary}
                    {v.mismatches?.map((m: any, i: number) => <div key={i} className="mt-1 text-danger">{m.field}: document {m.document_value} ≠ issuer {m.issuer_value}</div>)}</Td>
                  <Td className="whitespace-nowrap text-xs text-fg-dim">{dt(v.responded_at ?? v.requested_at)}</Td>
                </tr>))}
            </Table>)}</Card>
        </TabsContent>

        <TabsContent value="comments" className="space-y-3">
          {can("finding:review") || can("document:upload") ? (
            <Card className="p-3 space-y-2"><Textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Add a comment for the review record" aria-label="Comment" />
              <Button size="sm" variant="primary" disabled={!comment.trim()} onClick={() => run(async () => { await post(`/applications/${id}/comments`, { note: comment }); setComment(""); reloadActivity(); })}>Post comment</Button></Card>) : null}
          <ActivityList items={activity?.review_actions} />
        </TabsContent>

        <TabsContent value="audit" className="grid gap-4 lg:grid-cols-2">
          <Card><CardHeader title="Human review actions" /><div className="p-3"><ActivityList items={activity?.review_actions} /></div></Card>
          <Card><CardHeader title="Audit events (hash-chained)" />
            <ul className="max-h-[560px] divide-y divide-line overflow-y-auto scroll-thin">{activity?.audit_events?.map((e: any) => (
              <li key={e.id} className="px-4 py-2 text-xs"><div className="flex justify-between gap-2"><span className="font-mono text-accent">{e.action}</span><span className="text-fg-dim">#{e.seq} · {dt(e.ts)}</span></div>
                <div className="text-fg-muted">{e.actor_email} ({label(e.actor_role)})</div>
                {Object.keys(e.details ?? {}).length > 0 && <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[10px] text-fg-dim">{JSON.stringify(e.details)}</pre>}</li>))}</ul></Card>
        </TabsContent>

        <TabsContent value="runs">
          <Card>{!runs ? <LoadingBlock /> : (
            <Table head={["Started", "Trigger", "Status", "Findings", "Pinned rule versions", "Models"]}>
              {runs.map((r) => (
                <tr key={r.id}>
                  <Td className="whitespace-nowrap text-xs">{dt(r.started_at)}</Td><Td className="text-xs">{label(r.trigger)}</Td>
                  <Td><StatusBadge s={r.status} /></Td><Td className="tabular-nums">{r.finding_count}</Td>
                  <Td className="text-xs">{r.rule_versions.map((v: any) => <Badge key={v.rule_code} className="mb-1 mr-1">{v.rule_code} v{v.version}</Badge>)}</Td>
                  <Td className="text-[11px] text-fg-dim">{r.pipeline_version}<br />{r.embedding_model}<br />{r.explainer}</Td>
                </tr>))}
            </Table>)}</Card>
        </TabsContent>
      </Tabs>

      <OutcomeDialog app={app} open={outcomeOpen} onOpenChange={setOutcomeOpen} onDone={reload} />
      <Dialog open={!!deepFinding} onOpenChange={(o) => !o && setDeepFinding(null)} title="Finding" wide
        description="Evidence-linked, advisory observation.">{deepFinding && <FindingDetail id={deepFinding} onChanged={reload} />}</Dialog>
    </>
  );
}

function ChecklistTable({ appId }: { appId: string }) {
  const { data } = useApi<any>(`/applications/${appId}/compliance`);
  if (!data) return <LoadingBlock rows={3} />;
  if (!data.checklist.length) return <EmptyState title="No rules evaluated yet" />;
  return (
    <Table head={["Rule", "Requirement", "Clause", "Result", "Severity"]}>
      {data.checklist.map((c: any) => (
        <tr key={c.rule_code}>
          <Td className="font-mono text-xs text-accent">{c.rule_code} v{c.version}</Td>
          <Td>{c.title}{c.is_synthetic && <Badge tone="warn" className="ml-2">synthetic rule</Badge>}</Td>
          <Td className="text-xs">{c.clause_ref ?? "—"}{!c.source_located && <Badge tone="warn" className="ml-1">source unverified</Badge>}</Td>
          <Td><StatusBadge s={c.result} /></Td><Td className="text-xs">{c.severity}</Td>
        </tr>))}
    </Table>
  );
}

function ActivityList({ items }: { items?: any[] }) {
  if (!items) return <LoadingBlock rows={3} />;
  if (!items.length) return <EmptyState title="No activity yet" />;
  return (
    <ol className="space-y-2">{items.map((a) => (
      <li key={a.id} className="rounded-lg border border-line bg-ink-900/70 px-3 py-2 text-xs">
        <div className="flex flex-wrap justify-between gap-2"><span className="font-medium">{label(a.action)}{a.new_status && <span className="text-fg-dim"> → {label(a.new_status)}</span>}</span>
          <span className="text-fg-dim">{a.actor.full_name} · {dt(a.created_at)}</span></div>
        {a.note && <p className="mt-1 text-fg-muted">{a.note}</p>}
      </li>))}</ol>
  );
}
