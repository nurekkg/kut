import { dayNumber, DAY_MS, monthLabel } from "./dates";
import type { ItemState } from "./types";

export const GANTT_SCALES = ["week", "month"] as const;
export type GanttScale = (typeof GANTT_SCALES)[number];

// Ширина суток в пикселях: на шкале «неделя» неделя занимает 70 px, на шкале «месяц» - около 90 px.
export const PX_PER_DAY: Record<GanttScale, number> = { week: 10, month: 3 };

// `clamped`: у части работ даты не поместились в окно шкалы и прижаты к его краю.
export type Domain = { startDay: number; endDay: number; days: number; clamped: boolean };

type Span = { start: string; end: string };

// Окно шкалы вокруг «сегодня». Сервер принимает любые даты ГПР (до 9999-12-31), а строка с датой из
// другого века растянула бы шкалу на миллионы суток и подвесила Гант у всех, кто его откроет.
export const WINDOW_YEARS = 10;
const WINDOW_DAYS = WINDOW_YEARS * 366;

// Границы шкалы: самая ранняя и самая поздняя дата плана и факта, запас по краям и «сегодня»; даты
// за окном вокруг «сегодня» прижимаются к его краю.
export function computeDomain(spans: Span[], today: string, before = 7, after = 21): Domain {
  const todayDay = dayNumber(today);
  const earliest = todayDay - WINDOW_DAYS;
  const latest = todayDay + WINDOW_DAYS;
  let first = todayDay;
  let last = todayDay;
  let clamped = false;
  for (const span of spans) {
    const start = dayNumber(span.start);
    const end = dayNumber(span.end);
    if (start < earliest || start > latest || end < earliest || end > latest) clamped = true;
    first = Math.min(first, Math.max(start, earliest));
    last = Math.max(last, Math.min(end, latest));
  }
  const startDay = first - before;
  const endDay = last + after;
  return { startDay, endDay, days: endDay - startDay + 1, clamped };
}

// Сутки за пределами шкалы упираются в её край (с запасом в одни сутки): полоса строки с датой из
// другого века доходит до края и не уходит за него на миллионы пикселей.
function clampDay(domain: Domain, day: number): number {
  return Math.min(Math.max(day, domain.startDay - 1), domain.endDay + 1);
}

export function xStart(domain: Domain, scale: GanttScale, iso: string): number {
  return (clampDay(domain, dayNumber(iso)) - domain.startDay) * PX_PER_DAY[scale];
}

// Правый край полосы: окончание включает последний день целиком.
export function xEnd(domain: Domain, scale: GanttScale, iso: string): number {
  return (clampDay(domain, dayNumber(iso) + 1) - domain.startDay) * PX_PER_DAY[scale];
}

export function xOfDay(domain: Domain, scale: GanttScale, day: number): number {
  return (clampDay(domain, day) - domain.startDay) * PX_PER_DAY[scale];
}

export type Tick = { x: number; label: string };

function dayOfMonth(day: number): number {
  return new Date(day * DAY_MS).getUTCDate();
}

// 1 января 1970 - четверг: (день + 4) % 7 даёт 0 для воскресенья и 1 для понедельника.
function isMonday(day: number): boolean {
  return (day + 4) % 7 === 1;
}

export function monthTicks(domain: Domain, scale: GanttScale): Tick[] {
  const ticks: Tick[] = [];
  for (let day = domain.startDay; day <= domain.endDay; day += 1) {
    if (dayOfMonth(day) === 1 || day === domain.startDay) {
      ticks.push({ x: xOfDay(domain, scale, day), label: monthLabel(day) });
    }
  }
  return ticks;
}

export function weekTicks(domain: Domain, scale: GanttScale): Tick[] {
  const ticks: Tick[] = [];
  for (let day = domain.startDay; day <= domain.endDay; day += 1) {
    if (isMonday(day)) ticks.push({ x: xOfDay(domain, scale, day), label: String(dayOfMonth(day)) });
  }
  return ticks;
}

// Стрелка «окончание - начало»: из конца предшественника в начало последователя. Если начало
// последователя правее, путь идёт «вправо - вертикаль - вправо»; если нет, обходит полосу снизу.
export function arrowPath(x1: number, y1: number, x2: number, y2: number): string {
  const gap = 8;
  const tail = 4;
  if (x2 >= x1 + 2 * gap) {
    const middle = x1 + gap;
    return `M${x1},${y1} H${middle} V${y2} H${x2 - tail}`;
  }
  const lane = y2 > y1 ? y1 + 12 : y1 - 12;
  return `M${x1},${y1} H${x1 + gap} V${lane} H${x2 - gap} V${y2} H${x2 - tail}`;
}

export const BAR_CLASSES: Record<ItemState, { plan: string; fact: string }> = {
  done: { plan: "fill-emerald-500/25 stroke-emerald-600", fact: "fill-emerald-600" },
  in_progress: { plan: "fill-sky-500/25 stroke-sky-600", fact: "fill-sky-600" },
  not_started: { plan: "fill-slate-400/25 stroke-slate-500", fact: "fill-slate-500" },
  late_start: { plan: "fill-amber-400/30 stroke-amber-600", fact: "fill-amber-600" },
  overdue: { plan: "fill-red-500/25 stroke-red-600", fact: "fill-red-600" },
  overdue_no_fact: { plan: "fill-red-500/20 stroke-red-600", fact: "fill-red-600" },
};
