import path from "node:path";

export const PASSWORD = process.env.DEV_SEED_PASSWORD ?? "dev_password_change_me";
export const EAST = "ЖК «Восток» (dev)";
export const WEST = "ЖК «Запад» (dev)";
// Мок геолокации телефона на площадке: точность 12 м.
export const GEO = { latitude: 42.8746, longitude: 74.5698, accuracy: 12 };
export const PHOTO = path.join(__dirname, "..", "fixtures", "site-photo.jpg");
export const SCREENS = path.join(__dirname, "..", "artifacts", "screens");

export const PHONE = {
  viewport: { width: 375, height: 812 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
} as const;
export const DESKTOP = { viewport: { width: 1280, height: 800 } } as const;

// Дата в Бишкеке: так её считает сервер (факт не может быть позже сегодняшнего дня).
export function today(offsetDays = 0): string {
  const moment = new Date(Date.now() + offsetDays * 86_400_000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bishkek" }).format(moment);
}
