import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AuthGate } from "@/components/auth/auth-gate";
import { ProfileView } from "@/components/profile/profile-view";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Profile");
  return { title: t("metadataTitle") };
}

export default function ProfilePage() {
  return (
    <AuthGate>
      <ProfileView />
    </AuthGate>
  );
}
