import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { FullPageSpinner } from "@/components/auth/auth-gate";
import { LoginFlow } from "@/components/auth/login-flow";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Auth");
  return { title: t("metadataTitle") };
}

export default function LoginPage() {
  // useSearchParams (параметр next) требует Suspense при статической сборке страницы.
  return (
    <Suspense fallback={<FullPageSpinner />}>
      <LoginFlow />
    </Suspense>
  );
}
