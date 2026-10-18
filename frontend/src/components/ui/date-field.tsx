"use client";

import * as React from "react";

import { Input } from "@/components/ui/input";
import { dateFromIso, isoFromDate, maskDate } from "@/lib/date-input";

// Поле даты дд.мм.гггг с маской. Наружу уходит yyyy-mm-dd, пока дата неполная - пустая строка.
// Полная, но несуществующая дата (31.02) помечается как неверная: фильтр при этом не применяется.
function DateField({
  value,
  onChange,
  ...props
}: Omit<React.ComponentProps<typeof Input>, "value" | "onChange" | "type"> & {
  value: string;
  onChange: (iso: string) => void;
}) {
  const [text, setText] = React.useState(() => dateFromIso(value));
  // Текст - ввод пользователя. Если значение пришло извне (сброс фильтра) и с вводом не совпадает,
  // показываем его: эффект для синхронизации не нужен.
  const shown = (isoFromDate(text) ?? "") === value ? text : dateFromIso(value);
  const invalid = text.length === 10 && isoFromDate(text) === null;

  return (
    <Input
      inputMode="numeric"
      autoComplete="off"
      maxLength={10}
      aria-invalid={invalid || undefined}
      {...props}
      value={shown}
      onChange={(event) => {
        const next = maskDate(event.target.value);
        setText(next);
        onChange(isoFromDate(next) ?? "");
      }}
    />
  );
}

export { DateField };
