"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ClipboardList, LogOut, PlusCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { OutboxQueue } from "@/components/field/queue";
import { Records } from "@/components/field/records";
import { Wizard } from "@/components/field/wizard";
import { Button, buttonVariants } from "@/components/ui/button";
import { attach, detach, useOutbox } from "@/lib/field/outbox";
import { invalidateAllProgress } from "@/lib/schedule/queries";
import { cn } from "@/lib/utils";

type Tab = "new" | "records";

// Упрощённый экран мастера (/m): телефон в одной руке, крупные кнопки, один вопрос на шаг. Очередь
// отправки живёт на устройстве: пока сервер не подтвердил запись, она не теряется.
export function FieldApp() {
  const t = useTranslations("Field");
  const { user, signOut } = useAuth();
  const router = useRouter();
  const queryClient = useQueryClient();
  const outbox = useOutbox();
  const [tab, setTab] = useState<Tab>("new");
  const [leaving, setLeaving] = useState(false);

  const userId = user?.id ?? null;
  useEffect(() => {
    if (!userId) return;
    void attach(userId);
    return () => detach();
  }, [userId]);

  // Каждая доставленная запись меняет проценты и списки на всех экранах.
  const delivered = outbox.completed;
  useEffect(() => {
    if (delivered > 0) void invalidateAllProgress(queryClient);
  }, [delivered, queryClient]);

  async function logout() {
    setLeaving(true);
    try {
      await signOut();
    } finally {
      router.replace("/login");
    }
  }

  if (!user) return null;
  const allowed = user.permissions.includes("work_actual:write");
  const tabs: { id: Tab; label: string; icon: typeof PlusCircle }[] = [
    { id: "new", label: t("tabs.new"), icon: PlusCircle },
    { id: "records", label: t("tabs.records"), icon: ClipboardList },
  ];

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-xl flex-col">
      <header className="flex items-center justify-between gap-3 px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium" data-testid="user-name">
            {user.full_name}
          </p>
          <Link
            href="/projects"
            className="text-xs text-muted-foreground underline-offset-4 hover:underline"
          >
            {t("toProjects")}
          </Link>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon-lg"
          className="size-12"
          onClick={() => void logout()}
          disabled={leaving}
          aria-label={t("logout")}
        >
          <LogOut aria-hidden />
        </Button>
      </header>

      <main className="flex-1 space-y-6 px-4 pt-2 pb-32">
        {allowed ? (
          <>
            <OutboxQueue />
            {tab === "new" ? <Wizard onOpenRecords={() => setTab("records")} /> : <Records />}
          </>
        ) : (
          <div role="alert" className="space-y-2 rounded-xl border p-4">
            <h1 className="text-lg font-semibold">{t("noAccess.title")}</h1>
            <p className="text-sm text-muted-foreground">{t("noAccess.text")}</p>
            <Link href="/projects" className={buttonVariants({ variant: "outline", size: "lg", className: "h-12" })}>
              {t("toProjects")}
            </Link>
          </div>
        )}
      </main>

      {allowed ? (
        <nav
          aria-label={t("nav")}
          className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur"
        >
          <div className="mx-auto grid max-w-xl grid-cols-2">
            {tabs.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-current={tab === item.id ? "page" : undefined}
                onClick={() => setTab(item.id)}
                data-testid={`tab-${item.id}`}
                className={cn(
                  "flex min-h-16 flex-col items-center justify-center gap-1 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                  tab === item.id ? "text-foreground" : "text-muted-foreground",
                )}
              >
                <item.icon className="size-6" aria-hidden />
                {item.label}
              </button>
            ))}
          </div>
        </nav>
      ) : null}
    </div>
  );
}
