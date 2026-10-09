// Последние выбранные узлы мастера: хранятся на устройстве по пользователю. localStorage может
// быть закрыт (приватный режим, запрет сайта), тогда список просто пуст.
const LIMIT = 6;

export type RecentNode = { projectId: string; nodeId: string };

function storageKey(userId: string): string {
  return `kut.field.recent.${userId}`;
}

export function readRecents(userId: string): RecentNode[] {
  try {
    const raw = window.localStorage.getItem(storageKey(userId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is RecentNode =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as RecentNode).projectId === "string" &&
        typeof (item as RecentNode).nodeId === "string",
    );
  } catch {
    return [];
  }
}

export function rememberNode(userId: string, entry: RecentNode): void {
  try {
    const rest = readRecents(userId).filter(
      (item) => !(item.projectId === entry.projectId && item.nodeId === entry.nodeId),
    );
    window.localStorage.setItem(storageKey(userId), JSON.stringify([entry, ...rest].slice(0, LIMIT)));
  } catch {
    // хранилище недоступно: недавние узлы не запоминаются
  }
}
