const DATE_TIME = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "medium" });

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : DATE_TIME.format(date);
}

const DAY = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium" });

// Дата без времени ("2026-10-02"): разбираем вручную, чтобы часовой пояс не сдвинул день.
export function formatDate(value: string | null): string | null {
  if (!value) return null;
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return DAY.format(new Date(year, month - 1, day));
}

const MONEY = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });

export function formatAmount(value: string | null): string | null {
  if (value === null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? MONEY.format(number) : value;
}
