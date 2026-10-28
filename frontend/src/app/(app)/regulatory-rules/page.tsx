"use client";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApplicationPicker, CitationCard, SourceBadges, StatusBadge } from "@/components/domain";
import { PageHeader, useSession } from "@/components/shell";
import {
  Badge, Button, Card, CardBody, CardHeader, Dialog, EmptyState, Field, Input, LoadingBlock, Select, Table, Tabs, TabsContent,
  TabsList, Td, Textarea,
} from "@/components/ui/primitives";
import { api, post } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { cn, dt, label } from "@/lib/utils";

function RuleDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { can } = useSession();
  const { data: r, reload } = useApi<any>(`/rules/${id}`);
  const [appId, setAppId] = useState("");
  const [test, setTest] = useState<any>();
  const [editing, setEditing] = useState(false);
  const [cond, setCond] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string>();
  useEffect(() => { setTest(undefined); setErr(undefined); }, [id]);
  if (!r) return <LoadingBlock rows={6} />;
  const act = async (fn: () => Promise<unknown>) => { setErr(undefined); try { await fn(); await reload(); onChanged(); } catch (e) { setErr((e as Error).message); } };
  const newVersion = () => act(async () => {
    const created = await post("/rules", { rule_code: r.rule_code, title: r.title, description: r.description, category: r.category,
      source_id: r.source_id, clause_ref: r.clause_ref, effective_from: r.effective_from, severity: r.severity,
      finding_category: r.finding_category, reviewer_guidance: r.reviewer_guidance, retrieval_query: r.retrieval_query,
      required_documents: r.required_documents, required_fields: r.required_fields, test_cases: r.test_cases,
      condition: JSON.parse(cond), change_note: note });
    setEditing(false);
    window.history.replaceState(null, "", `/regulatory-rules?rule=${created.id}`);
  });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-accent">{r.rule_code} v{r.version}</span><StatusBadge s={r.status} />
        {r.is_synthetic && <Badge tone="warn">Synthetic demonstration rule</Badge>}<Badge>{r.severity}</Badge><Badge>{label(r.finding_category)}</Badge>
      </div>
      <div><h2 className="text-base font-semibold">{r.title}</h2><p className="mt-1 text-sm text-fg-muted">{r.description}</p></div>
      <div className="grid gap-2 text-xs sm:grid-cols-2">
        <div className="rounded-lg border border-line p-3"><div className="text-fg-dim">Source & clause</div>
          <div className="mt-1">{r.source ? `${r.source.title} · ${r.source.edition}` : "—"} · clause {r.clause_ref ?? "—"}</div>
          {r.source && <div className="mt-1"><SourceBadges h={{ ...r.source, source_status: r.source.status }} /></div>}</div>
        <div className="rounded-lg border border-line p-3"><div className="text-fg-dim">Effective</div><div className="mt-1">{r.effective_from ?? "—"} → {r.effective_to ?? "open"}</div>
          <div className="mt-1 text-fg-dim">Approved {r.approved_at ? dt(r.approved_at) : "—"}{r.change_note ? ` · ${r.change_note}` : ""}</div></div>
      </div>
      <div><div className="mb-1 text-xs font-semibold">Structured condition</div>
        <pre className="max-h-56 overflow-auto rounded-lg border border-line bg-ink-950 p-3 font-mono text-[11px] text-fg-muted scroll-thin">{JSON.stringify(r.condition, null, 2)}</pre></div>
      {r.reviewer_guidance && <p className="text-xs text-fg-muted"><span className="font-semibold text-fg">Reviewer guidance: </span>{r.reviewer_guidance}</p>}
      <div><div className="mb-1 text-xs font-semibold">Versions</div><div className="flex flex-wrap gap-1">{r.versions.map((v: any) => (
        <a key={v.id} href={`/regulatory-rules?rule=${v.id}`}><Badge tone={v.id === r.id ? "accent" : "neutral"}>v{v.version} · {label(v.status)}</Badge></a>))}</div></div>

      <Card className="p-3 space-y-2">
        <div className="text-xs font-semibold">Test preview (nothing is persisted)</div>
        <div className="flex flex-wrap gap-2"><ApplicationPicker value={appId} onChange={setAppId} />
          <Button size="sm" onClick={async () => setTest(await post(`/rules/${id}/test`, { application_id: appId || null }))}>Run tests{appId && " + preview"}</Button></div>
        {test && <>
          {test.tests.map((t: any) => <div key={t.name} className="text-xs">{t.passed ? "✓" : "✗"} {t.name}: expected {t.expected}, got <StatusBadge s={t.actual} /></div>)}
          {!test.tests.length && <p className="text-xs text-fg-dim">No labelled test cases on this version.</p>}
          {test.preview && <div className="text-xs">Preview on {test.preview.reference_no}: <StatusBadge s={test.preview.result} />
            <ul className="mt-1 space-y-0.5 text-fg-muted">{test.preview.outcomes.map((o: any, i: number) => <li key={i}>· {o.reason}</li>)}</ul></div>}
        </>}
      </Card>

      {can("rule:edit") && <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => { setCond(JSON.stringify(r.condition, null, 2)); setNote(""); setEditing(true); }}>Create new version</Button>
        {r.status === "DRAFT" && <Button size="sm" onClick={() => act(() => post(`/rules/${id}/submit`))}>Submit for approval</Button>}
        {can("rule:approve") && r.status === "PENDING_APPROVAL" && <Button size="sm" variant="ok" onClick={() => act(() => post(`/rules/${id}/approve`))}>Approve</Button>}
        {can("rule:approve") && r.status !== "RETIRED" && <Button size="sm" variant="danger" onClick={() => act(() => post(`/rules/${id}/retire`))}>Retire</Button>}
      </div>}
      {err && <p role="alert" className="text-xs text-danger">{err}</p>}
      <Dialog open={editing} onOpenChange={setEditing} title={`New version of ${r.rule_code}`} description="Creates a DRAFT version. Earlier versions and historical evaluations are never modified.">
        <div className="space-y-3">
          <Field label="Condition (JSON)" hint="Operators: documents_present, field_present, date_on_or_after, date_within_days, name_matches, intake_within_approved, equals, min_count; combine with all/any.">
            <Textarea value={cond} onChange={(e) => setCond(e.target.value)} className="min-h-[220px] font-mono text-xs" /></Field>
          <Field label="Change note"><Input value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          <Button variant="primary" onClick={newVersion}>Save draft version</Button>
          {err && <p role="alert" className="text-xs text-danger">{err}</p>}
        </div>
      </Dialog>
    </div>
  );
}

