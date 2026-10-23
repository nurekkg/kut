// Черновик приёма и выдачи на телефоне в sessionStorage вкладки. Хранятся идентификаторы созданных документов,
// отпечатки файлов (имя, размер, время изменения) и ключи идемпотентности запросов создания. Перезагрузка вкладки
// после сбоя продолжает тот же акт или ВН и не заводит второй: повтор того же запроса с тем же ключом сервер
// отвечает прежним результатом. Черновик привязан к пользователю: чужой черновик на общем телефоне не виден.
// Хранилища может не быть (приватный режим): тогда черновик живёт в памяти вкладки.

const PREFIX = "kut-stock-draft:";

export type CertEntry = { docId: string; fingerprint: string | null; loaded: boolean };

export type ReceiptDraft = {
  actId: string | null;
  versionId: string | null;
  scanFingerprint: string | null;
  certs: Record<string, CertEntry>;
};

export type IssueDraft = { id: string; versionId: string };

export type SavedKeys = Record<string, { request: string; key: string }>;

export function draftName(kind: "receipt" | "issue" | "inventory", projectId: string, userId: string): string {
  return `${kind}:${projectId}:${userId}`;
}

// Отпечаток файла: тот же файл после перезагрузки узнаётся по имени, размеру и времени изменения.
export function fileFingerprint(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

// Ключ сертификата в приходе: одна партия - один сертификат. Две партии одного материала с разными номерами -
// два сертификата; одна и та же партия в двух строках - один сертификат.
export function certificateKey(materialId: string, batchNo: string): string {
  return `${materialId}|${batchNo.trim()}`;
}

export function readDraft<T>(name: string): T | null {
  try {
    const raw = window.sessionStorage.getItem(PREFIX + name);
    return raw === null ? null : (JSON.parse(raw) as T);
  } catch {
    return null;
  }
}

export function writeDraft(name: string, value: unknown | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(PREFIX + name);
    else window.sessionStorage.setItem(PREFIX + name, JSON.stringify(value));
  } catch {
    // Хранилища нет: черновик живёт в памяти вкладки, как и раньше.
  }
}

// Вход другого пользователя на этой вкладке: черновики прежнего не продолжаются и не видны.
export function purgeStockDrafts(userId: string): void {
  try {
    const keep = [`:${userId}`, `:${userId}#keys`];
    const names: string[] = [];
    for (let index = 0; index < window.sessionStorage.length; index += 1) {
      const key = window.sessionStorage.key(index);
      if (key !== null && key.startsWith(PREFIX)) names.push(key);
    }
    for (const key of names) {
      if (!keep.some((suffix) => key.endsWith(suffix))) window.sessionStorage.removeItem(key);
    }
  } catch {
    // Хранилища нет: очищать нечего.
  }
}
