"use client";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { MotionConfig } from "framer-motion";
import {
  BarChart3, Bell, BookOpen, Building2, FileSearch, LayoutDashboard, LogOut, Menu, Plug, Scale, ScrollText, Search,
  Settings, ShieldCheck, Sparkles, X, FolderKanban,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { api, post } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { cn, dt, label } from "@/lib/utils";
import { Badge, Button } from "./ui/primitives";

export type Me = { id: string; email: string; full_name: string; role: string; institution_name?: string | null; permissions: string[] };
const SessionCtx = createContext<{ me: Me; can: (p: string) => boolean } | null>(null);
export const useSession = () => {
  const s = useContext(SessionCtx);
  if (!s) throw new Error("useSession outside provider");
  return s;
};

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/demo", label: "Guided demo", icon: Sparkles },
  { href: "/applications", label: "Applications", icon: FolderKanban },
  { href: "/document-intelligence", label: "Document intelligence", icon: FileSearch },
  { href: "/verification", label: "Verification", icon: ShieldCheck },
  { href: "/compliance", label: "Compliance (RAG)", icon: Scale },
  { href: "/infrastructure", label: "Infrastructure", icon: Building2 },
  { href: "/regulatory-rules", label: "Regulatory rules", icon: BookOpen, perm: "rule:read" },
  { href: "/connectors", label: "Connectors", icon: Plug, perm: "connector:read" },
  { href: "/reports", label: "Reports", icon: BarChart3, perm: "report:read" },
  { href: "/audit", label: "Audit trail", icon: ScrollText, perm: "audit:read" },
  { href: "/admin", label: "Administration", icon: Settings, perm: "user:manage" },
];

export function DisclaimerBar() {
  return (
    <div role="note" className="border-b border-warn/20 bg-warn/[0.06] px-4 py-1.5 text-center text-[11px] text-warn/90">
      Independent demonstration prototype — not affiliated with or an official product of AICTE. All data shown is
      synthetic. Findings are advisory and require human review; no approval or rejection decisions are made.
    </div>
  );
}

