const VOLUME = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
const PERCENT = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
const DAYS = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1, signDisplay: "exceptZero" });

export function toNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function formatVolume(value: string | number | null | undefined): string {
  return VOLUME.format(toNumber(value));
}

export function formatPercent(value: string | number | null | undefined): string {
  return `${PERCENT.format(toNumber(value))}%`;
}

// Отставание в днях: плюс - отстаём, минус - опережаем (как отдаёт сервер).
export function formatDelay(value: string | number | null | undefined): string {
  return DAYS.format(toNumber(value));
}

// Доля 0-100 для ширины полосы: вне диапазона не выходит.
export function clampPercent(value: string | number | null | undefined): number {
  return Math.min(100, Math.max(0, toNumber(value)));
}
