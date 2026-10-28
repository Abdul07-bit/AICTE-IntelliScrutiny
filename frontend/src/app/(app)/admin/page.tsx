"use client";
import { useEffect, useState } from "react";
import { PageHeader, useSession } from "@/components/shell";
import {
  Badge, Button, Card, CardBody, CardHeader, Dialog, ErrorState, Field, Input, LoadingBlock, Select, Table, Tabs, TabsContent, TabsList, Td,
} from "@/components/ui/primitives";
import { api, patch, post } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { dt, label } from "@/lib/utils";

const ROLES = ["INSTITUTION_UPLOADER", "SCRUTINY_OFFICER", "VERIFICATION_OFFICER", "REGULATORY_ADMIN", "SYSTEM_ADMIN", "AUDITOR"];

function Users() {
  const { me } = useSession();
  const { data, error, reload } = useApi<any[]>("/admin/users");
  const { data: insts } = useApi<any[]>("/institutions");
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ email: "", full_name: "", role: "SCRUTINY_OFFICER", institution_id: "", password: "" });
  const [err, setErr] = useState<string>();
  const run = async (fn: () => Promise<unknown>) => { setErr(undefined); try { await fn(); await reload(); } catch (e) { setErr((e as Error).message); } };
  if (error) return <ErrorState error={error} retry={reload} />;
  return (
    <div className="space-y-3">
      <Button size="sm" variant="primary" onClick={() => setCreating(true)}>Add user</Button>
      {err && <p role="alert" className="text-sm text-danger">{err}</p>}
      <Card>{!data ? <LoadingBlock /> : (
        <Table head={["User", "Role", "Institution", "Last login", "Status", ""]}>
          {data.map((u) => (
            <tr key={u.id}>
              <Td><div>{u.full_name}</div><div className="text-[11px] text-fg-dim">{u.email}</div></Td>
              <Td><Select value={u.role} disabled={u.id === me.id} onChange={(e) => run(() => patch(`/admin/users/${u.id}`, { role: e.target.value }))} className="w-52" aria-label={`Role for ${u.email}`}>
                {ROLES.map((r) => <option key={r} value={r}>{label(r)}</option>)}</Select></Td>
              <Td className="text-xs">{u.institution_name ?? "—"}</Td><Td className="text-xs">{dt(u.last_login_at)}</Td>
              <Td><Badge tone={u.is_active ? "ok" : "neutral"}>{u.is_active ? "active" : "inactive"}</Badge></Td>
              <Td>{u.id !== me.id && <Button size="sm" onClick={() => run(() => patch(`/admin/users/${u.id}`, { is_active: !u.is_active }))}>{u.is_active ? "Deactivate" : "Activate"}</Button>}</Td>
            </tr>))}
        </Table>)}</Card>
      <Dialog open={creating} onOpenChange={setCreating} title="Add user" description="Passwords must be at least 10 characters. In the demo environment new users are marked synthetic.">
        <div className="space-y-3">
          <Field label="Full name"><Input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
          <Field label="Email"><Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <Field label="Role"><Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{ROLES.map((r) => <option key={r} value={r}>{label(r)}</option>)}</Select></Field>
          {form.role === "INSTITUTION_UPLOADER" && <Field label="Institution"><Select value={form.institution_id} onChange={(e) => setForm({ ...form, institution_id: e.target.value })}>
            <option value="">Select…</option>{insts?.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}</Select></Field>}
          <Field label="Initial password"><Input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
          <Button variant="primary" onClick={() => run(async () => { await post("/admin/users", { ...form, institution_id: form.institution_id || null }); setCreating(false); })}>Create user</Button>
        </div>
      </Dialog>
    </div>
  );
}

function RolesMatrix() {
  const { data } = useApi<any>("/admin/roles");
  if (!data) return <LoadingBlock />;
  const roles = Object.keys(data.roles);
  return (
    <Card><Table head={["Permission", ...roles.map((r) => label(r))]}>
      {data.permissions.map((p: string) => (
        <tr key={p}><Td className="font-mono text-[11px]">{p}</Td>
          {roles.map((r) => <Td key={r} className="text-center">{data.roles[r].includes(p) ? <span className="text-ok" aria-label="granted">●</span> : <span className="text-fg-dim" aria-label="not granted">·</span>}</Td>)}</tr>))}
    </Table></Card>
  );
}