function Sidebar({ me, onNavigate }: { me: Me; onNavigate?: () => void }) {
  const path = usePathname();
  const items = NAV.filter((n) => !n.perm || me.permissions.includes(n.perm));
  return (
    <nav aria-label="Main" className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-4 py-4">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-accent to-ok text-ink-950">
          <ShieldCheck className="h-4.5 w-4.5" aria-hidden />
        </div>
        <div>
          <div className="text-sm font-semibold leading-tight">IntelliScrutiny</div>
          <div className="text-[10px] uppercase tracking-wider text-fg-dim">Prototype · synthetic</div>
        </div>
      </div>
      <ul className="flex-1 space-y-0.5 overflow-y-auto px-2 scroll-thin">
        {items.map(({ href, label: l, icon: Icon }) => {
          const active = path === href || path.startsWith(href + "/");
          return (
            <li key={href}>
              <Link href={href} onClick={onNavigate} aria-current={active ? "page" : undefined}
                className={cn("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                  active ? "bg-accent/10 text-accent" : "text-fg-muted hover:bg-ink-800 hover:text-fg")}>
                <Icon className="h-4 w-4 shrink-0" aria-hidden />{l}
              </Link>
            </li>
          );
        })}
      </ul>
      <div className="border-t border-line p-3 text-xs">
        <div className="truncate font-medium">{me.full_name}</div>
        <div className="truncate text-fg-dim">{label(me.role)}{me.institution_name ? ` · ${me.institution_name}` : ""}</div>
      </div>
    </nav>
  );
}

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Record<string, { id: string; label: string; sub?: string; href: string }[]>>();
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key === "k") { e.preventDefault(); ref.current?.focus(); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (q.trim().length < 2) { setRes(undefined); return; }
    const t = setTimeout(() => api(`/search?q=${encodeURIComponent(q.trim())}`).then(setRes).catch(() => setRes(undefined)), 250);
    return () => clearTimeout(t);
  }, [q]);
  const groups = res ? Object.entries(res).filter(([, v]) => v.length) : [];
  return (
    <div className="relative w-full max-w-md">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-dim" aria-hidden />
      <input ref={ref} value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)} placeholder="Search applications, documents, findings, rules… (Ctrl K)"
        aria-label="Global search" className="h-9 w-full rounded-lg border border-line bg-ink-850 pl-9 pr-3 text-sm placeholder:text-fg-dim" />
      {open && res && (
        <div className="absolute z-30 mt-1 w-full overflow-hidden rounded-lg border border-line bg-ink-900 shadow-xl" role="listbox">
          {groups.length === 0 && <p className="p-3 text-xs text-fg-muted">No matches</p>}
          {groups.map(([g, items]) => (
            <div key={g} className="border-b border-line last:border-0">
              <div className="px-3 pt-2 text-[10px] uppercase tracking-wider text-fg-dim">{g}</div>
              {items.map((i) => (
                <button key={i.id} role="option" aria-selected={false} onMouseDown={() => { router.push(i.href); setOpen(false); setQ(""); }}
                  className="block w-full px-3 py-1.5 text-left text-sm hover:bg-ink-800">
                  <span className="block truncate">{i.label}</span>
                  {i.sub && <span className="block truncate text-[11px] text-fg-dim">{label(i.sub)}</span>}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Notifications() {
  const { data, reload } = useApi<{ unread: number; items: any[] }>("/notifications", { poll: 15000 });
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <Button variant="ghost" size="icon" aria-label={`Notifications (${data?.unread ?? 0} unread)`} className="relative">
          <Bell className="h-4 w-4" />
          {!!data?.unread && <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-accent" />}
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={6} className="z-40 max-h-96 w-80 overflow-y-auto rounded-lg border border-line bg-ink-900 p-1 shadow-xl scroll-thin">
          <div className="flex items-center justify-between px-2 py-1.5">
            <span className="text-xs font-semibold">Notifications</span>
            <button className="text-[11px] text-accent hover:underline" onClick={() => post("/notifications/read-all").then(reload)}>Mark all read</button>
          </div>
          {!data?.items.length && <p className="px-2 py-4 text-center text-xs text-fg-muted">Nothing yet</p>}
          {data?.items.map((n) => (
            <DropdownMenu.Item key={n.id} asChild>
              <Link href={n.link ?? "#"} className={cn("block rounded-md px-2 py-2 text-xs outline-none data-[highlighted]:bg-ink-800", !n.read && "bg-accent/5")}>
                <span className="block font-medium">{n.title}</span>
                <span className="block text-fg-muted">{n.body}</span>
                <span className="block text-[10px] text-fg-dim">{dt(n.created_at)}</span>
              </Link>
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

export function Breadcrumbs() {
  const path = usePathname();
  const parts = path.split("/").filter(Boolean);
  return (
    <nav aria-label="Breadcrumb" className="text-xs text-fg-dim">
      <ol className="flex flex-wrap items-center gap-1">
        <li><Link href="/dashboard" className="hover:text-fg">Home</Link></li>
        {parts.map((p, i) => (
          <li key={i} className="flex items-center gap-1">
            <span aria-hidden>/</span>
            {i === parts.length - 1 ? <span className="text-fg-muted" aria-current="page">{p.length > 20 ? "Detail" : label(p.replace(/-/g, "_"))}</span>
              : <Link href={"/" + parts.slice(0, i + 1).join("/")} className="hover:text-fg">{label(p.replace(/-/g, "_"))}</Link>}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { data, error } = useApi<{ user: Me }>("/auth/me");
  const [mobile, setMobile] = useState(false);
  const router = useRouter();
  if (error) return <div className="grid min-h-screen place-items-center text-sm text-fg-muted">Redirecting to sign in…</div>;
  if (!data) return <div className="grid min-h-screen place-items-center" role="status"><div className="skeleton h-10 w-40" /></div>;
  const me = data.user;
  const can = (p: string) => me.permissions.includes(p);
  return (
    <SessionCtx.Provider value={{ me, can }}>
      <MotionConfig reducedMotion="user">
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-accent focus:px-3 focus:py-1 focus:text-ink-950">Skip to content</a>
        <DisclaimerBar />
        <div className="flex min-h-[calc(100vh-29px)]">
          <aside className="sticky top-0 hidden h-[calc(100vh-29px)] w-60 shrink-0 border-r border-line bg-ink-900/60 lg:block"><Sidebar me={me} /></aside>
          {mobile && (
            <div className="fixed inset-0 z-40 lg:hidden">
              <div className="absolute inset-0 bg-black/60" onClick={() => setMobile(false)} />
              <aside className="absolute inset-y-0 left-0 w-64 border-r border-line bg-ink-900">
                <Button variant="ghost" size="icon" className="absolute right-2 top-3" aria-label="Close menu" onClick={() => setMobile(false)}><X className="h-4 w-4" /></Button>
                <Sidebar me={me} onNavigate={() => setMobile(false)} />
              </aside>
            </div>
          )}
          <div className="flex min-w-0 flex-1 flex-col">
            <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-line bg-ink-950/80 px-4 py-2.5 backdrop-blur">
              <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open menu" onClick={() => setMobile(true)}><Menu className="h-4 w-4" /></Button>
              <GlobalSearch />
              <div className="ml-auto flex items-center gap-1">
                <Badge tone="warn" className="hidden sm:inline-flex">SYNTHETIC DATA</Badge>
                <Notifications />
                <Button variant="ghost" size="icon" aria-label="Sign out" onClick={async () => { await post("/auth/logout").catch(() => null); router.push("/login"); }}>
                  <LogOut className="h-4 w-4" />
                </Button>
              </div>
            </header>
            <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-5 sm:px-6">{children}</main>
          </div>
        </div>
      </MotionConfig>
    </SessionCtx.Provider>
  );
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-5 space-y-2">
      <Breadcrumbs />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {sub && <p className="mt-1 max-w-3xl text-sm text-fg-muted">{sub}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}
