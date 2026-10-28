"use client";
import { ExternalLink, FileSearch } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApplicationPicker, PageFacsimile, SeverityBadge, StatusBadge } from "@/components/domain";
import { PageHeader } from "@/components/shell";
import { Badge, Card, CardBody, CardHeader, EmptyState, LoadingBlock, Meter, Select } from "@/components/ui/primitives";
import { openDocument, qs } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { cn, label } from "@/lib/utils";

export default function DocumentIntelligence() {
  const sp = useSearchParams();
  const router = useRouter();
  const [appId, setAppId] = useState(sp.get("application") ?? "");
  const [docId, setDocId] = useState(sp.get("doc") ?? "");
  const [method, setMethod] = useState("");
  const { data: queue } = useApi<any>(`/documents${qs({ application_id: appId, method, page_size: 100 })}`);
  const { data: doc } = useApi<any>(docId ? `/documents/${docId}` : null);
  const [page, setPage] = useState(1);
  const [field, setField] = useState<any>(null);
  useEffect(() => { setPage(1); setField(null); }, [docId]);
  useEffect(() => { if (doc && !appId) setAppId(doc.application_id); }, [doc, appId]);
  const pg = doc?.pages?.find((p: any) => p.page_no === page);
  const isImage = doc?.content_type?.startsWith("image/");

  return (
    <>
      <PageHeader title="Document intelligence" sub="Uploaded-document queue with extraction method, schema-constrained fields, confidence and page-level evidence." />
      <div className="mb-3 flex flex-wrap gap-2">
        <ApplicationPicker value={appId} onChange={(v) => { setAppId(v); setDocId(""); router.replace(`/document-intelligence${qs({ application: v })}`); }} />
        <Select value={method} onChange={(e) => setMethod(e.target.value)} className="w-56" aria-label="Extraction method">
          <option value="">All extraction methods</option>
          {["pdf-text-layer", "tesseract-ocr", "synthetic-fixture", "none", "mixed"].map((m) => <option key={m}>{m}</option>)}
        </Select>
      </div>
      <div className="grid gap-4 xl:grid-cols-12">
        <Card className="xl:col-span-3">
          <CardHeader title="Queue" sub={`${queue?.total ?? "…"} document(s)`} />
          {!queue ? <LoadingBlock /> : !queue.items.length ? <EmptyState title="No documents" /> : (
            <ul className="max-h-[70vh] divide-y divide-line overflow-y-auto scroll-thin">
              {queue.items.map((d: any) => (
                <li key={d.id}>
                  <button onClick={() => setDocId(d.id)} aria-current={d.id === docId}
                    className={cn("w-full px-3 py-2.5 text-left hover:bg-ink-850", d.id === docId && "bg-accent/10")}>
                    <div className="flex items-center justify-between gap-2"><span className="truncate text-sm">{d.filename}</span><StatusBadge s={d.status} /></div>
                    <div className="mt-0.5 flex flex-wrap gap-1 text-[10px] text-fg-dim">
                      <span>{d.application_reference}</span>·<span>{label(d.doc_type ?? d.declared_type)}</span>
                      {d.extraction_method && <Badge tone={d.extraction_method === "synthetic-fixture" ? "warn" : "neutral"}>{d.extraction_method}</Badge>}
                      {d.anomaly_count > 0 && <Badge tone="violet">{d.anomaly_count} anomaly flag(s)</Badge>}
                    </div>
                  </button>
                </li>))}
            </ul>)}
        </Card>

        {!docId ? (
          <Card className="xl:col-span-9"><EmptyState icon={<FileSearch className="h-6 w-6" />} title="Select a document" body="Inspect extracted text, field locations, confidence, metadata and anomaly flags." /></Card>
        ) : !doc ? <Card className="xl:col-span-9"><LoadingBlock rows={8} /></Card> : (<>
          <Card className="xl:col-span-5">
            <CardHeader title={doc.filename} sub={`${label(doc.doc_type)} · classification ${((doc.classification_confidence ?? 0) * 100).toFixed(0)}% · ${doc.extraction_method ?? "not processed"}`}
              action={<button className="inline-flex items-center gap-1 text-xs text-accent hover:underline" onClick={() => openDocument(doc.id)}>Original<ExternalLink className="h-3 w-3" /></button>} />
            <CardBody className="space-y-3">
              {doc.pages.length > 1 && <div className="flex gap-1">{doc.pages.map((p: any) => (
                <button key={p.page_no} onClick={() => setPage(p.page_no)} className={cn("rounded px-2 py-0.5 text-xs", page === p.page_no ? "bg-accent text-ink-950" : "bg-ink-800")}>p.{p.page_no}</button>))}</div>}
              {isImage ? <p className="text-xs text-fg-muted">Image evidence — open the original to view it. Visual checks are on the Infrastructure page.</p>
                : pg ? <PageFacsimile page={pg} highlight={field?.page_no === page ? field.bbox : null} /> : <EmptyState title="No extracted pages" />}
              {pg?.method === "synthetic-fixture" && <Badge tone="warn">Synthetic fixture text — not OCR output</Badge>}
              <p className="text-[11px] text-fg-dim">Reconstructed from the extracted text layer with line positions. Select a field to highlight its source region.</p>
            </CardBody>
          </Card>
          <div className="space-y-4 xl:col-span-4">
            <Card>
              <CardHeader title="Extracted fields" sub="Schema-constrained; normalised values used for exact checks" />
              {!doc.fields.length ? <EmptyState title="No fields extracted" body={doc.metadata?.warnings?.join("; ")} /> : (
                <ul className="divide-y divide-line">{doc.fields.map((f: any) => (
                  <li key={f.id}>
                    <button onClick={() => { setField(f); if (f.page_no) setPage(f.page_no); }} className={cn("w-full px-4 py-2 text-left hover:bg-ink-850", field?.id === f.id && "bg-accent/10")}>
                      <div className="flex justify-between gap-2 text-xs"><span className="text-fg-muted">{label(f.name)}</span><span className="text-fg-dim">p.{f.page_no ?? "—"} · {f.method}</span></div>
                      <div className="mt-0.5 break-words text-sm">{f.value}</div>
                      {f.normalized_value !== f.value && <div className="break-words font-mono text-[11px] text-accent">{f.normalized_value}</div>}
                      <div className="mt-1 flex items-center gap-2"><Meter value={f.confidence} tone={f.confidence < 0.6 ? "warn" : "ok"} /><span className="text-[10px] tabular-nums text-fg-dim">{f.confidence.toFixed(2)}</span></div>
                    </button>
                  </li>))}</ul>)}
            </Card>
            <Card>
              <CardHeader title="Flags on this document" />
              <CardBody className="space-y-2">
                {!doc.findings.length && <p className="text-xs text-fg-dim">No flags.</p>}
                {doc.findings.map((f: any) => (
                  <div key={f.id} className="rounded-md border border-line p-2 text-xs"><div className="flex gap-1.5"><SeverityBadge s={f.severity} /><span className="font-medium">{f.title}</span></div>
                    <p className="mt-1 text-fg-muted">{f.reason}</p></div>))}
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="File provenance & metadata" />
              <CardBody className="space-y-1 text-[11px]">
                <div><span className="text-fg-dim">SHA-256 </span><span className="break-all font-mono">{doc.sha256}</span></div>
                <div><span className="text-fg-dim">Malware scan </span>{label(doc.malware_scan_status)} ({doc.malware_scan_engine})</div>
                <div><span className="text-fg-dim">Encrypted at rest </span>{doc.encrypted ? "yes" : "no (dev setting)"}</div>
                {doc.metadata?.extraction && Object.entries(doc.metadata.extraction).filter(([k]) => k !== "exif").map(([k, v]) => (
                  <div key={k}><span className="text-fg-dim">{label(k)} </span><span className="font-mono">{String(v ?? "—")}</span></div>))}
                {doc.metadata?.qr?.length > 0 && <div><span className="text-fg-dim">QR payload </span><span className="break-all font-mono">{doc.metadata.qr[0].raw.slice(0, 160)}…</span></div>}
              </CardBody>
            </Card>
          </div>
        </>)}
      </div>
    </>
  );
}
