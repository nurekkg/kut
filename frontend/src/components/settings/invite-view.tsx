"use client";

import { Check, Copy } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDateTime } from "@/lib/format";
import type { Invite } from "@/lib/settings/types";

// Ссылка одноразовая и нигде не сохраняется: после закрытия окна её уже не показать.
export function InviteView({ invite }: { invite: Invite }) {
  const t = useTranslations("Settings.users");
  const common = useTranslations("Settings.common");
  const [copied, setCopied] = useState(false);
  const url = `${window.location.origin}${invite.path}`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2_000);
    } catch {
      // буфер обмена недоступен: ссылку можно выделить и скопировать вручную
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {t("inviteText", { expires: formatDateTime(invite.expires_at) })}
      </p>
      <div className="space-y-1.5">
        <Label htmlFor="invite-url">{t("inviteLink")}</Label>
        <div className="flex gap-2">
          <Input
            id="invite-url"
            readOnly
            value={url}
            className="font-mono text-xs"
            onFocus={(event) => event.currentTarget.select()}
          />
          <Button type="button" variant="outline" className="h-9 shrink-0" onClick={() => void copy()}>
            {copied ? <Check data-icon="inline-start" aria-hidden /> : <Copy data-icon="inline-start" aria-hidden />}
            {copied ? common("copied") : common("copy")}
          </Button>
        </div>
      </div>
    </div>
  );
}
