import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { AuthGate, FullPageSpinner } from "@/components/auth/auth-gate";
import { ProjectWorkspace } from "@/components/projects/project-workspace";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Workspace");
  return { title: t("metadataTitle") };
}

export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  // Идентификатор попадает в адреса запросов к API: принимаем только UUID.
  if (!UUID.test(id)) notFound();

  return (
    <AuthGate>
      {/* useSearchParams (выбранный узел) требует Suspense */}
      <Suspense fallback={<FullPageSpinner />}>
        <ProjectWorkspace projectId={id} />
      </Suspense>
    </AuthGate>
  );
}
