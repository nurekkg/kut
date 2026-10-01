import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { AuthGate, FullPageSpinner } from "@/components/auth/auth-gate";
import { ProjectWorkspace } from "@/components/projects/project-workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Workspace");
  return { title: t("metadataTitle") };
}

export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  return (
    <AuthGate>
      {/* useSearchParams (выбранный узел) требует Suspense */}
      <Suspense fallback={<FullPageSpinner />}>
        <ProjectWorkspace projectId={id} />
      </Suspense>
    </AuthGate>
  );
}
