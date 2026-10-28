"use client";
import { FolderKanban, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { StatusBadge } from "@/components/domain";
import { PageHeader, useSession } from "@/components/shell";
import { Badge, Button, Card, Dialog, EmptyState, ErrorState, Field, Input, LoadingBlock, Select, Table, Td } from "@/components/ui/primitives";
import { post, qs } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { dt, label } from "@/lib/utils";

const STATUSES = ["DRAFT", "SUBMITTED", "PROCESSING", "VALIDATION_COMPLETE", "REVIEW_REQUIRED", "REVIEW_IN_PROGRESS",
  "RECTIFICATION_REQUESTED", "READY_FOR_FURTHER_SCRUTINY"];

function NewApplication({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { data: insts } = useApi<any[]>(open ? "/institutions" : null);
  const [inst, setInst] = useState("");
  const [err, setErr] = useState<string>();
  const [busy, setBusy] = useState(false);
  const router = useRouter();
  const create = async () => {
    setBusy(true); setErr(undefined);
    try {
      const a = await post("/applications", { institution_id: inst || insts?.[0]?.id, category: "EOA", academic_year: "2027-28" });
      router.push(`/applications/${a.id}?tab=documents`);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="New synthetic application" description="Creates a DRAFT Extension of Approval application. Upload documents next, then submit.">
      <div className="space-y-3">
        <Field label="Institution"><Select value={inst} onChange={(e) => setInst(e.target.value)}>
          {insts?.map((i) => <option key={i.id} value={i.id}>{i.code} — {i.name}</option>)}</Select></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Category"><Input value="Extension of Approval (EoA)" disabled /></Field>
          <Field label="Academic year"><Input value="2027-28" disabled /></Field>
        </div>
        {err && <p role="alert" className="text-sm text-danger">{err}</p>}
        <Button variant="primary" onClick={create} loading={busy} className="w-full">Create draft</Button>
      </div>
    </Dialog>
  );
}

export default function Applications() {
  const { can } = useSession();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const { data, error, reload } = useApi<any>(`/applications${qs({ q, status, page, page_size: 15 })}`);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  return (
    <>
      <PageHeader title="Application register" sub="Searchable register of synthetic approval applications with workflow and issuer-verification status tracked separately."
        actions={can("application:create") && <Button variant="primary" onClick={() => setCreating(true)}><Plus className="h-4 w-4" />New application</Button>} />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <Input placeholder="Search reference, institution or code" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} className="w-full sm:w-80" aria-label="Search applications" />
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="w-full sm:w-64" aria-label="Filter by status">
            <option value="">All workflow statuses</option>{STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
          </Select>
        </div>
        {error ? <ErrorState error={error} retry={reload} /> : !data ? <LoadingBlock /> : !data.items.length ? (
          <EmptyState icon={<FolderKanban className="h-6 w-6" />} title="No applications match" body="Clear the filters or run the guided demo to create a synthetic application." />
        ) : (
          <Table head={["Reference", "Institution", "Category", "Workflow status", "Issuer verification", "Open items", "Submitted", "Reviewer"]}>
            {data.items.map((a: any) => (
              <tr key={a.id} className="hover:bg-ink-850/70">
                <Td><Link href={`/applications/${a.id}`} className="font-mono text-xs text-accent hover:underline">{a.reference_no}</Link>
                  {a.is_synthetic && <Badge tone="warn" className="ml-2">synthetic</Badge>}</Td>
                <Td><div className="max-w-xs truncate">{a.institution.name}</div><div className="text-[11px] text-fg-dim">{a.institution.code}</div></Td>
                <Td>{a.category} · {a.academic_year}</Td>
                <Td><StatusBadge s={a.status} /></Td>
                <Td><StatusBadge s={a.verification_status} /></Td>
                <Td className="tabular-nums">{a.counts.open_findings} <span className="text-fg-dim">/ {a.counts.documents} docs</span></Td>
                <Td className="whitespace-nowrap text-xs text-fg-muted">{dt(a.submitted_at, false)}</Td>
                <Td className="text-xs text-fg-muted">{a.assigned_reviewer?.full_name ?? "Unassigned"}</Td>
              </tr>
            ))}
          </Table>
        )}
        {data && data.total > 0 && (
          <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-fg-muted">
            <span>{data.total} application(s)</span>
            <div className="flex items-center gap-2">
              <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
              <span>Page {page} of {pages}</span>
              <Button size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
            </div>
          </div>
        )}
      </Card>
      <NewApplication open={creating} onOpenChange={setCreating} />
    </>
  );
}
