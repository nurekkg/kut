import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

import { AuthGate } from "@/components/auth/auth-gate";
import { UserMenu } from "@/components/auth/user-menu";
import { AdminGate, SettingsNav } from "@/components/settings/settings-shell";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Settings");
  return { title: t("metadataTitle") };
}

export default async function SettingsLayout({ children }: { children: ReactNode }) {
  const t = await getTranslations("Settings");

  return (
    <AuthGate>
      <AdminGate>
        <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-6">
          <UserMenu />
          <header className="space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
            <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
          </header>
          <SettingsNav />
          {children}
        </main>
      </AdminGate>
    </AuthGate>
  );
}
