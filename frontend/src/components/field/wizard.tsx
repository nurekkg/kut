"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Building2, CheckCircle2, Loader2, RotateCw } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { EvidenceStep } from "@/components/field/evidence-step";
import { NodeStep } from "@/components/field/node-step";
import { VolumeStep, normalizeVolume } from "@/components/field/volume-step";
import { WorkStep, type PickedWork } from "@/components/field/work-step";
import { QueryState, useCodeMessage } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { newFactEntry } from "@/lib/field/entry";
import type { GeoState } from "@/lib/field/geo";
import { enqueue, retryEntry, useOutbox } from "@/lib/field/outbox";
import type { OutboxPhoto } from "@/lib/field/outbox-types";
import { rememberNode } from "@/lib/field/recents";
import { API, PROJECTS_KEY, treeKey } from "@/lib/projects/keys";
import { ancestors, buildIndex, pathLabel } from "@/lib/projects/tree";
import type { Project, TreeNode } from "@/lib/projects/types";
import { todayIso } from "@/lib/schedule/dates";

type Step = "node" | "work" | "volume" | "evidence" | "sent";
const FLOW: Step[] = ["node", "work", "volume", "evidence"];

// Мастер вносит факт за пять коротких шагов: объект (если он один - сразу пропускается), узел,
// работа, объём, фото и место. Каждый шаг - один вопрос и крупные кнопки.
export function Wizard({ onOpenRecords }: { onOpenRecords: () => void }) {
  const t = useTranslations("Field");
  const { user } = useAuth();
  const codeMessage = useCodeMessage();

  const projects = useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: () => api.get<Project[]>(`${API}/projects`),
  });
  const list = useMemo(() => projects.data ?? [], [projects.data]);
  const [chosenProject, setChosenProject] = useState<string | null>(null);
  const projectId = chosenProject ?? (list.length === 1 ? list[0].id : null);
  const hasProjectStep = list.length > 1;

  const tree = useQuery({
    queryKey: treeKey(projectId ?? "none"),
    queryFn: () => api.get<TreeNode[]>(`${API}/projects/${projectId}/tree`),
    enabled: projectId !== null,
    staleTime: 5 * 60_000,
  });
  const index = useMemo(() => buildIndex(tree.data ?? []), [tree.data]);

  const [step, setStep] = useState<Step>("node");
  const [node, setNode] = useState<TreeNode | null>(null);
  const [picked, setPicked] = useState<PickedWork | null>(null);
  const [volume, setVolume] = useState("");
  const [workDate, setWorkDate] = useState(todayIso);
  const [photos, setPhotos] = useState<OutboxPhoto[]>([]);
  const [entryId, setEntryId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Пока список объектов не пришёл, шаг считается «узел»: так экран не мигает шагом «объект».
  const current: Step | "project" =
    projects.isPending ? "node" : projectId === null ? "project" : step;
  const order: (Step | "project")[] = hasProjectStep ? ["project", ...FLOW] : FLOW;
  const position = order.indexOf(current);

  function reset(keepProject: boolean) {
    if (!keepProject) setChosenProject(null);
    setStep("node");
    setNode(null);
    setPicked(null);
    setVolume("");
    setWorkDate(todayIso());
    setPhotos([]);
    setEntryId(null);
    setSaveError(null);
  }

  function back() {
    if (current === "work") setStep("node");
    else if (current === "volume") setStep("work");
    else if (current === "evidence") setStep("volume");
    else if (current === "node" && hasProjectStep) reset(false);
  }

  function pickNode(selected: TreeNode) {
    if (user && projectId) rememberNode(user.id, { projectId, nodeId: selected.id });
    setNode(selected);
    setPicked(null);
    setStep("work");
  }

  function pickWork(selected: PickedWork) {
    setPicked(selected);
    setVolume("");
    setStep("volume");
  }

  async function send(geo: GeoState) {
    if (!user || !projectId || !node || !picked) return;
    setSending(true);
    setSaveError(null);
    const entry = newFactEntry({
      userId: user.id,
      projectId,
      label: { node: pathLabel(index, node), work: picked.work.name, unit: picked.work.unit },
      payload: {
        node_id: node.id,
        work_id: picked.work.id,
        schedule_item_id: picked.item?.id ?? null,
        volume: normalizeVolume(volume),
        work_date: workDate,
        latitude: geo.status === "ready" ? geo.latitude.toFixed(6) : null,
        longitude: geo.status === "ready" ? geo.longitude.toFixed(6) : null,
        accuracy_m: geo.status === "ready" ? geo.accuracy.toFixed(2) : null,
      },
      photos,
    });
    try {
      await enqueue(entry);
      setEntryId(entry.id);
      setStep("sent");
    } catch {
      setSaveError(t("saveFailed"));
    } finally {
      setSending(false);
    }
  }

  const titles: Record<Step | "project", string> = {
    project: t("steps.project"),
    node: t("steps.node"),
    work: t("steps.work"),
    volume: t("steps.volume"),
    evidence: t("steps.evidence"),
    sent: t("steps.sent"),
  };

  return (
    <section aria-labelledby="wizard-title" className="space-y-5" data-testid="wizard" data-step={current}>
      <header className="space-y-2">
        {current !== "sent" ? (
          <div className="flex items-center gap-2">
            {position > 0 || (current === "node" && hasProjectStep) ? (
              <Button
                type="button"
                variant="ghost"
                size="lg"
                className="-ml-2 h-12"
                onClick={back}
                data-testid="wizard-back"
              >
                <ArrowLeft data-icon="inline-start" className="size-5" aria-hidden />
                {t("back")}
              </Button>
            ) : null}
            <p className="ml-auto text-sm text-muted-foreground">
              {t("stepOf", { step: position + 1, total: order.length })}
            </p>
          </div>
        ) : null}
        <h1 id="wizard-title" className="text-2xl font-semibold tracking-tight">
          {titles[current]}
        </h1>
        {node && (current === "work" || current === "volume") ? (
          <p className="text-sm text-muted-foreground break-words">
            {[...ancestors(index, node), node].map((item) => item.name).join(" › ")}
            {picked && current !== "work" ? ` · ${picked.work.name}` : ""}
          </p>
        ) : null}
      </header>

      <QueryState
        loading={projects.isPending || (projectId !== null && tree.isPending)}
        error={projects.error ?? tree.error}
        onRetry={() => {
          void projects.refetch();
          void tree.refetch();
        }}
      >
        {current === "project" ? (
          list.length === 0 ? (
            <p className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
              {t("noProjects")}
            </p>
          ) : (
            <ul className="space-y-2">
              {list.map((project) => (
                <li key={project.id}>
                  <button
                    type="button"
                    data-testid="project-option"
                    onClick={() => setChosenProject(project.id)}
                    className="flex min-h-16 w-full items-center gap-3 rounded-xl border px-4 py-3 text-left text-lg font-medium outline-none active:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    <Building2 className="size-6 shrink-0 text-muted-foreground" aria-hidden />
                    {project.name}
                  </button>
                </li>
              ))}
            </ul>
          )
        ) : null}

        {current === "node" && user && projectId ? (
          <NodeStep index={index} userId={user.id} projectId={projectId} onPick={pickNode} />
        ) : null}
        {current === "work" && node && projectId ? (
          <WorkStep projectId={projectId} node={node} onPick={pickWork} />
        ) : null}
        {current === "volume" && picked ? (
          <VolumeStep
            unit={picked.work.unit}
            item={picked.item}
            volume={volume}
            workDate={workDate}
            onVolume={setVolume}
            onDate={setWorkDate}
            onNext={() => setStep("evidence")}
          />
        ) : null}
        {current === "evidence" && picked && node ? (
          <EvidenceStep
            summary={{
              node: pathLabel(index, node),
              work: picked.work.name,
              unit: picked.work.unit,
              volume: normalizeVolume(volume),
              workDate,
              unplanned: picked.item === null,
            }}
            photos={photos}
            onAdd={(added) => setPhotos((previous) => [...previous, ...added])}
            onRemove={(id) => setPhotos((previous) => previous.filter((photo) => photo.id !== id))}
            onSend={(geo) => void send(geo)}
            sending={sending}
            error={saveError}
          />
        ) : null}
        {current === "sent" && entryId ? (
          <Sent
            entryId={entryId}
            codeMessage={codeMessage}
            onAnother={() => reset(true)}
            onRecords={() => {
              reset(true);
              onOpenRecords();
            }}
          />
        ) : null}
      </QueryState>
    </section>
  );
}

