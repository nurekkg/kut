// Поле даты дд.мм.гггг. Нативный <input type="date"> показывает формат языка браузера (mm/dd/yyyy в en-US),
// поэтому даты вводим текстом с маской. Модуль без зависимостей: его проверяют unit-тесты напрямую.

// Ввод: только цифры; точки ставятся сами после дня и месяца.
export function maskDate(input: string): string {
  const digits = input.replace(/\D/g, "").slice(0, 8);
  if (digits.length <= 2) return digits;
  if (digits.length <= 4) return `${digits.slice(0, 2)}.${digits.slice(2)}`;
  return `${digits.slice(0, 2)}.${digits.slice(2, 4)}.${digits.slice(4)}`;
}

// Дата дд.мм.гггг -> yyyy-mm-dd. Неполная или несуществующая дата (31.02) -> null.
export function isoFromDate(text: string): string | null {
  const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(text);
  if (!match) return null;
  const [, day, month, year] = match;
  const y = Number(year);
  const m = Number(month);
  const d = Number(day);
  const probe = new Date(Date.UTC(y, m - 1, d));
  if (probe.getUTCFullYear() !== y || probe.getUTCMonth() !== m - 1 || probe.getUTCDate() !== d) return null;
  return `${year}-${month}-${day}`;
}

// yyyy-mm-dd -> дд.мм.гггг. Пустое или неверное значение -> пустая строка.
export function dateFromIso(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : "";
}
