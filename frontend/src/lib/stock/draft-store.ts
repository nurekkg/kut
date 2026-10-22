// Черновик приёма и выдачи на телефоне в sessionStorage: только идентификаторы уже созданных на сервере
// документов (акт М-7, версия со сканом, сертификаты, ВН). Перезагрузка вкладки после сбоя продолжает
// тот же акт или ВН и не заводит второй. Ключи идемпотентности сюда не пишем: после перезагрузки запросы
// шлются заново, а уже созданное находится по идентификатору. Хранилища может не быть (приватный режим).

const PREFIX = "kut-stock-draft:";

export type ReceiptDraft = {
  actId: string | null;
  versionId: string | null;
  certDocs: Record<string, string>;
};

export type IssueDraft = { id: string; versionId: string };

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