function Sources() {
  const { can } = useSession();
  const { data, reload } = useApi<any[]>("/regulatory/sources", { poll: (d: any) => (d?.some((s: any) => s.index_status === "PENDING") ? 1500 : false) });
  const { data: status } = useApi<any>("/rag/status");
  const [view, setView] = useState<any>();
  const [upload, setUpload] = useState(false);
  const [err, setErr] = useState<string>();
  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault(); setErr(undefined);
    try { await api("/regulatory/sources", { method: "POST", body: new FormData(e.currentTarget) }); setUpload(false); reload(); }
    catch (x) { setErr((x as Error).message); }
  };
  return (
    <div className="space-y-4">
      {status && <p className="text-xs text-fg-muted">Embedding model <span className="font-mono">{status.embedding_model}</span> ({status.embedding_kind}, {status.embedding_dim}d) ·
        Regulatory index {status.regulatory_index.chunks} chunks ({status.regulatory_index.stale_chunks} stale) · Evidence index {status.evidence_index.chunks} chunks — stored in separate tables.</p>}
      {can("source:manage") && <Button size="sm" variant="primary" onClick={() => setUpload(true)}>Import source PDF</Button>}
      <Card>{!data ? <LoadingBlock /> : (
        <Table head={["Source", "Type", "Status", "Effective", "Index", "Chunks", ""]}>
          {data.map((s) => (
            <tr key={s.id}>
              <Td><div className="font-medium">{s.title}</div><div className="text-[11px] text-fg-dim">{s.edition}</div><SourceBadges h={s} /></Td>
              <Td className="text-xs">{label(s.source_type)}</Td><Td><StatusBadge s={s.status} /></Td>
              <Td className="whitespace-nowrap text-xs">{s.effective_from ?? "—"} → {s.effective_to ?? "open"}</Td>
              <Td><StatusBadge s={s.index_status} />{s.index_error && <div className="text-[10px] text-danger">{s.index_error}</div>}</Td>
              <Td className="tabular-nums">{s.chunk_count}</Td>
              <Td className="whitespace-nowrap text-xs"><button className="text-accent hover:underline" onClick={async () => setView(await api(`/regulatory/sources/${s.id}/chunks`))}>Chunks</button>
                {can("source:manage") && <button className="ml-3 text-accent hover:underline" onClick={() => post(`/regulatory/sources/${s.id}/reindex`).then(reload)}>Re-index</button>}</Td>
            </tr>))}
        </Table>)}</Card>
      <Dialog open={!!view} onOpenChange={(o) => !o && setView(undefined)} wide title={view ? `${view.source.title} · ${view.source.edition}` : ""} description="Indexed clause-level chunks with provenance">
        <div className="space-y-2">{view?.chunks.map((c: any) => <CitationCard key={c.id} c={{ ...c, title: view.source.title, edition: view.source.edition, page: c.page, is_synthetic: view.source.is_synthetic, source_type: view.source.source_type, source_status: c.status }} />)}</div>
      </Dialog>
      <Dialog open={upload} onOpenChange={setUpload} title="Import regulatory source" description="Indexed into the Regulatory Knowledge Index by a background job. Only import authorized source documents.">
        <form onSubmit={submit} className="space-y-3">
          <Field label="PDF file"><input name="file" type="file" accept="application/pdf" required className="text-xs" /></Field>
          <Field label="Title"><Input name="title" required /></Field>
          <div className="grid grid-cols-2 gap-3"><Field label="Edition"><Input name="edition" required /></Field>
            <Field label="Type"><Select name="source_type">{["HANDBOOK", "AMENDMENT", "CIRCULAR", "CHECKLIST", "INTERNAL_INTERPRETATION"].map((t) => <option key={t} value={t}>{label(t)}</option>)}</Select></Field></div>
          <div className="grid grid-cols-2 gap-3"><Field label="Effective from"><Input name="effective_from" type="date" /></Field><Field label="Source URL"><Input name="source_url" type="url" /></Field></div>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" name="is_official" value="true" /> I confirm this is an official, authorized source</label>
          {err && <p role="alert" className="text-xs text-danger">{err}</p>}
          <Button type="submit" variant="primary">Import and index</Button>
        </form>
      </Dialog>
    </div>
  );
}

