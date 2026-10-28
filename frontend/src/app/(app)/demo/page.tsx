"use client";
import { motion } from "framer-motion";
import { ArrowRight, Play, Sparkles } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { ProcessingTimeline } from "@/components/domain";
import { PageHeader, useSession } from "@/components/shell";
import { Badge, Button, Card, CardBody, CardHeader, LoadingBlock, Table, Td } from "@/components/ui/primitives";
import { post } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { cn, label } from "@/lib/utils";

export default function GuidedDemo() {
  const { can } = useSession();
  const { data: scenario } = useApi<any>("/demo/scenario");
  const [run, setRun] = useState<{ application_id: string; reference_no: string; job: any }>();
  const [fixtures, setFixtures] = useState(true);
  const [err, setErr] = useState<string>();
  const [busy, setBusy] = useState(false);
  const { data: job } = useApi<any>(run ? `/jobs/${run.job.id}` : null,
    { poll: (j: any) => (j && ["QUEUED", "RUNNING"].includes(j.status) ? 800 : false) });
  const done = job && !["QUEUED", "RUNNING"].includes(job.status);

  const start = async () => {
    setBusy(true); setErr(undefined);
    try { setRun(await post("/demo/run", { use_fixtures: fixtures })); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <>
      <PageHeader title="Guided demonstration" sub="Seven steps through the full workflow using a fictional institution. Every document, issuer and regulatory clause in this scenario is synthetic." />
      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <CardHeader title="1 · Start the scenario" sub="Creates a fresh synthetic EoA application, uploads 10 files through the real upload pipeline, submits and processes it." />
          <CardBody className="space-y-3">
            <label className="flex items-start gap-2 text-xs text-fg-muted">
              <input type="checkbox" className="mt-0.5" checked={fixtures} onChange={(e) => setFixtures(e.target.checked)} />
              <span>Use labelled <b>synthetic fixture text</b> for the scanned affidavit (which has no text layer) if OCR is unavailable. Untick to run only the
                actual OCR pipeline — without Tesseract installed the affidavit is then reported as unreadable rather than guessed.</span>
            </label>
            {can("demo:run") ? <Button variant="primary" onClick={start} loading={busy} className="w-full"><Play className="h-4 w-4" />{run ? "Run again (new application)" : "Run guided demo"}</Button>
              : <p className="text-xs text-warn">Sign in as the scrutiny officer to run the demo.</p>}
            {err && <p role="alert" className="text-xs text-danger">{err}</p>}
            {run && <p className="text-xs">Application <Link className="font-mono text-accent hover:underline" href={`/applications/${run.application_id}`}>{run.reference_no}</Link></p>}
            {job ? <ProcessingTimeline job={job} /> : run && <LoadingBlock rows={4} />}
            {done && <Badge tone={job.status === "SUCCEEDED" ? "ok" : "warn"}>Processing {label(job.status)}</Badge>}
          </CardBody>
        </Card>
        <div className="space-y-3 lg:col-span-3">
          {scenario?.steps.map((s: any, i: number) => {
            const href = run ? s.href.replace("{id}", run.application_id) : null;
            const ready = !!run && (s.n === 1 || done);
            return (
              <motion.div key={s.n} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}>
                <Card className={cn("flex items-start gap-4 p-4 transition-colors", ready && "border-accent/40")}>
                  <div className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-full border text-sm font-semibold",
                    ready ? "border-accent bg-accent/10 text-accent" : "border-line text-fg-dim")}>{s.n}</div>
                  <div className="min-w-0 flex-1">
                    <h2 className="text-sm font-semibold">{s.title}</h2>
                    <p className="mt-0.5 text-xs text-fg-muted">{s.what}</p>
                  </div>
                  {href && ready && <Button asChild size="sm"><Link href={href}>Open<ArrowRight className="h-3.5 w-3.5" /></Link></Button>}
                </Card>
              </motion.div>
            );
          })}
        </div>
      </div>
      <Card className="mt-4">
        <CardHeader title="Scenario files" sub="What each synthetic file is designed to exercise" action={<a className="text-xs text-accent hover:underline" href="/api/v1/demo/sample-pack">Download sample pack</a>} />
        {scenario ? (
          <Table head={["File", "Declared type", "Scenario element", "Notes"]}>
            {scenario.documents.map((d: any) => (
              <tr key={d.filename}>
                <Td className="font-mono text-xs">{d.filename}</Td><Td className="text-xs">{label(d.declared_type)}</Td>
                <Td className="text-xs">{d.scenario}</Td>
                <Td className="text-xs text-fg-muted">{d.description}{d.has_fixture_text && <Badge tone="warn" className="ml-2">fixture text available</Badge>}</Td>
              </tr>))}
          </Table>) : <LoadingBlock />}
        <CardBody className="border-t border-line">
          <ul className="space-y-1 text-[11px] text-fg-dim">{scenario?.notes.map((n: string) => <li key={n} className="flex gap-2"><Sparkles className="mt-0.5 h-3 w-3 shrink-0" />{n}</li>)}</ul>
        </CardBody>
      </Card>
    </>
  );
}
