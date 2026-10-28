"use client";
import { Building2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApplicationPicker, FindingsPanel } from "@/components/domain";
import { PageHeader } from "@/components/shell";
import { Badge, Card, CardBody, CardHeader, EmptyState, LoadingBlock } from "@/components/ui/primitives";
import { api, qs } from "@/lib/api";
import { useApi } from "@/lib/hooks";

function Thumb({ doc }: { doc: any }) {
  const [url, setUrl] = useState<string>();
  useEffect(() => { api(`/documents/${doc.id}/url`).then((r) => setUrl(r.url)).catch(() => null); }, [doc.id]);
  const { data: detail } = useApi<any>(`/documents/${doc.id}`);
  const q = detail?.metadata?.image_quality;
  return (
    <Card className="overflow-hidden">
      <div className="aspect-[4/3] bg-ink-800">{url && <img src={url} alt={doc.description ?? doc.filename} className="h-full w-full object-cover" />}</div>
      <div className="space-y-1 p-3 text-xs">
        <div className="font-medium">{doc.description ?? doc.filename}</div>
        {q && <div className="flex flex-wrap gap-1"><Badge>{q.width}×{q.height}</Badge><Badge>brightness {q.brightness}</Badge><Badge>sharpness {q.sharpness}</Badge></div>}
        <div className="text-[10px] text-fg-dim">feature detector: {detail?.metadata?.feature_detector ?? "—"}</div>
      </div>
    </Card>
  );
}

export default function Infrastructure() {
  const sp = useSearchParams();
  const router = useRouter();
  const [appId, setAppId] = useState(sp.get("application") ?? "");
  const { data: docs } = useApi<any[]>(appId ? `/applications/${appId}/documents` : null);
  const { data: comp } = useApi<any>(appId ? `/applications/${appId}/compliance` : null);
  const photos = docs?.filter((d) => d.content_type.startsWith("image/")) ?? [];
  const plan = docs?.some((d) => d.doc_type === "BUILDING_PLAN");
  const infraRule = comp?.retrievals?.find((r: any) => r.rule_code === "SYN-EOA-INFRA-006");
  return (
    <>
      <PageHeader title="Infrastructure evidence review"
        sub="Machine observations on submitted photographs (duplication, quality, capture metadata). These are not verified deficiencies — a photograph cannot establish dimensions, capacity or legal sufficiency." />
      <div className="mb-4"><ApplicationPicker value={appId} onChange={(v) => { setAppId(v); router.replace(`/infrastructure${qs({ application: v })}`); }} /></div>
      {!appId ? <Card><EmptyState icon={<Building2 className="h-6 w-6" />} title="Select an application" /></Card> : !docs ? <LoadingBlock /> : (
        <div className="grid gap-4 xl:grid-cols-3">
          <div className="space-y-4 xl:col-span-2">
            <Card>
              <CardHeader title={`Submitted images (${photos.length})`} />
              <CardBody>{photos.length ? <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{photos.map((d) => <Thumb key={d.id} doc={d} />)}</div>
                : <EmptyState title="No photographs submitted" />}</CardBody>
            </Card>
            <Card>
              <CardHeader title="Observations & reviewer actions" sub="Open an observation to request additional evidence or recommend a physical inspection" />
              <CardBody><FindingsPanel applicationId={appId} filter={{ agent: "INFRASTRUCTURE" }} empty="No infrastructure observations" /></CardBody>
            </Card>
          </div>
          <Card className="self-start">
            <CardHeader title="Evidence completeness" sub="From the applicable synthetic rule" />
            <CardBody className="space-y-2 text-sm">
              <div className="flex justify-between"><span>Approved building plan</span>{plan ? <Badge tone="ok">present</Badge> : <Badge tone="warn">missing</Badge>}</div>
              <div className="flex justify-between"><span>Instructional-space photographs</span><Badge tone={photos.length >= 4 ? "ok" : "warn"}>{photos.length} / 4</Badge></div>
              {infraRule?.outcomes?.filter((o: any) => o.result !== "PASS").map((o: any, i: number) => <p key={i} className="rounded-md bg-warn/10 p-2 text-xs text-warn">{o.reason}</p>)}
              <p className="text-[11px] text-fg-dim">Insufficient images mean compliance cannot be established from the submission alone; this is routed to the reviewer, not concluded.</p>
            </CardBody>
          </Card>
        </div>
      )}
    </>
  );
}