// Итог отправки: пока запись в очереди устройства - прогресс; когда сервер её принял - «отправлено».
function Sent({
  entryId,
  codeMessage,
  onAnother,
  onRecords,
}: {
  entryId: string;
  codeMessage: (code: string) => string;
  onAnother: () => void;
  onRecords: () => void;
}) {
  const t = useTranslations("Field.sent");
  const outbox = useOutbox();
  const entry = outbox.entries.find((item) => item.id === entryId);
  const progress = outbox.progress[entryId];
  const percent = progress && progress.total > 0 ? Math.round((progress.loaded / progress.total) * 100) : 0;

  return (
    <div className="space-y-5" data-testid="sent" data-state={entry ? entry.stage : "done"}>
      {!entry ? (
        <div role="status" className="space-y-2 text-center">
          <CheckCircle2 className="mx-auto size-14 text-emerald-600" aria-hidden />
          <p className="text-xl font-semibold">{t("doneTitle")}</p>
          <p className="text-sm text-muted-foreground">{t("doneText")}</p>
        </div>
      ) : entry.stage === "failed" ? (
        <div role="alert" className="space-y-3">
          <p className="text-lg font-semibold text-destructive">{t("failedTitle")}</p>
          <p className="text-sm">{codeMessage(entry.error ?? "unknown")}</p>
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="h-14 w-full text-base"
            onClick={() => void retryEntry(entry.id)}
          >
            <RotateCw data-icon="inline-start" aria-hidden />
            {t("retry")}
          </Button>
        </div>
      ) : (
        <div role="status" className="space-y-3 text-center">
          <Loader2 className="mx-auto size-12 motion-safe:animate-spin" aria-hidden />
          <p className="text-xl font-semibold">
            {outbox.busy === entryId ? t("sending") : t("waiting")}
          </p>
          {progress ? (
            <p className="text-sm text-muted-foreground">
              {t("photoProgress", { photo: progress.photo, photos: progress.photos, percent })}
            </p>
          ) : null}
          <p className="text-sm text-muted-foreground">{t("safe")}</p>
        </div>
      )}
      <div className="grid gap-2">
        <Button type="button" size="lg" className="h-14 w-full text-base" onClick={onAnother} data-testid="another">
          {t("another")}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="lg"
          className="h-14 w-full text-base"
          onClick={onRecords}
          data-testid="to-records"
        >
          {t("records")}
        </Button>
      </div>
    </div>
  );
}
