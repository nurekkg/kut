// Запись очереди отправки: один факт работ с фото. Вся последовательность (создать факт, загрузить
// снимки, подтвердить их, отправить на проверку) идёт шагами, и после каждого шага запись
// сохраняется в IndexedDB. Ключи повтора созданы до первого запроса: потерянный ответ не
// породит второго факта, повтор получит прежний ответ сервера.

export type OutboxPhoto = {
  id: string;
  blob: Blob;
  // Маленькая картинка (data URL) для списка снимков на экране.
  thumb: string;
  name: string;
  takenAt: string;
  // Выдача ссылки на загрузку (сервер задаёт ключ в хранилище); живёт expiresIn секунд.
  key: string | null;
  uploadUrl: string | null;
  headers: Record<string, string> | null;
  presignedAt: number | null;
  expiresIn: number | null;
  uploaded: boolean;
};

export type FactPayload = {
  node_id: string;
  work_id: string;
  schedule_item_id: string | null;
  volume: string;
  work_date: string;
  latitude: string | null;
  longitude: string | null;
  accuracy_m: string | null;
};

export type OutboxLabel = { node: string; work: string; unit: string };

export type OutboxEntry = {
  id: string;
  userId: string;
  projectId: string;
  // create - новый факт; resubmit - отклонённый факт: правка, новые фото, повторная отправка.
  mode: "create" | "resubmit";
  createdAt: number;
  label: OutboxLabel;
  payload: FactPayload;
  factId: string | null;
  // Статус факта по последнему ответу сервера: approved у прораба и техдиректора с первого шага.
  factStatus: string | null;
  createKey: string;
  patch: Record<string, string | null> | null;
  patched: boolean;
  photos: OutboxPhoto[];
  confirmKey: string;
  confirmFingerprint: string;
  confirmed: boolean;
  submitted: boolean;
  // failed - сервер отказал по существу (нужны правка или удаление), автоповтор не нужен.
  stage: "pending" | "failed";
  error: string | null;
  attempts: number;
};

export type UploadProgress = { photo: number; photos: number; loaded: number; total: number };
