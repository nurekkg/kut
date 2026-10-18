"use client";

import * as React from "react";

import { Input } from "@/components/ui/input";
import { dateFromIso, isoFromDate, maskDate } from "@/lib/date-input";

// Поле даты дд.мм.гггг с маской. Наружу уходит yyyy-mm-dd, пока дата неполная - пустая строка.
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

  return (
    <Input
      inputMode="numeric"
      autoComplete="off"
      placeholder="дд.мм.гггг"
      maxLength={10}
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
