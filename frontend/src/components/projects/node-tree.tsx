"use client";

import { ChevronDown, ChevronRight, Plus, Search } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState, type KeyboardEvent } from "react";

import { NodeIcon } from "@/components/projects/node-icon";
import { useDebounced } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MAX_SEARCH_MATCHES, search, visibleRows, type TreeIndex } from "@/lib/projects/tree";
import { cn } from "@/lib/utils";

type Props = {
  index: TreeIndex;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAddRoot?: () => void;
};

export function NodeTree({ index, selectedId, onSelect, onAddRoot }: Props) {
  const t = useTranslations("Workspace.tree");
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query, 250);
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set((index.children.get(null) ?? []).map((node) => node.id)),
  );
  const [focusId, setFocusId] = useState<string | null>(null);

  // Выбранный узел (например, по ссылке) всегда виден: при смене выбора раскрываем его предков.
  // Это пересчёт состояния от свойства прямо при отрисовке, а не эффект.
  const [seenSelected, setSeenSelected] = useState<string | null>(null);
  if (selectedId !== seenSelected) {
    setSeenSelected(selectedId);
    const node = selectedId ? index.byId.get(selectedId) : undefined;
    if (node) {
      const missing = node.path.slice(0, -1).filter((id) => !expanded.has(id));
      if (missing.length > 0) setExpanded(new Set([...expanded, ...missing]));
    }
  }

  const found = useMemo(() => search(index, debounced), [index, debounced]);
  const searching = debounced.trim() !== "";
  const rows = useMemo(
    () => visibleRows(index, expanded, searching ? found.shown : null),
    [index, expanded, searching, found.shown],
  );

  const tabbable = focusId ?? selectedId ?? rows[0]?.node.id ?? null;

  function toggle(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function focusRow(id: string) {
    setFocusId(id);
    document.getElementById(`node-row-${id}`)?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>, position: number) {
    const row = rows[position];
    if (!row) return;
    if (event.key === "ArrowDown" && rows[position + 1]) {
      event.preventDefault();
      focusRow(rows[position + 1].node.id);
    } else if (event.key === "ArrowUp" && rows[position - 1]) {
      event.preventDefault();
      focusRow(rows[position - 1].node.id);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      if (row.hasChildren && !row.expanded) toggle(row.node.id);
      else if (row.hasChildren && rows[position + 1]) focusRow(rows[position + 1].node.id);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (row.hasChildren && row.expanded && !searching) toggle(row.node.id);
      else if (row.node.parent_id) focusRow(row.node.parent_id);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="space-y-1.5">
        <Label htmlFor="tree-search" className="sr-only">
          {t("search")}
        </Label>
        <div className="relative">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            id="tree-search"
            type="search"
            className="h-10 pl-8 md:h-9"
            placeholder={t("searchPlaceholder")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>
      {searching && found.truncated ? (
        <p className="text-xs text-muted-foreground">{t("truncated", { count: MAX_SEARCH_MATCHES })}</p>
      ) : null}
      <div
        role="tree"
        aria-label={t("label")}
        className="min-h-0 flex-1 space-y-0.5 overflow-y-auto overscroll-contain pr-1"
      >
        {rows.length === 0 ? (
          <p className="px-1 py-3 text-sm text-muted-foreground">
            {searching ? t("emptySearch") : t("empty")}
          </p>
        ) : null}
        {rows.map((row, position) => {
          const selected = row.node.id === selectedId;
          return (
            <div
              key={row.node.id}
              role="treeitem"
              aria-level={row.depth + 1}
              aria-selected={selected}
              aria-expanded={row.hasChildren ? row.expanded : undefined}
              className={cn(
                "flex min-h-11 items-center rounded-lg md:min-h-9",
                selected ? "bg-muted font-medium" : "hover:bg-muted/60",
              )}
              style={{ paddingLeft: `${row.depth * 14}px` }}
              onKeyDown={(event) => onKeyDown(event, position)}
            >
              {row.hasChildren ? (
                <button
                  type="button"
                  tabIndex={-1}
                  className="flex size-9 shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 md:size-8"
                  aria-label={row.expanded ? t("collapse") : t("expand")}
                  onClick={() => toggle(row.node.id)}
                  disabled={searching}
                >
                  {row.expanded ? (
                    <ChevronDown className="size-4" aria-hidden />
                  ) : (
                    <ChevronRight className="size-4" aria-hidden />
                  )}
                </button>
              ) : (
                <span className="size-9 shrink-0 md:size-8" aria-hidden />
              )}
              <button
                type="button"
                id={`node-row-${row.node.id}`}
                tabIndex={row.node.id === tabbable ? 0 : -1}
                className="flex min-w-0 flex-1 items-center gap-2 rounded-md py-1.5 pr-2 text-left text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                onClick={() => {
                  setFocusId(row.node.id);
                  onSelect(row.node.id);
                }}
                onFocus={() => setFocusId(row.node.id)}
              >
                <NodeIcon type={row.node.type} className="size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 truncate">{row.node.name}</span>
                {row.node.code ? (
                  <span className="shrink-0 text-xs text-muted-foreground">{row.node.code}</span>
                ) : null}
              </button>
            </div>
          );
        })}
      </div>
      {onAddRoot ? (
        <Button type="button" variant="outline" size="lg" className="h-10" onClick={onAddRoot}>
          <Plus data-icon="inline-start" aria-hidden />
          {t("addRoot")}
        </Button>
      ) : null}
    </div>
  );
}
