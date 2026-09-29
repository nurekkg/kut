import { getTranslations } from "next-intl/server";

import { HealthStatus } from "@/components/health-status";

export default async function Home() {
  const t = await getTranslations("Home");

  return (
    <main className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center gap-6 px-4 py-10">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </header>
      <HealthStatus />
    </main>
  );
}
