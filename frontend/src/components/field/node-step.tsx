"use client";

import { Search } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { NodeIcon } from "@/components/projects/node-icon";
import { useDebounced } from "@/components/settings/shared";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { readRecents } from "@/lib/field/recents";
import { ancestors, search, type TreeIndex } from "@/lib/projects/tree";
import type { TreeNode } from "@/lib/projects/types";

const SHOWN = 30;

function NodeButton({
  node,
  index,
  onPick,
}: {
  node: TreeNode;
  index: TreeIndex;
  onPick: (node: TreeNode) => void;
}) {
  const path = ancestors(index, node)
    .map((item) => item.name)
    .join(" › ");
  return (
    <li>
      <button
        type="button"
        data-testid="node-option"
        onClick={() => onPick(node)}
        className="flex min-h-14 w-full items-center gap-3 rounded-xl border px-4 py-2 text-left outline-none active:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <NodeIcon type={node.type} className="size-5 shrink-0 text-muted-foreground" />
        <span className="min-w-0">
          <span className="block font-medium break-words">
            {node.name}
            {node.code ? <span className="text-muted-foreground"> · {node.code}</span> : null}
          </span>
          {path ? (
            <span className="block text-xs text-muted-foreground break-words">{path}</span>
          ) : null}
        </span>
      </button>
    </li>
  );
}

// Шаг «узел»: поиск по названию и коду, недавние узлы и узлы, где человек назначен мастером или
// прорабом. Дерево уже у клиента, поэтому поиск мгновенный и не требует связи.
export function NodeStep({
  index,
  userId,
  projectId,
  onPick,
}: {
  index: TreeIndex;
  userId: string;
  projectId: string;
  onPick: (node: TreeNode) => void;
}) {
  const t = useTranslations("Field.node");
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query, 200);
  const searching = debounced.trim() !== "";

  const found = useMemo(() => search(index, debounced), [index, debounced]);
  const results = useMemo(
    () =>
      [...found.matches]
        .slice(0, SHOWN)
        .map((id) => index.byId.get(id))
        .filter((node): node is TreeNode => node !== undefined),
    [found, index],
  );
  const recents = useMemo(
    () =>
      readRecents(userId)
        .filter((item) => item.projectId === projectId)
        .map((item) => index.byId.get(item.nodeId))
        .filter((node): node is TreeNode => node !== undefined),
    [index, userId, projectId],
  );
  const mine = useMemo(() => {
    const own: TreeNode[] = [];
    for (const node of index.byId.values()) {
      if (node.master_user_id === userId || node.foreman_user_id === userId) own.push(node);
      if (own.length >= 12) break;
    }
    return own;
  }, [index, userId]);

  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <Label htmlFor="field-node-search" className="text-base">
          {t("searchLabel")}
        </Label>
        <div className="relative">
          <Search
            className="pointer-events-none absolute top-1/2 left-3 size-5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            id="field-node-search"
            type="search"
            enterKeyHint="search"
            autoComplete="off"
            className="h-14 pl-10 text-base"
            placeholder={t("searchPlaceholder")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>

      {searching ? (
        <section aria-label={t("results")} className="space-y-2">
          {results.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("nothingFound")}</p>
          ) : (
            <ul className="space-y-2">
              {results.map((node) => (
                <NodeButton key={node.id} node={node} index={index} onPick={onPick} />
              ))}
            </ul>
          )}
          {found.truncated || found.matches.size > SHOWN ? (
            <p className="text-xs text-muted-foreground">{t("refine", { count: SHOWN })}</p>
          ) : null}
        </section>
      ) : (
        <>
          {recents.length > 0 ? (
            <section aria-labelledby="field-recent" className="space-y-2">
              <h2 id="field-recent" className="text-sm font-semibold">
                {t("recent")}
              </h2>
              <ul className="space-y-2" data-testid="recent-nodes">
                {recents.map((node) => (
                  <NodeButton key={node.id} node={node} index={index} onPick={onPick} />
                ))}
              </ul>
            </section>
          ) : null}
          {mine.length > 0 ? (
            <section aria-labelledby="field-mine" className="space-y-2">
              <h2 id="field-mine" className="text-sm font-semibold">
                {t("mine")}
              </h2>
              <ul className="space-y-2">
                {mine.map((node) => (
                  <NodeButton key={node.id} node={node} index={index} onPick={onPick} />
                ))}
              </ul>
            </section>
          ) : null}
          {recents.length === 0 && mine.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("hint")}</p>
          ) : null}
        </>
      )}
    </div>
  );
}
