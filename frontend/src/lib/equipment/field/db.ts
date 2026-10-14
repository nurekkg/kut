import type { ShiftEntry } from "./types";

// Очередь машиниста лежит в своей базе IndexedDB: фото чека (Blob) и ключи повтора должны пережить
// перезагрузку страницы и разрыв связи. Оболочка минимальная: put, remove и список.
const DB_NAME = "kut-equipment-field";
const DB_VERSION = 1;
const STORE = "outbox";

let opening: Promise<IDBDatabase> | null = null;

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE, { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("indexeddb_blocked"));
  });
}

function database(): Promise<IDBDatabase> {
  opening ??= openDatabase().catch((error: unknown) => {
    opening = null;
    throw error;
  });
  return opening;
}

async function run<T>(
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const connection = await database();
  return new Promise<T>((resolve, reject) => {
    const transaction = connection.transaction(STORE, mode);
    const request = action(transaction.objectStore(STORE));
    transaction.oncomplete = () => resolve(request.result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export const shiftDb = {
  put: (entry: ShiftEntry) => run("readwrite", (store) => store.put(entry)),
  remove: (id: string) => run("readwrite", (store) => store.delete(id)),
  all: () => run<ShiftEntry[]>("readonly", (store) => store.getAll()),
};
