"use client";

import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useCallback, useState, type ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Modal } from "@/components/settings/dialogs";
import { Button } from "@/components/ui/button";
import { countUnsent, detach, discardAll, flush } from "@/lib/field/outbox";

type Stage =
  | { kind: "closed" }
  | { kind: "ask"; count: number }
  | { kind: "sending"; count: number }
  | { kind: "left"; count: number };

// Выход из приложения. Если на устройстве лежат неотправленные записи мастера (с фото и геометками),
// сначала спрашиваем: отправить сейчас или удалить, - иначе они остались бы на общем телефоне. После
// выбора очередь пользователя очищается и сеанс закрывается.
export function useLogout(): { request: () => Promise<void>; leaving: boolean; dialog: ReactNode } {
  const t = useTranslations("LogoutGuard");
  const { user, signOut } = useAuth();
  const router = useRouter();
  const [stage, setStage] = useState<Stage>({ kind: "closed" });
  const [leaving, setLeaving] = useState(false);
  const userId = user?.id ?? null;

  const finish = useCallback(async () => {
    setLeaving(true);
    try {
      if (userId) await discardAll(userId); // очистка очереди: после выхода на устройстве её быть не должно
      await signOut();
    } finally {
      detach();
      router.replace("/login");
    }
  }, [userId, signOut, router]);

  const request = useCallback(async () => {
    if (!userId) return;
    const count = await countUnsent(userId);
    if (count === 0) await finish();
    else setStage({ kind: "ask", count });
  }, [userId, finish]);

  async function sendNow() {
    if (!userId || stage.kind === "closed") return;
    setStage({ kind: "sending", count: stage.count });
    const left = await flush(userId);
    if (left === 0) await finish();
    else setStage({ kind: "left", count: left });
  }

  async function discard() {
    await finish();
  }

  const sending = stage.kind === "sending";
  const dialog =
    stage.kind === "closed" ? null : (
      <Modal
        title={t("title")}
        description={
          stage.kind === "left"
            ? t("textLeft", { count: stage.count })
            : t("text", { count: stage.count })
        }
        onClose={() => !sending && !leaving && setStage({ kind: "closed" })}
      >
        <div className="space-y-2" data-testid="logout-dialog">
          <Button
            type="button"
            size="lg"
            className="h-12 w-full"
            disabled={sending || leaving}
            onClick={() => void sendNow()}
            data-testid="logout-send"
          >
            {sending ? (
              <Loader2 data-icon="inline-start" className="motion-safe:animate-spin" aria-hidden />
            ) : null}
            {sending ? t("sending") : t("send")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="lg"
            className="h-12 w-full"
            disabled={sending || leaving}
            onClick={() => void discard()}
            data-testid="logout-discard"
          >
            {t("discard")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="h-12 w-full"
            disabled={sending || leaving}
            onClick={() => setStage({ kind: "closed" })}
            data-testid="logout-stay"
          >
            {t("stay")}
          </Button>
        </div>
      </Modal>
    );

  return { request, leaving, dialog };
}
