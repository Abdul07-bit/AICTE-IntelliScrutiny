"use client";
import { Database, Search } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApplicationPicker, CitationCard, EvidenceCard, StatusBadge } from "@/components/domain";
import { PageHeader } from "@/components/shell";
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, Input, LoadingBlock, Table, Td } from "@/components/ui/primitives";
import { post, qs } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { cn, dt, label } from "@/lib/utils";

function IndexColumn({ title, sub, tone, children }: { title: string; sub: string; tone: "accent" | "ok"; children: React.ReactNode }) {
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex items-center gap-2"><Database className={cn("h-4 w-4", tone === "accent" ? "text-accent" : "text-ok")} aria-hidden />
        <div><h3 className="text-sm font-semibold">{title}</h3><p className="text-[11px] text-fg-dim">{sub}</p></div></div>
      {children}
    </div>
  );
}

export default function Compliance() {
  const sp = useSearchParams();
  const router = useRouter();
  const [appId, setAppId] = useState(sp.get("application") ?? "");
  const { data, error } = useApi<any>(appId ? `/applications/${appId}/compliance` : null);
  const [rule, setRule] = useState<string>();
  const [q, setQ] = useState("");
  const [asOf, setAsOf] = useState("");
  const [play, setPlay] = useState<{ reg: any[]; ev: any[] }>();
  const [busy, setBusy] = useState(false);
  const { data: unresolved } = useApi<any>(appId ? `/findings${qs({ application_id: appId, category: "UNRESOLVED_EVIDENCE", page_size: 100 })}` : null);
  useEffect(() => { if (data?.retrievals?.length && !rule) setRule(data.retrievals[0].rule_code); }, [data, rule]);
  const r = data?.retrievals?.find((x: any) => x.rule_code === rule);

  const search = async () => {
    if (q.trim().length < 2) return;
    setBusy(true);
    try {
      const [reg, ev] = await Promise.all([
        post("/rag/search/regulatory", { query: q, as_of: asOf || null, k: 4 }),
        appId ? post("/rag/search/evidence", { query: q, application_id: appId, k: 4 }) : Promise.resolve({ hits: [] }),
      ]);
      setPlay({ reg: reg.hits, ev: ev.hits });
    } finally { setBusy(false); }
  };

  return (
    <>
      <PageHeader title="Compliance — dual-index reconciliation"
        sub="For each applicable rule: regulatory clauses from the Regulatory Knowledge Index, permitted evidence from the Institutional Evidence Index, and the exact deterministic comparison — shown separately and stored with the run." />
      <div className="mb-4"><ApplicationPicker value={appId} onChange={(v) => { setAppId(v); setRule(undefined); router.replace(`/compliance${qs({ application: v })}`); }} /></div>
      {!appId ? <Card><EmptyState title="Select an application" body="Choose a processed application to inspect its stored retrieval and rule evaluation records." /></Card>
        : error ? <Card><EmptyState title="Unavailable" body={error.message} /></Card> : !data ? <LoadingBlock rows={6} /> : !data.run ? <Card><EmptyState title="This application has not been processed yet" /></Card> : (
        <div className="grid gap-4 xl:grid-cols-12">
          <Card className="xl:col-span-3">
            <CardHeader title="Applicable rules" sub={`As of ${data.run.as_of_date} · ${data.run.embedding_model}`} />
            <ul className="divide-y divide-line">{data.retrievals.map((x: any) => (
              <li key={x.rule_code}><button onClick={() => setRule(x.rule_code)} aria-current={rule === x.rule_code}
                className={cn("w-full px-3 py-2.5 text-left hover:bg-ink-850", rule === x.rule_code && "bg-accent/10")}>
                <div className="flex items-center justify-between gap-2"><span className="font-mono text-xs text-accent">{x.rule_code} v{x.rule_version}</span><StatusBadge s={x.result} /></div>
                <div className="mt-0.5 line-clamp-2 text-[11px] text-fg-muted">{data.checklist.find((c: any) => c.rule_code === x.rule_code)?.title}</div>
              </button></li>))}</ul>
          </Card>
          <div className="space-y-4 xl:col-span-9">
            {r && (
              <Card>
                <CardHeader title={`${r.rule_code} v${r.rule_version} — ${label(r.result)}`} sub={`Retrieval query: “${r.query}” · stored ${dt(r.timestamp)}`}
                  action={!r.source_located && <Badge tone="warn">Source clause not located — needs authorized review</Badge>} />
                <CardBody className="grid gap-4 lg:grid-cols-2">
                  <IndexColumn title="Index 1 · Regulatory Knowledge" sub="Edition in force on the reference date; superseded text excluded" tone="accent">
                    {r.regulatory_hits.length ? r.regulatory_hits.map((h: any) => <CitationCard key={h.chunk_id} c={h} />) : <EmptyState title="No regulatory text retrieved" />}
                  </IndexColumn>
                  <IndexColumn title="Index 2 · Institutional Evidence" sub="Filtered to this institution in SQL before similarity ranking" tone="ok">
                    {r.evidence_hits.length ? r.evidence_hits.map((h: any) => <EvidenceCard key={h.chunk_id} e={h} />) : <EmptyState title="No institutional evidence retrieved" />}
                  </IndexColumn>
                </CardBody>
                <div className="border-t border-line">
                  <div className="px-4 pt-3 text-xs font-semibold">Exact structured comparison (deterministic rule engine)</div>
                  <Table head={["Operator", "Document", "Observed", "Expected", "Result", "Reason"]}>
                    {r.outcomes.map((o: any, i: number) => (
                      <tr key={i}>
                        <Td className="font-mono text-[11px]">{o.op}</Td><Td className="text-xs">{label(o.doc_type)}{o.page ? ` · p.${o.page}` : ""}</Td>
                        <Td className="break-all font-mono text-[11px]">{typeof o.observed === "object" ? JSON.stringify(o.observed) : String(o.observed ?? "—")}</Td>
                        <Td className="font-mono text-[11px]">{String(o.expected ?? "—")}</Td><Td><StatusBadge s={o.result} /></Td>
                        <Td className="max-w-sm text-xs text-fg-muted">{o.reason}</Td>
                      </tr>))}
                  </Table>
                </div>
              </Card>
            )}
            <Card>
              <CardHeader title="Unresolved questions routed for authorized review" sub="Indeterminate checks, unverifiable sources and evidence awaiting confirmation" />
              {!unresolved ? <LoadingBlock rows={2} /> : !unresolved.items.length ? <EmptyState title="None" /> : (
                <ul className="divide-y divide-line">{unresolved.items.map((f: any) => (
                  <li key={f.id} className="flex flex-wrap items-center gap-2 px-4 py-2 text-xs"><Badge>{f.agent}</Badge><span className="flex-1">{f.title}</span><StatusBadge s={f.review_status} /></li>))}</ul>)}
            </Card>
          </div>
        </div>
      )}

      <Card className="mt-4">
        <CardHeader title="Retrieval playground" sub="Query both indexes side by side. Optionally set a historical reference date to see which edition applied then." />
        <CardBody className="space-y-4">
          <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); search(); }}>
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. fire safety certificate validity" className="w-full sm:w-96" aria-label="Query" />
            <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} className="w-44" aria-label="As-of date" />
            <Button type="submit" variant="primary" loading={busy}><Search className="h-4 w-4" />Search</Button>
          </form>
          {play && (
            <div className="grid gap-4 lg:grid-cols-2">
              <IndexColumn title="Regulatory Knowledge Index" sub={asOf ? `In force on ${asOf}` : "Current (active) sources"} tone="accent">
                {play.reg.length ? play.reg.map((h) => <CitationCard key={h.chunk_id} c={h} compact />) : <EmptyState title="No hits" />}</IndexColumn>
              <IndexColumn title="Institutional Evidence Index" sub={appId ? "Selected application + prior records of its institution" : "Select an application to search evidence"} tone="ok">
                {play.ev.length ? play.ev.map((h) => <EvidenceCard key={h.chunk_id} e={h} />) : <EmptyState title="No hits" />}</IndexColumn>
            </div>)}
        </CardBody>
      </Card>
    </>
  );
}
