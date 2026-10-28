import { Suspense } from "react";
import { AppShell } from "@/components/shell";

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell>
      <Suspense fallback={<div className="skeleton h-40" />}>{children}</Suspense>
    </AppShell>
  );
}
