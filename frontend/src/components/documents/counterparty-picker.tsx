"use client";

import { useTranslations } from "next-intl";
import { useState, type KeyboardEvent } from "react";

import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { useDocumentCounterparties } from "@/lib/documents/queries";
import type { CounterpartyOut } from "@/lib/documents/types";

// Выбор контрагента: поиск по названию и список совпадений. Справочник отдаёт не больше 50 строк,
// поэтому при полном списке просим уточнить поиск. Выбранный контрагент хранит родитель и держит его в
// списке, даже если поиск его отсёк или пикер перемонтировался (смена вида в диалоге).
// Enter в поиске не отправляет форму: иначе документ создался бы с уже выбранным контрагентом.
export function CounterpartyPicker({
  id,
  selected,
  placeholder,
  label,
  required = false,
  testId,
  onChange,
}: {
  id: string;
  selected: CounterpartyOut | null;
  placeholder: string;
  label: string;
  required?: boolean;
  testId: string;
  onChange: (item: CounterpartyOut | null) => void;
}) {
  const t = useTranslations("Documents.common");
  const [search, setSearch] = useState("");
  const found = useDocumentCounterparties(search.trim());
  const items = found.data ?? [];
  const options =
    selected !== null && !items.some((item) => item.id === selected.id) ? [selected, ...items] : items;

  function swallowEnter(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") event.preventDefault();
  }

  return (
    <div className="grid min-w-0 gap-1.5">
      <Input
        type="search"
        aria-label={`${label}: ${t("counterpartySearch")}`}
        placeholder={t("counterpartySearch")}
        maxLength={64}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        onKeyDown={swallowEnter}
        data-testid={`${testId}-search`}
      />
      <NativeSelect
        id={id}
        aria-label={label}
        value={selected?.id ?? ""}
        required={required}
        onChange={(event) => {
          const next = event.target.value;
          onChange(next === "" ? null : (options.find((item) => item.id === next) ?? null));
        }}
        data-testid={testId}
      >
        <option value="">{placeholder}</option>
        {options.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name}
          </option>
        ))}
      </NativeSelect>
      {items.length >= 50 ? <p className="text-xs text-muted-foreground">{t("counterpartyMore")}</p> : null}
    </div>
  );
}
