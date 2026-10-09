import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { ApprovalsView } from "@/components/approvals/approvals-view";
import { AuthGate } from "@/components/auth/auth-gate";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Approvals");
  return { title: t("metadataTitle") };
}

export default function ApprovalsPage() {
  return (
    <AuthGate>
      <ApprovalsView />
    </AuthGate>
  );
}
