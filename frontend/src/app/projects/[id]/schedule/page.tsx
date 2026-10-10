import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { AuthGate, FullPageSpinner } from "@/components/auth/auth-gate";
import { ScheduleView } from "@/components/schedule/schedule-view";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Schedule");
  return { title: t("metadataTitle") };
}

export default async function SchedulePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  // Идентификатор попадает в адреса запросов к API: принимаем только UUID.
  if (!UUID.test(id)) notFound();

  return (
    <AuthGate>
      {/* useSearchParams (фильтр по узлу из ссылки) требует Suspense */}
      <Suspense fallback={<FullPageSpinner />}>
        <ScheduleView projectId={id} />
      </Suspense>
    </AuthGate>
  );
}
