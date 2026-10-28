"use client";
import { ShieldCheck, ShieldAlert } from "lucide-react";
import { Fragment, useState } from "react";
import { PageHeader } from "@/components/shell";
import { Badge, Button, Card, EmptyState, ErrorState, Input, LoadingBlock, Select, Table, Td } from "@/components/ui/primitives";
import { qs } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { dt, label } from "@/lib/utils";

export default function Audit() {
  const [q, setQ] = useState("");
  const [action, setAction] = useState("");
  const [entity, setEntity] = useState("");
  const [since, setSince] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string>();
  const { data: facets } = useApi<any>("/audit/facets");
  const { data: chain } = useApi<any>("/audit/verify-chain");
  const { data, error, reload } = useApi<any>(`/audit${qs({ q, action, entity_type: entity, since, page, page_size: 30 })}`);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  return (
    <>
      <PageHeader title="Audit trail" sub="Append-only, hash-chained record of document access, validation, rule changes, verification and reviewer actions."
        actions={chain && (chain.valid ? <Badge tone="ok"><ShieldCheck className="h-3 w-3" />Hash chain intact · {chain.checked} events</Badge>
          : <Badge tone="danger"><ShieldAlert className="h-3 w-3" />Chain broken at #{chain.broken_at_seq}</Badge>)} />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <Input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="Search action, actor, entity ID or request ID" className="w-full sm:w-80" aria-label="Search audit" />
          <Select value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} className="w-60" aria-label="Action"><option value="">All actions</option>
            {facets?.actions.map((a: string) => <option key={a}>{a}</option>)}</Select>
          <Select value={entity} onChange={(e) => { setEntity(e.target.value); setPage(1); }} className="w-48" aria-label="Entity type"><option value="">All entities</option>
            {facets?.entity_types.map((a: string) => <option key={a}>{a}</option>)}</Select>
          <Input type="date" value={since} onChange={(e) => { setSince(e.target.value); setPage(1); }} className="w-44" aria-label="Since" />
        </div>
        {error ? <ErrorState error={error} retry={reload} /> : !data ? <LoadingBlock rows={8} /> : !data.items.length ? <EmptyState title="No events match" /> : (
          <Table head={["#", "Time", "Actor", "Action", "Entity", "Request", ""]}>
            {data.items.map((e: any) => (
              <Fragment key={e.id}>
                <tr className="hover:bg-ink-850/60">
                  <Td className="tabular-nums text-xs text-fg-dim">{e.seq}</Td><Td className="whitespace-nowrap text-xs">{dt(e.ts)}</Td>
                  <Td className="text-xs">{e.actor_email}<div className="text-[10px] text-fg-dim">{label(e.actor_role)}</div></Td>
                  <Td className="font-mono text-xs text-accent">{e.action}</Td>
                  <Td className="text-xs">{e.entity_type}<div className="font-mono text-[10px] text-fg-dim">{e.entity_id?.slice(0, 8)}</div></Td>
                  <Td className="font-mono text-[10px] text-fg-dim">{e.request_id ?? "—"}</Td>
                  <Td><Button size="sm" variant="ghost" aria-expanded={open === e.id} onClick={() => setOpen(open === e.id ? undefined : e.id)}>{open === e.id ? "Hide" : "Details"}</Button></Td>
                </tr>
                {open === e.id && <tr><td colSpan={7} className="bg-ink-950 px-4 py-3">
                  <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-fg-muted">{JSON.stringify({ details: e.details, source_refs: e.source_refs, hash: e.hash, prev_hash: e.prev_hash }, null, 2)}</pre></td></tr>}
              </Fragment>))}
          </Table>)}
        {data && <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-fg-muted"><span>{data.total} event(s)</span>
          <div className="flex items-center gap-2"><Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button><span>{page} / {pages}</span>
            <Button size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button></div></div>}
      </Card>
    </>
  );
}
