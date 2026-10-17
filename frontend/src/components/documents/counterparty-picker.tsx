"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { useDocumentCounterparties } from "@/lib/documents/queries";
import type { CounterpartyOut } from "@/lib/documents/types";

// Выбор контрагента: поиск по названию и список совпадений. Справочник отдаёт не больше 50 строк,
// поэтому при полном списке просим уточнить поиск. Выбранный контрагент остаётся в списке, даже если
// поиск его отсёк.
export function CounterpartyPicker({
  id,
  value,
  placeholder,
  label,
  required = false,
  testId,
  onChange,
}: {
  id: string;
  value: string;
  placeholder: string;
  label: string;
  required?: boolean;
  testId: string;
  onChange: (id: string) => void;
}) {
  const t = useTranslations("Documents.common");
  const [search, setSearch] = useState("");
  const [chosen, setChosen] = useState<CounterpartyOut | null>(null);
  const found = useDocumentCounterparties(search.trim());
  const items = found.data ?? [];
  const options =
    chosen !== null && !items.some((item) => item.id === chosen.id) ? [chosen, ...items] : items;

  return (
    <div className="grid min-w-0 gap-1.5">
      <Input
        type="search"
        aria-label={`${label}: ${t("counterpartySearch")}`}
        placeholder={t("counterpartySearch")}
        maxLength={64}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        data-testid={`${testId}-search`}
      />
      <NativeSelect
        id={id}
        aria-label={label}
        value={value}
        required={required}
        onChange={(event) => {
          const next = event.target.value;
          setChosen(items.find((item) => item.id === next) ?? null);
          onChange(next);
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
