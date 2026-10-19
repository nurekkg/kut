"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { RouteRejectDialog, SignedScanDialog } from "@/components/documents/route-dialogs";
import { useDocumentErrorMessage } from "@/components/documents/shared";
import { Section } from "@/components/equipment/section";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { newIdempotencyKey } from "@/lib/api";
import { canWriteKind } from "@/lib/documents/access";
import { formatMoment } from "@/lib/documents/format";
import { isRouteRoleCode } from "@/lib/documents/route";
import { DOCUMENTS_KEY, documentsApi, useVersionRoute } from "@/lib/documents/queries";
import type { DocumentOut, RouteStep, VersionOut } from "@/lib/documents/types";
import { cn } from "@/lib/utils";

// Маршрут версии (ADR-015 часть 5): шаги последней попытки, кто и когда решил, решения и причины. Кнопки - по
// праву и по состоянию: отправить - черновик, решить - шаг мой (сервер проверит тот же шаг, автора и замещение),
// подписанный скан - согласованная версия. Причины и имена отдаёт сервер; здесь показываем то, что пришло.
export function RouteBlock({
  doc,
  version,
  permissions,
}: {
  doc: DocumentOut;
  version: VersionOut;
  permissions: string[];
}) {
  const t = useTranslations("Documents.route");
  const roles = useTranslations("Roles");
  const message = useDocumentErrorMessage();
  const queryClient = useQueryClient();
  const route = useVersionRoute(doc.id, version.id, true);
  const [rejecting, setRejecting] = useState(false);
  const [scanning, setScanning] = useState(false);

  const writable = canWriteKind(doc.kind, permissions) && doc.archived_at === null;
  const position = route.data?.current_position ?? null;
  const canDecide = route.data?.can_decide === true && version.status === "on_approval" && position !== null;

  const send = useMutation({
    mutationFn: () => documentsApi.sendRoute(doc.id, version.id, newIdempotencyKey()),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY }),
  });
  const approve = useMutation({
    mutationFn: (current: number) =>
      documentsApi.decide(doc.id, version.id, { position: current, decision: "approve" }, newIdempotencyKey()),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY }),
  });

  const roleLabel = (code: string) => (isRouteRoleCode(code) ? roles(code) : code);
  const steps = route.data?.steps ?? [];
  const lastAttempt = steps.reduce((max, step) => Math.max(max, step.attempt), 0);
  const lastSteps = steps.filter((step) => step.attempt === lastAttempt);
  const decisions = route.data?.decisions ?? [];
  const currentDecisions = decisions.filter((decision) => decision.attempt === lastAttempt);
  const pastDecisions = decisions.filter((decision) => decision.attempt !== lastAttempt);
  const reasonOf = (step: RouteStep): string | null => {
    const matches = currentDecisions.filter((decision) => decision.position === step.position);
    return matches.length > 0 ? matches[matches.length - 1].reason : null;
  };
  const failure = send.error ?? approve.error;
  const describe = (step: RouteStep): string | null => {
    if (!step.decided_by_name) return null;
    const who = step.on_behalf_of_name
      ? `${t("decidedBy", { name: step.decided_by_name })} ${t("onBehalf", { name: step.on_behalf_of_name })}`
      : t("decidedBy", { name: step.decided_by_name });
    return step.decided_at ? `${who} · ${formatMoment(step.decided_at)}` : who;
  };
  const finished = version.status === "approved" || version.status === "signed";

  return (
    <Section id="document-route-title" title={t("title")}>
      {route.isPending ? <p className="text-sm text-muted-foreground">…</p> : null}
      {route.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(route.error)}
        </p>
      ) : null}
      {route.isSuccess ? (
        <div className="flex flex-col gap-3" data-testid="document-route">
          <p className="text-sm text-muted-foreground" data-testid="document-route-status">
            {route.data.attempts > 0 ? t("attempt", { number: route.data.attempts }) : t("noRoute")}
            {position !== null && lastSteps.length > 0 ? (
              <span className="ml-2 text-foreground">
                {t("current", { role: roleLabel(lastSteps.find((step) => step.position === position)?.role_code ?? "") })}
              </span>
            ) : null}
            {finished && route.data.attempts > 0 ? <span className="ml-2 text-foreground">{t("finished")}</span> : null}
            {route.data.source_version_no !== null && route.data.source_version_no !== version.version_no ? (
              <span className="ml-2 text-foreground" data-testid="route-source-version">
                {t("sourceVersion", { number: route.data.source_version_no })}
              </span>
            ) : null}
          </p>

          {lastSteps.length > 0 ? (
            <ol className="flex flex-col gap-2" data-testid="route-steps">
              {lastSteps.map((step) => (
                <StepRow
                  key={`${step.attempt}-${step.position}`}
                  step={step}
                  current={step.position === position}
                  label={roleLabel(step.role_code)}
                  statusLabel={t(`status.${step.status}`)}
                  decidedText={describe(step)}
                  reasonText={reasonOf(step) !== null ? t("reason", { reason: reasonOf(step) ?? "" }) : null}
                />
              ))}
            </ol>
          ) : null}

          {decisions.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("noDecisions")}</p>
          ) : null}
          {pastDecisions.length > 0 ? (
            <div className="flex flex-col gap-2" data-testid="route-past-attempts">
              <h3 className="text-sm font-medium">{t("pastAttempts")}</h3>
              <ul className="flex flex-col divide-y rounded-lg border text-sm" data-testid="route-decisions">
              {pastDecisions.map((decision) => (
                <li key={decision.id} className="flex flex-col gap-1 p-3" data-testid="route-decision">
                  <span className="font-medium">
                    {t("decisionLine", {
                      verb: decision.decision === "approve" ? t("verbApproved") : t("verbRejected"),
                      name: decision.actor_name ?? "",
                      role: roleLabel(decision.role_code),
                      time: formatMoment(decision.decided_at),
                    })}
                    {decision.on_behalf_of_name ? ` · ${t("onBehalf", { name: decision.on_behalf_of_name })}` : ""}
                  </span>
                  {decision.reason !== null ? (
                    <span className="break-words text-muted-foreground" data-testid="route-decision-reason">
                      {t("reason", { reason: decision.reason })}
                    </span>
                  ) : null}
                </li>
              ))}
              </ul>
            </div>
          ) : null}

          {failure ? (
            <p role="alert" className="text-sm text-destructive" data-testid="route-error">
              {message(failure)}
            </p>
          ) : null}

          {version.status === "draft" && writable ? (
            <Button
              type="button"
              size="lg"
              className="h-12 w-full sm:w-auto"
              disabled={send.isPending}
              onClick={() => send.mutate()}
              data-testid="route-send"
            >
              {t("send")}
            </Button>
          ) : null}
          {canDecide ? (
            <div className="grid grid-cols-1 gap-2 sm:flex sm:flex-wrap">
              <Button
                type="button"
                size="lg"
                className="h-12"
                disabled={approve.isPending}
                onClick={() => {
                  if (position !== null) approve.mutate(position);
                }}
                data-testid="route-approve"
              >
                {t("approve")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="lg"
                className="h-12"
                disabled={approve.isPending}
                onClick={() => setRejecting(true)}
                data-testid="route-reject-open"
              >
                {t("reject")}
              </Button>
            </div>
          ) : null}
          {version.status === "approved" && writable && permissions.includes("documents:approve") ? (
            <Button
              type="button"
              variant="outline"
              size="lg"
              className="h-12 w-full sm:w-auto"
              onClick={() => setScanning(true)}
              data-testid="route-scan-open"
            >
              {t("scan")}
            </Button>
          ) : null}
        </div>
      ) : null}

      {rejecting && position !== null ? (
        <RouteRejectDialog
          documentId={doc.id}
          versionId={version.id}
          position={position}
          financial={doc.financial}
          onClose={() => setRejecting(false)}
        />
      ) : null}
      {scanning ? (
        <SignedScanDialog documentId={doc.id} versionId={version.id} onClose={() => setScanning(false)} />
      ) : null}
    </Section>
  );
}

function StepRow({
  step,
  current,
  label,
  statusLabel,
  decidedText,
  reasonText,
}: {
  step: RouteStep;
  current: boolean;
  label: string;
  statusLabel: string;
  decidedText: string | null;
  reasonText: string | null;
}) {
  return (
    <li
      className={cn(
        "flex flex-col gap-1 rounded-lg border p-3 text-sm sm:flex-row sm:items-center sm:justify-between",
        current && "border-primary bg-primary/5",
      )}
      data-testid="route-step"
      data-status={step.status}
      aria-current={current ? "step" : undefined}
    >
      <span className="font-medium">
        {step.position}. {label}
      </span>
      <span className="flex flex-wrap items-center gap-2 text-muted-foreground">
        <Badge variant="outline" className="h-6 px-2 text-foreground">
          {statusLabel}
        </Badge>
        {decidedText ? <span className="tabular-nums">{decidedText}</span> : null}
      </span>
      {reasonText ? (
        <span className="basis-full break-words text-muted-foreground" data-testid="route-step-reason">
          {reasonText}
        </span>
      ) : null}
    </li>
  );
}
