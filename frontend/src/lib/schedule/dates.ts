export const DAY_MS = 86_400_000;

const BISHKEK_DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Bishkek",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

// «Сегодня» сервер считает по Бишкеку (факт не может быть датирован будущим днём): телефон с другим
// часовым поясом не должен давать другую дату.
export function todayIso(): string {
  return BISHKEK_DAY.format(new Date());
}

// Дата без времени ("2026-10-09") как номер дня от эпохи: границы дня не зависят от пояса устройства.
export function dayNumber(iso: string): number {
  const [year, month, day] = iso.split("-").map(Number);
  return Math.round(Date.UTC(year, month - 1, day) / DAY_MS);
}

export function isoFromDay(day: number): string {
  return new Date(day * DAY_MS).toISOString().slice(0, 10);
}

export function addDays(iso: string, days: number): string {
  return isoFromDay(dayNumber(iso) + days);
}

const SHORT_DATE = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "2-digit",
});

export function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [year, month, day] = iso.split("-").map(Number);
  if (!year || !month || !day) return iso;
  return SHORT_DATE.format(new Date(year, month - 1, day));
}

const TERM_DATE = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });

// Срок техники (документы, ТО, назначения): год из четырёх цифр. Двузначный год (31.12.40) путал сроки.
export function formatTermDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [year, month, day] = iso.split("-").map(Number);
  if (!year || !month || !day) return iso;
  return TERM_DATE.format(new Date(year, month - 1, day));
}

const MONTH = new Intl.DateTimeFormat("ru-RU", { month: "short", timeZone: "UTC" });

export function monthLabel(day: number): string {
  const date = new Date(day * DAY_MS);
  return `${MONTH.format(date).replace(".", "")} ${date.getUTCFullYear()}`;
}
