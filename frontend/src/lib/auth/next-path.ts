import type { Me } from "@/lib/auth/types";

export const DEFAULT_PATH = "/projects";

// Первый раздел после входа: список объектов нужен только тем, у кого есть objects:read.
export function landingPath(user: Pick<Me, "role" | "permissions"> | null): string {
  if (!user || user.permissions.includes("objects:read")) return DEFAULT_PATH;
  if (user.role === "machinist") return "/equipment/my";
  if (user.permissions.includes("equipment:read")) return "/equipment";
  return DEFAULT_PATH;
}

const PLACEHOLDER_ORIGIN = "http://same-origin.invalid";

// Параметр next приходит из адресной строки: пускаем только пути внутри приложения, иначе форма
// входа стала бы открытым редиректом на чужой сайт. Парсеры URL выбрасывают табуляции и переводы
// строк, поэтому «/<tab>/evil.example» превращается в «//evil.example»: управляющие символы и
// обратная косая черта отклоняются сразу, а итог проверяется разбором URL, а не префиксами строки.
export function safeNext(value: string | null): string {
  if (!value || !value.startsWith("/")) return DEFAULT_PATH;
  for (const char of value) {
    const code = char.charCodeAt(0);
    if (code < 32 || code === 127 || char === "\\") return DEFAULT_PATH;
  }
  let url: URL;
  try {
    url = new URL(value, PLACEHOLDER_ORIGIN);
  } catch {
    return DEFAULT_PATH;
  }
  if (url.origin !== PLACEHOLDER_ORIGIN) return DEFAULT_PATH;
  const path = url.pathname + url.search + url.hash;
  if (path === "/login" || path.startsWith("/login?")) return DEFAULT_PATH;
  return path;
}
