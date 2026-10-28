"use client";
import * as DialogP from "@radix-ui/react-dialog";
import * as TabsP from "@radix-ui/react-tabs";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { LoaderCircle, X } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------- Button
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 whitespace-nowrap",
  {
    variants: {
      variant: {
        primary: "bg-accent text-ink-950 hover:bg-cyan-300",
        secondary: "bg-ink-700 text-fg hover:bg-ink-600 border border-line",
        ghost: "text-fg-muted hover:text-fg hover:bg-ink-800",
        danger: "bg-danger/15 text-danger border border-danger/30 hover:bg-danger/25",
        ok: "bg-ok/15 text-ok border border-ok/30 hover:bg-ok/25",
      },
      size: { sm: "h-8 px-3 text-xs", md: "h-9 px-4", icon: "h-9 w-9" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild, loading, children, disabled, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} disabled={disabled || loading} {...props}>
        {asChild ? children : <>{loading && <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden />}{children}</>}
      </Comp>
    );
  },
);
Button.displayName = "Button";

// ---------------------------------------------------------------- Card
export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-line bg-ink-900/80 backdrop-blur-sm", className)} {...p} />;
}
export function CardHeader({ title, sub, action, className }: { title: React.ReactNode; sub?: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 border-b border-line px-4 py-3", className)}>
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-fg">{title}</h2>
        {sub && <p className="mt-0.5 text-xs text-fg-muted">{sub}</p>}
      </div>
      {action}
    </div>
  );
}
export const CardBody = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("p-4", className)} {...p} />;

// ---------------------------------------------------------------- Badge
const tones = {
  neutral: "bg-ink-700 text-fg-muted border-line",
  accent: "bg-accent/10 text-accent border-accent/30",
  ok: "bg-ok/10 text-ok border-ok/30",
  warn: "bg-warn/10 text-warn border-warn/30",
  danger: "bg-danger/10 text-danger border-danger/30",
  violet: "bg-violet-400/10 text-violet-300 border-violet-400/30",
};
export type Tone = keyof typeof tones;
export function Badge({ tone = "neutral", className, children, title }: { tone?: Tone; className?: string; children: React.ReactNode; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4", tones[tone], className)}>
      {children}
    </span>
  );
}

// ---------------------------------------------------------------- Form controls
const field = "w-full rounded-lg border border-line bg-ink-850 px-3 py-2 text-sm text-fg placeholder:text-fg-dim focus:border-accent/60";
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...p }, ref) => <input ref={ref} className={cn(field, "h-9 py-0", className)} {...p} />);
Input.displayName = "Input";
export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...p }, ref) => <textarea ref={ref} className={cn(field, "min-h-[80px]", className)} {...p} />);
Textarea.displayName = "Textarea";
export function Select({ className, children, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(field, "h-9 py-0 pr-8", className)} {...p}>{children}</select>;
}
export function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-fg-muted">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-fg-dim">{hint}</span>}
    </label>
  );
}

// ---------------------------------------------------------------- Dialog
export function Dialog({ open, onOpenChange, title, description, children, wide }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: string; children: React.ReactNode; wide?: boolean;
}) {
  return (
    <DialogP.Root open={open} onOpenChange={onOpenChange}>
      <DialogP.Portal>
        <DialogP.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" />
        <DialogP.Content className={cn("fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-line bg-ink-900 p-5 shadow-2xl scroll-thin", wide ? "max-w-4xl" : "max-w-lg")}>
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <DialogP.Title className="text-base font-semibold">{title}</DialogP.Title>
              {description ? <DialogP.Description className="mt-1 text-xs text-fg-muted">{description}</DialogP.Description>
                : <DialogP.Description className="sr-only">{title}</DialogP.Description>}
            </div>
            <DialogP.Close asChild><Button variant="ghost" size="icon" aria-label="Close"><X className="h-4 w-4" /></Button></DialogP.Close>
          </div>
          {children}
        </DialogP.Content>
      </DialogP.Portal>
    </DialogP.Root>
  );
}

