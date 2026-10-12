import { addDays, dayNumber } from "../schedule/dates";

import type { CalendarAssignment, CalendarDay, CalendarMaintenance } from "./types";

// Календарь: ±4 недели от сегодняшнего дня, окно сдвигается по неделям.
export const CALENDAR_HALF_DAYS = 28;
export const CALENDAR_DAYS = CALENDAR_HALF_DAYS * 2 + 1;
export const CELL_WIDTH = 40;
export const LANE_HEIGHT = 22;
export const LANES = 2;
export const CALENDAR_ROW_HEIGHT = LANE_HEIGHT * LANES + 12;
export const CALENDAR_HEADER_HEIGHT = 44;

export function calendarWindow(today: string, weekShift: number): { from: string; to: string } {
  const from = addDays(today, -CALENDAR_HALF_DAYS + weekShift * 7);
  return { from, to: addDays(from, CALENDAR_DAYS - 1) };
}

export type DayColumn = { iso: string; day: number; weekday: number; weekend: boolean; today: boolean };

export function dayColumns(from: string, today: string): DayColumn[] {
  return Array.from({ length: CALENDAR_DAYS }, (_, index) => {
    const iso = addDays(from, index);
    const weekday = (((dayNumber(iso) + 4) % 7) + 7) % 7;
    return {
      iso,
      day: Number(iso.slice(8, 10)),
      weekday,
      weekend: weekday === 0 || weekday === 6,
      today: iso === today,
    };
  });
}

export type HoursLevel = "none" | "partial" | "full" | "over";

// Часы за день: 24 - полный день; больше 24 на сервере не сохраняется, но подсветка нужна.
export function hoursLevel(hours: number): HoursLevel {
  if (hours <= 0) return "none";
  if (hours < 24) return "partial";
  if (hours === 24) return "full";
  return "over";
}

export function hoursByDay(days: CalendarDay[]): Map<string, number> {
  return new Map(days.map((item) => [item.date, Number(item.hours)]));
}

export type Bar = {
  key: string;
  kind: "assignment" | "repair" | "to";
  label: string;
  start: number;
  end: number;
  lane: number;
};

export type BarLayout = { bars: Bar[]; hidden: number };

// Полоски назначений и ТО в дорожках: одна строка календаря - до LANES видимых дорожек.
// Жадная раскладка: полоска встаёт в первую дорожку, где предыдущая уже закончилась.
export function layoutBars(
  from: string,
  to: string,
  assignments: CalendarAssignment[],
  maintenance: CalendarMaintenance[],
  labels: { assignment: (item: CalendarAssignment) => string; maintenance: (item: CalendarMaintenance) => string },
): BarLayout {
  const windowStart = dayNumber(from);
  const windowEnd = dayNumber(to);
  const candidates: Omit<Bar, "lane">[] = [];
  for (const item of assignments) {
    const start = Math.max(dayNumber(item.date_from), windowStart);
    const end = Math.min(dayNumber(item.date_to), windowEnd);
    if (start > end) continue;
    candidates.push({
      key: `a-${item.id}`,
      kind: "assignment",
      label: labels.assignment(item),
      start: start - windowStart,
      end: end - windowStart,
    });
  }
  for (const item of maintenance) {
    const start = Math.max(dayNumber(item.planned_from), windowStart);
    const plannedTo = item.planned_to ?? item.planned_from;
    const end = Math.min(dayNumber(plannedTo), windowEnd);
    if (start > end) continue;
    candidates.push({
      key: `m-${item.id}`,
      kind: item.kind === "repair" ? "repair" : "to",
      label: labels.maintenance(item),
      start: start - windowStart,
      end: end - windowStart,
    });
  }
  candidates.sort((a, b) => a.start - b.start || a.end - b.end || a.key.localeCompare(b.key));
  const laneEnds: number[] = [];
  const bars: Bar[] = [];
  let hidden = 0;
  for (const candidate of candidates) {
    const lane = laneEnds.findIndex((end) => end < candidate.start);
    const placed = lane === -1 ? laneEnds.length : lane;
    if (placed >= LANES) {
      hidden += 1;
      continue;
    }
    laneEnds[placed] = candidate.end;
    bars.push({ ...candidate, lane: placed });
  }
  return { bars, hidden };
}
