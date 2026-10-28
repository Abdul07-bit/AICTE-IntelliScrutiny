"use client";
import { useState } from "react";
import { StatusBadge } from "@/components/domain";
import { PageHeader, useSession } from "@/components/shell";
import { Badge, Button, Card, CardBody, CardHeader, ErrorState, LoadingBlock } from "@/components/ui/primitives";
import { patch, post } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { dt, label } from "@/lib/utils";

export default function Connectors() {
  const { can } = useSession();
  const { data, error, reload } = useApi<any[]>("/connectors");
  const [err, setErr] = useState<string>();
  const manage = can("connector:manage");
  const run = async (fn: () => Promise<unknown>) => { setErr(undefined); try { await fn(); await reload(); } catch (e) { setErr((e as Error).message); } };
  if (error) return <ErrorState error={error} retry={reload} />;
  return (
    <>
      <PageHeader title="Verification connectors" sub="Extensible connector interface. MOCK connectors are deterministic and answer only from labelled synthetic registries; live integrations require official authorization and credentials supplied via environment variables." />
      {err && <p role="alert" className="mb-3 text-sm text-danger">{err}</p>}
      {!data ? <LoadingBlock rows={6} /> : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.map((c) => (
            <Card key={c.id}>
              <CardHeader title={c.name} sub={c.issuer_name ?? label(c.kind)} action={<StatusBadge s={c.health_status} />} />
              <CardBody className="space-y-2 text-xs">
                <div className="flex flex-wrap gap-1"><Badge tone={c.mode === "MOCK" ? "warn" : "accent"}>{c.mode}</Badge><Badge>{label(c.kind)}</Badge>
                  <Badge tone={c.enabled ? "ok" : "neutral"}>{c.enabled ? "enabled" : "disabled"}</Badge>
                  {c.config?.simulate_outage && <Badge tone="danger">simulated outage</Badge>}</div>
                {c.doc_types?.length > 0 && <div className="text-fg-muted">Document types: {c.doc_types.map(label).join(", ")}</div>}
                <div className="grid grid-cols-2 gap-1 text-fg-muted">
                  <span>Timeout</span><span className="tabular-nums">{c.timeout_ms} ms</span>
                  <span>Retries</span><span className="tabular-nums">{c.max_retries}</span>
                  <span>Requests</span><span className="tabular-nums">{c.request_count}</span>
                  <span>Last success</span><span>{c.last_success_at ? dt(c.last_success_at) : "—"}</span>
                  {c.synthetic_registry_size != null && <><span>Synthetic records</span><span>{c.synthetic_registry_size}</span></>}
                  {c.registered_issuer_keys && <><span>Issuer keys</span><span>{c.registered_issuer_keys.join(", ")}</span></>}
                </div>
                {c.last_error && <p className="rounded-md bg-danger/10 p-2 text-danger">{c.last_error} <span className="text-fg-dim">({dt(c.last_error_at)})</span></p>}
                {c.authorization_note && <p className="rounded-md bg-ink-850 p-2 text-fg-muted">{c.authorization_note}</p>}
                <div className="flex flex-wrap gap-2 pt-1">
                  <Button size="sm" onClick={() => run(() => post(`/connectors/${c.id}/health-check`))}>Health check</Button>
                  {manage && c.kind !== "DIGILOCKER" && c.kind !== "MANUAL" && <Button size="sm" onClick={() => run(() => patch(`/connectors/${c.id}`, { enabled: !c.enabled }))}>{c.enabled ? "Disable" : "Enable"}</Button>}
                  {manage && c.mode === "MOCK" && c.kind === "ISSUER_API" && <Button size="sm" variant={c.config?.simulate_outage ? "ok" : "danger"}
                    onClick={() => run(() => patch(`/connectors/${c.id}`, { simulate_outage: !c.config?.simulate_outage }))}>{c.config?.simulate_outage ? "End simulated outage" : "Simulate outage"}</Button>}
                </div>
              </CardBody>
            </Card>))}
        </div>)}
    </>
  );
}