// ---------------------------------------------------------------- Tabs
export const Tabs = TabsP.Root;
export function TabsList({ items }: { items: { value: string; label: React.ReactNode }[] }) {
  return (
    <TabsP.List className="flex gap-1 overflow-x-auto border-b border-line scroll-thin" aria-label="Sections">
      {items.map((i) => (
        <TabsP.Trigger key={i.value} value={i.value}
          className="relative whitespace-nowrap px-3 py-2 text-sm text-fg-muted transition-colors hover:text-fg data-[state=active]:text-accent data-[state=active]:after:absolute data-[state=active]:after:inset-x-2 data-[state=active]:after:-bottom-px data-[state=active]:after:h-0.5 data-[state=active]:after:bg-accent">
          {i.label}
        </TabsP.Trigger>
      ))}
    </TabsP.List>
  );
}
export const TabsContent = ({ className, ...p }: React.ComponentProps<typeof TabsP.Content>) =>
  <TabsP.Content className={cn("pt-4 focus-visible:ring-0", className)} {...p} />;

// ---------------------------------------------------------------- Table
export function Table({ head, children, className }: { head: React.ReactNode[]; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-x-auto scroll-thin", className)}>
      <table className="w-full text-left text-sm">
        <thead className="border-b border-line text-[11px] uppercase tracking-wide text-fg-dim">
          <tr>{head.map((h, i) => <th key={i} scope="col" className="px-3 py-2 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-line/70">{children}</tbody>
      </table>
    </div>
  );
}
export const Td = ({ className, ...p }: React.TdHTMLAttributes<HTMLTableCellElement>) =>
  <td className={cn("px-3 py-2.5 align-top", className)} {...p} />;

// ---------------------------------------------------------------- States
export const Skeleton = ({ className }: { className?: string }) => <div className={cn("skeleton h-4", className)} aria-hidden />;

export function LoadingBlock({ rows = 4 }: { rows?: number }) {
  return <div className="space-y-2 p-4" role="status" aria-label="Loading">{Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-8" />)}</div>;
}

export function EmptyState({ icon, title, body, action }: { icon?: React.ReactNode; title: string; body?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      {icon && <div className="text-fg-dim">{icon}</div>}
      <p className="text-sm font-medium">{title}</p>
      {body && <p className="max-w-md text-xs text-fg-muted">{body}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: Error & { requestId?: string }; retry?: () => void }) {
  return (
    <div role="alert" className="m-4 rounded-lg border border-danger/30 bg-danger/5 p-4 text-sm">
      <p className="font-medium text-danger">Could not load this data</p>
      <p className="mt-1 text-fg-muted">{error.message}{error.requestId && <span className="ml-2 font-mono text-xs">ref {error.requestId}</span>}</p>
      {retry && <Button size="sm" className="mt-3" onClick={retry}>Retry</Button>}
    </div>
  );
}

export function Stat({ label, value, sub, tone = "neutral", icon }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: Tone; icon?: React.ReactNode }) {
  const color = { neutral: "text-fg", accent: "text-accent", ok: "text-ok", warn: "text-warn", danger: "text-danger", violet: "text-violet-300" }[tone];
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between text-xs text-fg-muted"><span>{label}</span>{icon}</div>
      <div className={cn("mt-2 text-2xl font-semibold tabular-nums", color)}>{value}</div>
      {sub && <div className="mt-1 text-[11px] text-fg-dim">{sub}</div>}
    </Card>
  );
}

export function Meter({ value, tone = "accent" }: { value: number; tone?: "accent" | "ok" | "warn" | "danger" }) {
  const bg = { accent: "bg-accent", ok: "bg-ok", warn: "bg-warn", danger: "bg-danger" }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-700" role="meter" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      <div className={cn("h-full rounded-full", bg)} style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </div>
  );
}
