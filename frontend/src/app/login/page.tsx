"use client";
import { ShieldCheck } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { DisclaimerBar } from "@/components/shell";
import { Button, Card, Field, Input } from "@/components/ui/primitives";
import { post } from "@/lib/api";

const ACCOUNTS = [
  ["officer@scrutiny.example", "Scrutiny officer — reviews findings"],
  ["verifier@scrutiny.example", "Issuer-verification officer"],
  ["regadmin@scrutiny.example", "Regulatory administrator — rules & sources"],
  ["sysadmin@scrutiny.example", "System administrator — users, connectors"],
  ["auditor@scrutiny.example", "Read-only auditor"],
  ["uploader@sits.example", "Institution uploader (synthetic institute)"],
];

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next") || "/dashboard";
  const [email, setEmail] = useState("officer@scrutiny.example");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await post("/auth/login", { email, password });
      router.push(next.startsWith("/") ? next : "/dashboard");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto grid max-w-5xl gap-8 px-4 py-12 md:grid-cols-2 md:py-20">
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-accent to-ok text-ink-950"><ShieldCheck className="h-5 w-5" /></div>
          <div>
            <h1 className="text-xl font-semibold">AICTE IntelliScrutiny</h1>
            <p className="text-xs text-fg-dim">Working prototype · synthetic demonstration data</p>
          </div>
        </div>
        <p className="text-sm leading-relaxed text-fg-muted">
          Evidence-linked document validation, issuer verification and regulatory-compliance assistance for approval
          applications. Two separate retrieval indexes (regulatory knowledge and institutional evidence), a versioned
          rule engine and mock issuer connectors produce advisory findings for human reviewers.
        </p>
        <Card className="p-3">
          <p className="mb-2 text-xs font-medium text-fg-muted">Synthetic demo accounts (password <code className="font-mono text-accent">Demo@12345</code>)</p>
          <ul className="space-y-1">
            {ACCOUNTS.map(([e, d]) => (
              <li key={e}>
                <button type="button" onClick={() => setEmail(e)} className="w-full rounded-md px-2 py-1 text-left text-xs hover:bg-ink-800">
                  <span className="font-mono text-fg">{e}</span><span className="block text-fg-dim">{d}</span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card className="self-start p-6">
        <form onSubmit={submit} className="space-y-4" aria-describedby={error ? "login-error" : undefined}>
          <h2 className="text-base font-semibold">Sign in</h2>
          <Field label="Email"><Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
          <Field label="Password"><Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
          {error && <p id="login-error" role="alert" className="text-sm text-danger">{error}</p>}
          <Button type="submit" variant="primary" className="w-full" loading={busy}>Sign in</Button>
        </form>
      </Card>
    </div>
  );
}

export default function LoginPage() {
  return (
    <>
      <DisclaimerBar />
      <Suspense><LoginForm /></Suspense>
    </>
  );
}
