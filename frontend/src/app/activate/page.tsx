import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { FullPageSpinner } from "@/components/auth/auth-gate";
import { ActivateForm } from "@/components/auth/activate-form";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Activate");
  // Токен приглашения в адресе: страница не должна отдавать его в Referer при переходе по ссылкам.
  return { title: t("metadataTitle"), referrer: "no-referrer" };
}

export default function ActivatePage() {
  return (
    <Suspense fallback={<FullPageSpinner />}>
      <ActivateForm />
    </Suspense>
  );
}
