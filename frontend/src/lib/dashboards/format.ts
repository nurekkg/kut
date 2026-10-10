import { formatAmount } from "@/lib/format";
import { toNumber } from "@/lib/schedule/format";

export const DASH = "—";

type Value = string | number | null | undefined;

const ONE_DECIMAL = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
  signDisplay: "negative",
});
const SIGNED_ONE_DECIMAL = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
  signDisplay: "exceptZero",
});
const WHOLE = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0, signDisplay: "negative" });
const CURRENCY: Record<string, string> = { KGS: "сом" };

function isPresent(value: Value): value is string | number {
  return value !== null && value !== undefined && value !== "";
}

export function formatPercent(value: Value): string {
  return isPresent(value) ? `${ONE_DECIMAL.format(toNumber(value))} %` : DASH;
}

// Значение из графика приходит числом или null (факта после сегодня нет).
export function formatChartPercent(value: unknown): string {
  return formatPercent(typeof value === "number" || typeof value === "string" ? value : null);
}

// Отклонение в процентных пунктах: минус - отставание, плюс - опережение.
export function formatDeviation(value: Value): string {
  return isPresent(value) ? `${SIGNED_ONE_DECIMAL.format(toNumber(value))} п.п.` : DASH;
}

export function formatDays(value: Value): string {
  return isPresent(value) ? `${WHOLE.format(toNumber(value))} дн.` : DASH;
}

export function formatMoney(amount: string, currency: string): string {
  return `${formatAmount(amount) ?? DASH} ${CURRENCY[currency] ?? currency}`;
}

// Дата без времени разбираем по частям: часовой пояс устройства не сдвигает день.
export function formatDay(iso: string | null): string {
  if (!iso) return DASH;
  const [year, month, day] = iso.slice(0, 10).split("-");
  return `${day}.${month}.${year}`;
}

export function formatDayMonth(iso: string): string {
  const [, month, day] = iso.split("-");
  return `${day}.${month}`;
}