export default function RegulatoryRules() {
  const sp = useSearchParams();
  const { data, reload } = useApi<any[]>("/rules");
  const [sel, setSel] = useState<string | null>(sp.get("rule"));
  useEffect(() => { setSel(sp.get("rule")); }, [sp]);
  useEffect(() => { if (!sel && data?.length) setSel(data[0].id); }, [data, sel]);
  return (
    <>
      <PageHeader title="Regulatory rules & sources" sub="Administrator-managed, versioned rule registry with approval workflow, test previews and indexed regulatory sources. Demonstration rules are synthetic and labelled." />
      <Tabs defaultValue="rules">
        <TabsList items={[{ value: "rules", label: "Rule registry" }, { value: "sources", label: "Regulatory sources" }]} />
        <TabsContent value="rules" className="grid gap-4 xl:grid-cols-12">
          <Card className="xl:col-span-4">{!data ? <LoadingBlock /> : !data.length ? <EmptyState title="No rules" /> : (
            <ul className="divide-y divide-line">{data.map((r) => (
              <li key={r.id}><button onClick={() => setSel(r.id)} aria-current={sel === r.id} className={cn("w-full px-3 py-2.5 text-left hover:bg-ink-850", sel === r.id && "bg-accent/10")}>
                <div className="flex items-center justify-between gap-2"><span className="font-mono text-xs text-accent">{r.rule_code} v{r.version}</span><StatusBadge s={r.status} /></div>
                <div className="mt-0.5 truncate text-xs text-fg-muted">{r.title}</div></button></li>))}</ul>)}</Card>
          <Card className="xl:col-span-8"><CardBody>{sel ? <RuleDetail id={sel} onChanged={reload} /> : <EmptyState title="Select a rule" />}</CardBody></Card>
        </TabsContent>
        <TabsContent value="sources"><Sources /></TabsContent>
      </Tabs>
    </>
  );
}
