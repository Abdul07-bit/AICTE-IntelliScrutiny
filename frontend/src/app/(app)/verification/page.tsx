"use client";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { StatusBadge } from "@/components/domain";
import { PageHeader, useSession } from "@/components/shell";
import { Badge, Button, Card, CardHeader, Dialog, EmptyState, Field, Input, LoadingBlock, Select, Table, Td, Textarea } from "@/components/ui/primitives";
import { post, qs } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { dt, label } from "@/lib/utils";

const STATUSES = ["VERIFIED", "MISMATCH_DETECTED", "EXPIRED", "PENDING", "SOURCE_UNAVAILABLE", "MANUAL_REVIEW_REQUIRED", "NOT_APPLICABLE"];

function ManualOutcome({ req, onClose, onDone }: { req: any; onClose: () => void; onDone: () => void }) {
  const [outcome, setOutcome] = useState("VERIFIED");
  const [note, setNote] = useState("");
  const [ref, setRef] = useState("");
  const [err, setErr] = useState<string>();
  const save = async () => {
    try { await post(`/verification/requests/${req.id}/manual-outcome`, { outcome, note, issuer_contact_reference: ref || null }); onDone(); onClose(); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Record manual issuer confirmation"
      description={`${req.application_reference} · ${req.document_filename ?? "application"} — record what the issuer confirmed and how.`}>
      <div className="space-y-3">
        <p className="rounded-md bg-ink-850 p-2 text-xs text-fg-muted">{req.response_meta?.reason ?? req.response_meta?.summary}</p>
        <Field label="Outcome"><Select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
          {["VERIFIED", "MISMATCH_DETECTED", "EXPIRED", "MANUAL_REVIEW_REQUIRED", "NOT_APPLICABLE"].map((s) => <option key={s} value={s}>{label(s)}</option>)}</Select></Field>
        <Field label="Issuer contact / evidence reference" hint="e.g. letter number or call log reference"><Input value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
        <Field label="Note (required)"><Textarea value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        {err && <p role="alert" className="text-sm text-danger">{err}</p>}
        <Button variant="primary" disabled={note.trim().length < 5} onClick={save}>Save outcome</Button>
      </div>
    </Dialog>
  );
}

export default function Verification() {
  const sp = useSearchParams();
  const { can } = useSession();
  const [status, setStatus] = useState("");
  const [connector, setConnector] = useState("");
  const appId = sp.get("application") ?? "";
  const [editing, setEditing] = useState<any>(null);
  const { data: conns } = useApi<any[]>(can("connector:read") ? "/connectors" : null);
  const { data: manual, reload: reloadManual } = useApi<any>(`/verification/requests${qs({ manual_queue: true, application_id: appId, page_size: 50 })}`);
  const { data, reload } = useApi<any>(`/verification/requests${qs({ status, connector_key: connector, application_id: appId, page_size: 100 })}`);
  return (
    <>
      <PageHeader title="Issuer verification" sub="Connector requests, outcomes and the manual-confirmation queue. Mock connectors answer only from labelled synthetic registries; source unavailability is never treated as forgery." />
      {conns && (
        <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
          {conns.map((c) => (
            <Card key={c.id} className="p-3">
              <div className="truncate text-xs font-medium" title={c.name}>{c.name}</div>
              <div className="mt-1.5 flex flex-wrap gap-1"><StatusBadge s={c.health_status} /><Badge>{c.mode}</Badge>{!c.enabled && <Badge>disabled</Badge>}</div>
              <div className="mt-1 text-[10px] text-fg-dim">{c.request_count} request(s) · last ok {c.last_success_at ? dt(c.last_success_at) : "—"}</div>
            </Card>))}
        </div>)}
      <Card className="mb-4">
        <CardHeader title="Manual confirmation queue" sub="Items routed because a source was unavailable, a signature did not validate, or a reviewer requested confirmation" />
        {!manual ? <LoadingBlock rows={2} /> : !manual.items.length ? <EmptyState title="Queue is empty" /> : (
          <Table head={["Application", "Document", "Reason", "Requested", ""]}>
            {manual.items.map((v: any) => (
              <tr key={v.id}>
                <Td className="font-mono text-xs">{v.application_reference}</Td><Td className="text-xs">{v.document_filename ?? "—"}</Td>
                <Td className="max-w-md text-xs text-fg-muted">{v.response_meta?.reason}</Td><Td className="whitespace-nowrap text-xs">{dt(v.requested_at)}</Td>
                <Td>{can("verification:confirm") ? <Button size="sm" variant="primary" onClick={() => setEditing(v)}>Record outcome</Button> : <Badge>verification officer only</Badge>}</Td>
              </tr>))}
          </Table>)}
      </Card>
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-56" aria-label="Status"><option value="">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}</Select>
          <Select value={connector} onChange={(e) => setConnector(e.target.value)} className="w-64" aria-label="Connector"><option value="">All connectors</option>
            {conns?.map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}</Select>
        </div>
        {!data ? <LoadingBlock /> : !data.items.length ? <EmptyState title="No requests match" /> : (
          <Table head={["Application", "Document", "Connector", "Result", "Request ref", "Source response", "Timestamp"]}>
            {data.items.map((v: any) => (
              <tr key={v.id}>
                <Td className="font-mono text-xs">{v.application_reference}</Td><Td className="text-xs">{v.document_filename ?? "—"}</Td>
                <Td className="text-xs">{v.connector_key}{v.is_simulated && <Badge className="ml-1">simulated</Badge>}</Td>
                <Td><StatusBadge s={v.effective_status} /></Td><Td className="font-mono text-[11px]">{v.request_ref}</Td>
                <Td className="max-w-md text-xs text-fg-muted">{v.manual_note ?? v.response_meta?.summary}
                  {v.mismatches?.map((m: any, i: number) => <div key={i} className="text-danger">{m.field}: {m.document_value} ≠ {m.issuer_value}</div>)}
                  {v.attempts > 1 && <div className="text-fg-dim">{v.attempts} attempts</div>}</Td>
                <Td className="whitespace-nowrap text-xs text-fg-dim">{dt(v.responded_at ?? v.requested_at)}</Td>
              </tr>))}
          </Table>)}
      </Card>
      {editing && <ManualOutcome req={editing} onClose={() => setEditing(null)} onDone={() => { reload(); reloadManual(); }} />}
    </>
  );
}