function SettingsPanel() {
  const { data, reload } = useApi<any>("/admin/settings");
  const { data: sys } = useApi<any>("/admin/system");
  const [ret, setRet] = useState<any>();
  const [preview, setPreview] = useState<any>();
  const [msg, setMsg] = useState<string>();
  useEffect(() => { if (data) setRet(data.retention); }, [data]);
  if (!data || !ret) return <LoadingBlock />;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader title="Retention policy" sub="Days before synthetic documents are purged. Hashes, metadata and audit events are retained; legal holds are respected." />
        <CardBody className="space-y-3">
          {Object.entries(ret).map(([k, v]) => (
            <Field key={k} label={label(k)}><Input type="number" min={1} value={v as number} onChange={(e) => setRet({ ...ret, [k]: Number(e.target.value) })} /></Field>))}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="primary" onClick={async () => { await api("/admin/settings/retention", { method: "PUT", json: { value: ret } }); setMsg("Saved"); reload(); }}>Save</Button>
            <Button size="sm" onClick={async () => setPreview(await api("/admin/retention/preview"))}>Preview deletion</Button>
            {preview?.candidates > 0 && <Button size="sm" variant="danger" onClick={async () => { const r = await post("/admin/retention/execute"); setMsg(`Purged ${r.purged} document(s)`); setPreview(undefined); }}>Execute purge</Button>}
          </div>
          {preview && <p className="text-xs text-fg-muted">{preview.candidates} candidate(s), {(preview.bytes / 1024).toFixed(0)} KB. {preview.policy}</p>}
          {msg && <p role="status" className="text-xs text-ok">{msg}</p>}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="System configuration" sub="Read-only runtime view; secrets are never displayed" />
        <CardBody>{!sys ? <LoadingBlock /> : (
          <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1.5 text-xs">
            <dt className="text-fg-dim">Environment</dt><dd>{sys.app_env}</dd>
            <dt className="text-fg-dim">Database</dt><dd>{sys.database}</dd>
            <dt className="text-fg-dim">Vector backend</dt><dd>{sys.vector_backend}</dd>
            <dt className="text-fg-dim">Embedding</dt><dd>{sys.embedding.embedding_model} ({sys.embedding.embedding_kind})</dd>
            <dt className="text-fg-dim">OCR</dt><dd>{sys.ocr.tesseract_available ? "Tesseract available" : "Tesseract not installed"} · {sys.ocr.pipeline_version}</dd>
            <dt className="text-fg-dim">Explainer</dt><dd>{sys.explainer}</dd>
            <dt className="text-fg-dim">Storage encryption</dt><dd>{sys.storage_encryption ? "enabled" : "disabled"}</dd>
            <dt className="text-fg-dim">Malware scanner</dt><dd>{sys.malware_scanner}</dd>
            <dt className="text-fg-dim">Upload limit</dt><dd>{sys.max_upload_mb} MB · signed URLs {sys.signed_url_ttl_seconds}s</dd>
            <dt className="text-fg-dim">Audit chain</dt><dd>{sys.audit_chain.valid ? `intact (${sys.audit_chain.checked})` : "BROKEN"}</dd>
          </dl>)}</CardBody>
      </Card>
    </div>
  );
}

export default function Admin() {
  return (
    <>
      <PageHeader title="Administration" sub="Users and roles, permissions, retention and system configuration. Role-based access is enforced by the backend on every request." />
      <Tabs defaultValue="users">
        <TabsList items={[{ value: "users", label: "Users" }, { value: "roles", label: "Roles & permissions" }, { value: "settings", label: "Retention & system" }]} />
        <TabsContent value="users"><Users /></TabsContent>
        <TabsContent value="roles"><RolesMatrix /></TabsContent>
        <TabsContent value="settings"><SettingsPanel /></TabsContent>
      </Tabs>
    </>
  );
}
