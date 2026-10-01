const DATE_TIME = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "medium" });

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : DATE_TIME.format(date);
}
