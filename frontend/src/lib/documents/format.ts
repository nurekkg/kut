const AMOUNT = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// Сумма приходит строкой-десятичным ("1234.50"); в интерфейсе - "1 234,50 сом". Валюта одна - сом (KGS),
// поэтому суффикс добавляет один хелпер на всех экранах документов и в выгрузке.
export function formatMoney(value: string): string {
  const number = Number(value);
  return Number.isFinite(number) ? `${AMOUNT.format(number)} сом` : value;
}

// Ввод суммы: пробелы и запятая допустимы, до двух знаков после разделителя. Иначе null.
export function parseAmount(input: string): string | null {
  const text = input.replace(/\s/g, "").replace(",", ".");
  return /^\d{1,12}(\.\d{1,2})?$/.test(text) ? text : null;
}

// Количество позиции: пробелы и запятая допустимы, до четырёх знаков; сервер принимает только точку.
export function normalizeQuantity(input: string): string {
  return input.replace(/\s/g, "").replace(",", ".");
}

const QUANTITY = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 4 });

// Количество с сервера - "2.5000"; показываем без лишних нулей: "2,5".
export function formatQuantity(value: string): string {
  const number = Number(value);
  return Number.isFinite(number) ? QUANTITY.format(number) : value;
}

export function parseQuantity(input: string): string | null {
  const text = normalizeQuantity(input);
  return /^\d{1,14}(\.\d{1,4})?$/.test(text) && Number(text) > 0 ? text : null;
}

const DAY = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Asia/Bishkek",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

const MOMENT = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Asia/Bishkek",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

// Сервер считает даты по Бишкеку, поэтому и показываем их по Бишкеку, а не по поясу устройства.
export function formatDay(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : DAY.format(date);
}

export function formatMoment(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : MOMENT.format(date);
}

const MEGABYTES = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export function formatMegabytes(bytes: number): string {
  return MEGABYTES.format(Math.max(bytes / (1024 * 1024), 0.1));
}
