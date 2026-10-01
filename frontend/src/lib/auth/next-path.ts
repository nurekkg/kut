export const DEFAULT_PATH = "/projects";

// Параметр next приходит из адресной строки: пускаем только пути внутри приложения,
// иначе форма входа стала бы открытым редиректом на чужой сайт.
export function safeNext(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return DEFAULT_PATH;
  }
  if (value === "/login" || value.startsWith("/login?")) return DEFAULT_PATH;
  return value;
}
