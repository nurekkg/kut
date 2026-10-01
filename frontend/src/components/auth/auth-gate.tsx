"use client";

import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";

export function FullPageSpinner() {
  const t = useTranslations("Auth");
  return (
    <main className="flex flex-1 items-center justify-center p-6" role="status">
      <Loader2 className="size-6 text-muted-foreground motion-safe:animate-spin" aria-hidden />
      <span className="sr-only">{t("checkingSession")}</span>
    </main>
  );
}

// Защита страницы на клиенте нужна только для удобства: данные закрывает сервер (401, RLS).
export function AuthGate({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === "anonymous") router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [status, pathname, router]);

  if (status !== "authenticated") return <FullPageSpinner />;
  return <>{children}</>;
}
