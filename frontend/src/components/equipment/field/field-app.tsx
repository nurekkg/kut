"use client";

import { useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ClipboardList, LogOut, Upload } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { useLogout } from "@/components/auth/logout-guard";
import { Button, buttonVariants } from "@/components/ui/button";
import { attachShiftQueue, detachShiftQueue, useShiftQueue } from "@/lib/equipment/field/queue";
import type { SubmitOutcome } from "@/lib/equipment/field/types";
import { cn } from "@/lib/utils";

import { MineView } from "./mine-view";
import { QueueView } from "./queue-view";
import { ShiftForm, type FormTarget } from "./shift-form";
import { TodayView } from "./today-view";

type Tab = "today" | "mine" | "queue";

// Экран машиниста техники (/m/equipment, SE-2b): телефон в одной руке, крупные кнопки, очередь на
// устройстве. Права и объект проверяет сервер; здесь только отсев по роли машиниста.
export function EquipmentFieldApp() {
  const t = useTranslations("EquipmentField");
  const { user } = useAuth();
  const { request: logout, leaving, dialog: logoutDialog } = useLogout();
  const queue = useShiftQueue();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("today");
  const [target, setTarget] = useState<FormTarget | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const userId = user?.id ?? null;

  useEffect(() => {
    if (!userId) return;
    void attachShiftQueue(userId);
    return () => detachShiftQueue();
  }, [userId]);

  if (!user) return null;
  const allowed = user.role === "machinist";
  const pending = queue.entries.length;
  const tabs: { id: Tab; label: string; icon: typeof CalendarDays }[] = [
    { id: "today", label: t("tabs.today"), icon: CalendarDays },
    { id: "mine", label: t("tabs.mine"), icon: ClipboardList },
    { id: "queue", label: t("tabs.queue"), icon: Upload },
  ];

  function finished(outcome: SubmitOutcome): void {
    setTarget(null);
    setTab(outcome.kind === "failed" ? "queue" : "today");
    if (outcome.kind === "sent") setNotice(t("form.sent"));
    else if (outcome.kind === "queued") setNotice(t("form.queued"));
    else setNotice(null);
    void queryClient.invalidateQueries({ queryKey: ["equipment"] });
  }

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-xl flex-col">
      {logoutDialog}
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

      <main className="flex-1 space-y-4 px-4 pt-2 pb-32">
        {allowed ? (
          target ? (
            <ShiftForm
              target={target}
              onBack={() => setTarget(null)}
              onFinished={finished}
            />
          ) : (
            <>
              {notice ? (
                <p role="status" className="rounded-xl bg-muted p-3 text-sm" data-testid="notice">
                  {notice}
                </p>
              ) : null}
              {tab === "today" ? (
                <TodayView
                  onEnter={(item) => {
                    setNotice(null);
                    setTarget({ kind: "new", ...item });
                  }}
                />
              ) : null}
              {tab === "mine" ? (
                <MineView onFix={(item) => setTarget({ kind: "fix", ...item })} />
              ) : null}
              {tab === "queue" ? <QueueView entries={queue.entries} busy={queue.busy} /> : null}
            </>
          )
        ) : (
          <div role="alert" className="space-y-2 rounded-xl border p-4">
            <h1 className="text-lg font-semibold">{t("noAccess")}</h1>
            <Link
              href="/projects"
              className={buttonVariants({ variant: "outline", size: "lg", className: "h-12" })}
            >
              {t("toProjects")}
            </Link>
          </div>
        )}
      </main>

      {allowed && !target ? (
        <nav
          aria-label={t("nav")}
          className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur"
        >
          <ul className="mx-auto grid max-w-xl grid-cols-3 gap-1 p-2">
            {tabs.map((item) => {
              const Icon = item.icon;
              const active = tab === item.id;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => setTab(item.id)}
                    aria-current={active ? "page" : undefined}
                    data-testid={`tab-${item.id}`}
                    className={cn(
                      "flex h-14 w-full flex-col items-center justify-center gap-0.5 rounded-xl text-xs font-medium",
                      active ? "bg-muted text-foreground" : "text-muted-foreground",
                    )}
                  >
                    <Icon className="size-5" aria-hidden />
                    <span>
                      {item.label}
                      {item.id === "queue" && pending > 0 ? ` (${pending})` : ""}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>
      ) : null}
      {leaving ? <span className="sr-only">{t("leaving")}</span> : null}
    </div>
  );
}
