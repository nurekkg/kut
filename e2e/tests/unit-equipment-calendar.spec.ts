import { expect, test } from "@playwright/test";

import {
  CALENDAR_DAYS,
  calendarWindow,
  dayColumns,
  hoursLevel,
  layoutBars,
  LANES,
} from "../../frontend/src/lib/equipment/calendar";
import { documentState, maintenanceBucket } from "../../frontend/src/lib/equipment/status";

// Чистая логика календаря и статусов техники, без браузера и стека.
const TODAY = "2026-10-12";
const labels = {
  assignment: (item: { project_name: string; planned_hours_per_day: string }) => `${item.project_name} ${item.planned_hours_per_day}`,
  maintenance: (item: { title: string }) => item.title,
};

test("окно календаря: ±4 недели от сегодня, сдвиг по неделям", () => {
  const window = calendarWindow(TODAY, 0);
  expect(window.from).toBe("2026-09-14");
  expect(window.to).toBe("2026-11-09");
  const columns = dayColumns(window.from, TODAY);
  expect(columns).toHaveLength(CALENDAR_DAYS);
  expect(columns.find((column) => column.today)?.iso).toBe(TODAY);
  const next = calendarWindow(TODAY, 1);
  expect(next.from).toBe("2026-09-21");
});

test("выходные: суббота и воскресенье помечены", () => {
  const columns = dayColumns("2026-10-10", "2026-10-10");
  // 10 октября 2026 - суббота, 11 - воскресенье, 12 - понедельник
  expect(columns[0].weekend).toBe(true);
  expect(columns[1].weekend).toBe(true);
  expect(columns[2].weekend).toBe(false);
});

test("уровень часов: пусто, частично, сутки заняты, перегруз", () => {
  expect(hoursLevel(0)).toBe("none");
  expect(hoursLevel(10)).toBe("partial");
  expect(hoursLevel(24)).toBe("full");
  expect(hoursLevel(26)).toBe("over");
});

test("полоски назначений не перекрываются в одной дорожке и не выходят за окно", () => {
  const from = "2026-10-10";
  const to = "2026-10-20";
  const layout = layoutBars(
    from,
    to,
    [
      { id: "a", project_id: "p1", project_name: "Восток", date_from: "2026-10-08", date_to: "2026-10-12", planned_hours_per_day: "8", status: "planned" },
      { id: "b", project_id: "p2", project_name: "Запад", date_from: "2026-10-12", date_to: "2026-10-14", planned_hours_per_day: "6", status: "planned" },
      { id: "c", project_id: "p3", project_name: "Север", date_from: "2026-10-13", date_to: "2026-10-13", planned_hours_per_day: "4", status: "planned" },
      { id: "d", project_id: "p4", project_name: "Юг", date_from: "2026-10-13", date_to: "2026-10-13", planned_hours_per_day: "2", status: "planned" },
    ],
    [{ id: "m", kind: "repair", title: "Ходовая", planned_from: "2026-10-18", planned_to: null, status: "planned" }],
    labels,
  );
  const first = layout.bars.find((bar) => bar.key === "a-a");
  expect(first?.start).toBe(0);
  expect(first?.end).toBe(2);
  const sameDay = layout.bars.filter((bar) => bar.start <= 3 && bar.end >= 3);
  expect(new Set(sameDay.map((bar) => bar.lane)).size).toBe(sameDay.length);
  for (const bar of layout.bars) {
    expect(bar.lane).toBeLessThan(LANES);
    expect(bar.start).toBeGreaterThanOrEqual(0);
    expect(bar.end).toBeLessThan(CALENDAR_DAYS);
  }
  const repair = layout.bars.find((bar) => bar.key === "m-m");
  expect(repair?.kind).toBe("repair");
});

test("третья полоска в один день уходит в скрытые, а не ломает раскладку", () => {
  const same = (id: string) => ({
    id,
    project_id: id,
    project_name: id,
    date_from: "2026-10-12",
    date_to: "2026-10-12",
    planned_hours_per_day: "1",
    status: "planned",
  });
  const layout = layoutBars("2026-10-10", "2026-10-20", [same("x"), same("y"), same("z")], [], labels);
  expect(layout.bars).toHaveLength(LANES);
  expect(layout.hidden).toBe(3 - LANES);
});

test("состояние документа по сроку и статус ТО по бакетам", () => {
  expect(documentState("2026-10-11", TODAY)).toBe("expired");
  expect(documentState("2026-11-01", TODAY)).toBe("expiring");
  expect(documentState("2027-06-01", TODAY)).toBe("valid");
  const overdue = { status: "planned", planned_from: "2026-10-01", planned_to: "2026-10-05" } as const;
  expect(maintenanceBucket({ ...overdue, id: "1", equipment_unit_id: "u", kind: "to", title: "t", project_id: null, done_at: null }, TODAY)).toBe("overdue");
  const upcoming = { status: "planned", planned_from: "2026-10-20", planned_to: null } as const;
  expect(maintenanceBucket({ ...upcoming, id: "2", equipment_unit_id: "u", kind: "to", title: "t", project_id: null, done_at: null }, TODAY)).toBe("upcoming");
  const later = { status: "planned", planned_from: "2026-12-20", planned_to: null } as const;
  expect(maintenanceBucket({ ...later, id: "3", equipment_unit_id: "u", kind: "to", title: "t", project_id: null, done_at: null }, TODAY)).toBe("later");
});
