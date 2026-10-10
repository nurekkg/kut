"use client";

import { X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState, type ReactNode } from "react";

import { useDebounced } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Props<T> = {
  id: string;
  label: string;
  placeholder: string;
  selected: T | null;
  onSelect: (item: T | null) => void;
  // Поиск делает вызывающий (по уже загруженным данным) и возвращает не больше нескольких десятков совпадений.
  search: (query: string) => T[];
  keyOf: (item: T) => string;
  render: (item: T) => ReactNode;
  emptyText: string;
  hint?: string;
};

// Выбор одного значения из большого списка поиском (узел, вид работ, строка ГПР): на тысячах
// значений обычный список не нужен, достаточно нескольких первых совпадений.
export function SearchPicker<T>({
  id,
  label,
  placeholder,
  selected,
  onSelect,
  search,
  keyOf,
  render,
  emptyText,
  hint,
}: Props<T>) {
  const t = useTranslations("Schedule.picker");
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query.trim(), 200);
  const results = useMemo(() => (selected ? [] : search(debounced)), [selected, search, debounced]);

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {selected ? (
        <div className="flex min-h-10 items-center gap-2 rounded-lg border bg-muted/40 px-2.5 py-1.5 text-sm">
          <div className="min-w-0 flex-1">{render(selected)}</div>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t("clear", { name: label })}
            onClick={() => {
              onSelect(null);
              setQuery("");
            }}
          >
            <X aria-hidden />
          </Button>
        </div>
      ) : (
        <>
          <Input
            id={id}
            type="search"
            autoComplete="off"
            className="h-10 md:h-9"
            placeholder={placeholder}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            // Enter в поиске не должен отправлять форму, в которую встроен выбор.
            onKeyDown={(event) => {
              if (event.key === "Enter") event.preventDefault();
            }}
            aria-describedby={hint ? `${id}-hint` : undefined}
          />
          <ul
            className="max-h-44 divide-y overflow-y-auto rounded-lg border text-sm empty:hidden"
            aria-label={t("results", { name: label })}
          >
            {results.map((item) => (
              <li key={keyOf(item)}>
                <button
                  type="button"
                  className="flex min-h-10 w-full items-center px-2.5 py-1.5 text-left outline-none hover:bg-muted focus-visible:bg-muted"
                  onClick={() => onSelect(item)}
                >
                  <span className="min-w-0 flex-1">{render(item)}</span>
                </button>
              </li>
            ))}
          </ul>
          {debounced !== "" && results.length === 0 ? (
            <p className="text-sm text-muted-foreground">{emptyText}</p>
          ) : null}
        </>
      )}
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
