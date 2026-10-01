"use client";

import { Check, Copy } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";

export function RecoveryCodes({
  codes,
  onContinue,
}: {
  codes: string[];
  onContinue: () => void;
}) {
  const t = useTranslations("Auth.recoveryCodes");
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);

  async function copyAll() {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2_000);
    } catch {
      // буфер обмена недоступен: коды можно выделить и переписать вручную
    }
  }

  return (
    <div className="space-y-5">
      <ul className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg bg-muted px-4 py-3 font-mono text-base">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <Button type="button" variant="outline" size="sm" onClick={() => void copyAll()}>
        {copied ? <Check data-icon="inline-start" aria-hidden /> : <Copy data-icon="inline-start" aria-hidden />}
        {copied ? t("copied") : t("copyAll")}
      </Button>
      <label className="flex items-start gap-3 text-sm">
        <input
          type="checkbox"
          className="mt-0.5 size-5 shrink-0 accent-primary"
          checked={saved}
          onChange={(event) => setSaved(event.target.checked)}
        />
        <span>{t("acknowledge")}</span>
      </label>
      <Button
        type="button"
        size="lg"
        className="h-11 w-full text-base"
        disabled={!saved}
        onClick={onContinue}
      >
        {t("continue")}
      </Button>
    </div>
  );
}
