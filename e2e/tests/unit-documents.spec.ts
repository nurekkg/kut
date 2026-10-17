import { expect, test } from "@playwright/test";

import {
  canArchive,
  canCreate,
  canDownload,
  canEditVersionAmount,
  canEnterAmount,
  canExport,
  canSeeAmountColumn,
  canSeeMoney,
  canUseCounterparties,
  canWriteKind,
  effectiveDiffers,
} from "../../frontend/src/lib/documents/access";
import { ApiError } from "../../frontend/src/lib/auth/api";
import { documentErrorKey } from "../../frontend/src/lib/documents/errors";
import { formatAmount, formatDay, parseAmount } from "../../frontend/src/lib/documents/format";
import {
  equipmentDocumentKind,
  isEquipmentDocumentKind,
  isMoneyEquipmentDocumentKind,
} from "../../frontend/src/lib/documents/equipment-kinds";
import { creatableKinds, isKindCode } from "../../frontend/src/lib/documents/kinds";
import type { KindOut, VersionOut } from "../../frontend/src/lib/documents/types";
import {
  contentTypeOf,
  isRetryable,
  prepareUpload,
  sha256Base64,
  UPLOAD_MAX_BYTES,
  UploadError,
  uploadVersion,
  type VersionTransport,
} from "../../frontend/src/lib/documents/upload";

// Чистая логика экранов документов, без браузера и без стека. Права проверяются через массивы строк
// прав, а не через текст интерфейса: скрытая кнопка лишь подсказка, решает сервер.
const NBSP = String.fromCharCode(0xa0);
const IDEMPOTENCY_KEY = "idem-1";

test("колонка суммы: без строк и без сумм её нет, с одной суммой есть", () => {
  expect(canSeeAmountColumn([])).toBe(false);
  expect(canSeeAmountColumn([{ amount: null }])).toBe(false);
  expect(canSeeAmountColumn([{ amount: null }, { amount: "12.00" }])).toBe(true);
});

test("создание и загрузка видов юрлица и объекта требуют documents:write", () => {
  expect(canWriteKind("ks2", [])).toBe(false);
  expect(canWriteKind("ks2", ["documents:read"])).toBe(false);
  expect(canWriteKind("ks2", ["documents:write"])).toBe(true);
  expect(canCreate([])).toBe(false);
  expect(canCreate(["documents:read"])).toBe(false);
  expect(canCreate(["documents:write"])).toBe(true);
});

test("виды техники пишет право техники; технический осмотр - ещё и механик (R2)", () => {
  expect(canWriteKind("equipment_passport", ["documents:write"])).toBe(false);
  expect(canWriteKind("equipment_passport", ["equipment:write:documents"])).toBe(true);
  expect(canWriteKind("equipment_technical_inspection", ["equipment:write:maintenance"])).toBe(true);
  expect(canWriteKind("equipment_passport", ["equipment:write:maintenance"])).toBe(false);
  expect(canWriteKind("equipment_insurance", ["equipment:write:maintenance"])).toBe(false);
  expect(isEquipmentDocumentKind("equipment_permit")).toBe(true);
  expect(isEquipmentDocumentKind("ks2")).toBe(false);
});

test("архивирование требует отдельного уточнения documents:write:archive", () => {
  expect(canArchive(["documents:write"])).toBe(false);
  expect(canArchive(["documents:write:archive"])).toBe(true);
});

test("экспорт реестра требует documents:export", () => {
  expect(canExport(["documents:read"])).toBe(false);
  expect(canExport(["documents:export"])).toBe(true);
});

test("деньги вида: денежные виды техники - по тарифам (R3), прочие финансовые - по documents:read:financial", () => {
  expect(canSeeMoney("ks2", ["documents:read:financial"])).toBe(true);
  expect(canSeeMoney("ks2", ["equipment:read:rates"])).toBe(false);
  expect(canSeeMoney("equipment_insurance", ["equipment:read:rates"])).toBe(true);
  expect(canSeeMoney("equipment_insurance", ["documents:read:financial"])).toBe(false);
  expect(isMoneyEquipmentDocumentKind("equipment_other")).toBe(true);
  expect(isMoneyEquipmentDocumentKind("equipment_passport")).toBe(false);
});

test("ссылка на файл финансового вида только при праве на деньги вида", () => {
  expect(canDownload({ kind: "ks2", financial: true }, [])).toBe(false);
  expect(canDownload({ kind: "ks2", financial: true }, ["documents:read:financial"])).toBe(true);
  expect(canDownload({ kind: "ks2", financial: false }, [])).toBe(true);
  expect(
    canDownload({ kind: "equipment_insurance", financial: true }, ["equipment:read:documents"]),
  ).toBe(false);
  expect(canDownload({ kind: "equipment_insurance", financial: true }, ["equipment:read:rates"])).toBe(
    true,
  );
  expect(
    canDownload({ kind: "equipment_passport", financial: false }, ["equipment:read:documents"]),
  ).toBe(true);
});

test("внешним ссылку на финансовый файл отдаёт сервер по их видимости, внутреннему - по праву", () => {
  expect(canDownload({ kind: "ks2", financial: true }, [], false)).toBe(true);
  expect(canDownload({ kind: "ks2", financial: true }, [], true)).toBe(false);
});

test("сумма версии правится только в черновике, не в архиве и не внешним (R1)", () => {
  const ks2 = { kind: "ks2", financial: true, archived_at: null };
  const rights = ["documents:write", "documents:read:financial"];
  expect(canEditVersionAmount(ks2, { status: "draft" }, rights)).toBe(true);
  expect(canEditVersionAmount(ks2, { status: "approved" }, rights)).toBe(false);
  expect(
    canEditVersionAmount({ ...ks2, archived_at: "2026-10-01T00:00:00Z" }, { status: "draft" }, rights),
  ).toBe(false);
  expect(canEditVersionAmount(ks2, { status: "draft" }, ["documents:write"])).toBe(false);
  expect(canEditVersionAmount(ks2, { status: "draft" }, rights, false)).toBe(false);
  const insurance = { kind: "equipment_insurance", financial: true, archived_at: null };
  expect(
    canEditVersionAmount(insurance, { status: "draft" }, ["equipment:write:documents", "equipment:read:rates"]),
  ).toBe(true);
  expect(canEditVersionAmount(insurance, { status: "draft" }, ["equipment:write:documents"])).toBe(false);
});

test("сумму при загрузке вводит внутренний с правом на деньги вида", () => {
  expect(canEnterAmount({ kind: "ks2", financial: true }, ["documents:read:financial"])).toBe(true);
  expect(canEnterAmount({ kind: "ks2", financial: false }, ["documents:read:financial"])).toBe(false);
  expect(canEnterAmount({ kind: "ks2", financial: true }, ["documents:read:financial"], false)).toBe(false);
  expect(canEnterAmount({ kind: "equipment_insurance", financial: true }, ["documents:read:financial"])).toBe(
    false,
  );
  expect(canEnterAmount({ kind: "equipment_insurance", financial: true }, ["equipment:read:rates"])).toBe(
    true,
  );
});

test("отметка «действует vN» только когда действующая версия не последняя", () => {
  expect(effectiveDiffers({ effective_version_no: 1, latest_version_no: 2 })).toBe(true);
  expect(effectiveDiffers({ effective_version_no: 2, latest_version_no: 2 })).toBe(false);
  expect(effectiveDiffers({ effective_version_no: null, latest_version_no: 1 })).toBe(false);
});

test("контрагенты - по документному праву чтения (справочник внутренним, не по праву техники)", () => {
  expect(canUseCounterparties(["documents:read"])).toBe(true);
  expect(canUseCounterparties(["equipment:read:rates"])).toBe(false);
  expect(canUseCounterparties(["equipment:write"])).toBe(false);
});

test("сумма: тысячи через пробел-неразрывный и две цифры после запятой", () => {
  expect(formatAmount("1234.50")).toBe(`1${NBSP}234,50`);
  expect(formatAmount("0.5")).toBe("0,50");
});

test("ввод суммы: пробелы и запятая допустимы, лишние знаки и буквы - нет", () => {
  expect(parseAmount("1 234,5")).toBe("1234.5");
  expect(parseAmount(`1${NBSP}234,50`)).toBe("1234.50");
  expect(parseAmount("100")).toBe("100");
  expect(parseAmount("12.345")).toBeNull();
  expect(parseAmount("abc")).toBeNull();
  expect(parseAmount("")).toBeNull();
});

test("дата показывается по Бишкеку: 23:30 UTC - уже следующий день", () => {
  expect(formatDay("2026-10-14T23:30:00Z")).toBe("15.10.2026");
});

test("код ошибки переводится в ключ текста, префикс document. снимается", () => {
  expect(documentErrorKey("document.macros_forbidden")).toBe("macrosForbidden");
  expect(documentErrorKey("macros_forbidden")).toBe("macrosForbidden");
  expect(documentErrorKey("equipment.document_kind_mismatch")).toBe("equipmentDocumentKindMismatch");
  expect(documentErrorKey("nope")).toBe("unknown");
  expect(documentErrorKey("constructor")).toBe("unknown");
});

test("тип файла берётся из расширения без учёта регистра; неизвестное расширение отвергается", () => {
  expect(contentTypeOf("Отчёт.PDF")).toBe("application/pdf");
  expect(contentTypeOf("скан.heic")).toBe("image/heic");
  expect(contentTypeOf("file.exe")).toBeNull();
  expect(contentTypeOf("noext")).toBeNull();
  expect(contentTypeOf("a.constructor")).toBeNull();
});

test("повтор: сбой сети и 5xx да, отказ по содержимому файла нет", () => {
  expect(isRetryable(new TypeError("Failed to fetch"))).toBe(true);
  expect(isRetryable(new ApiError(503, "storage_unavailable"))).toBe(true);
  expect(isRetryable(new ApiError(409, "document.not_uploaded"))).toBe(true);
  expect(isRetryable(new UploadError("not_uploaded"))).toBe(true);
  expect(isRetryable(new ApiError(422, "document.macros_forbidden"))).toBe(false);
  expect(isRetryable(new ApiError(422, "document.active_content"))).toBe(false);
  expect(isRetryable(new ApiError(409, "document.file_already_confirmed"))).toBe(false);
});

test("контрольная сумма SHA-256 в base64 совпадает с эталоном", async () => {
  expect(await sha256Base64(new Blob(["abc"]))).toBe("ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=");
});

test("выбор файла: неизвестный тип, пустой и слишком большой файл отвергаются до хэширования", async () => {
  const code = async (file: File) => {
    try {
      await prepareUpload(file, IDEMPOTENCY_KEY);
      return "accepted";
    } catch (error) {
      return error instanceof UploadError ? error.code : "other";
    }
  };
  expect(await code(new File(["x"], "a.exe"))).toBe("type_not_allowed");
  expect(await code(new File([], "a.pdf"))).toBe("empty");
  const huge = { name: "a.pdf", size: UPLOAD_MAX_BYTES + 1 } as unknown as File;
  expect(await code(huge)).toBe("too_large");
});

test("подготовка файла: тип, контрольная сумма и ключ идемпотентности", async () => {
  const work = await prepareUpload(new File(["abc"], "scan.PDF"), "key-1");
  expect(work.name).toBe("scan.PDF");
  expect(work.contentType).toBe("application/pdf");
  expect(work.checksum).toBe("ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=");
  expect(work.idempotencyKey).toBe("key-1");
  expect(work.presign).toBeNull();
});

test("вид строки срока техники соответствует виду документа реестра", () => {
  expect(equipmentDocumentKind("passport")).toBe("equipment_passport");
  expect(equipmentDocumentKind("technical_inspection")).toBe("equipment_technical_inspection");
  expect(equipmentDocumentKind("insurance")).toBe("equipment_insurance");
  expect(equipmentDocumentKind("permit")).toBe("equipment_permit");
  expect(equipmentDocumentKind("other")).toBe("equipment_other");
  expect(equipmentDocumentKind("unknown")).toBeNull();
});

function kind(code: string, overrides: Partial<KindOut> = {}): KindOut {
  return {
    code,
    prefix: "XX",
    scope: "object",
    financial: false,
    counterparty_required: false,
    contract_required: false,
    links: [],
    ...overrides,
  };
}

test("в реестре объекта создаются только виды без связей, договора и юрличных видов", () => {
  const kinds = [
    kind("ks2", { financial: true, counterparty_required: true }),
    kind("hidden_works_act", { links: ["project_node", "work"] }),
    kind("contract_rental", { scope: "legal_entity", contract_required: true }),
    kind("contract_supply", { scope: "legal_entity", counterparty_required: true }),
    kind("waybill_internal", { scope: "either" }),
  ];
  expect(creatableKinds(kinds, true).map((item) => item.code)).toEqual(["ks2", "waybill_internal"]);
  expect(creatableKinds(kinds, false).map((item) => item.code)).toEqual(["waybill_internal"]);
});

test("код вида: известный принимается, произвольный - нет", () => {
  expect(isKindCode("ks2")).toBe(true);
  expect(isKindCode("zzz")).toBe(false);
});

// Версии: ссылка presign и ключ идемпотентности переживают сбой, поэтому повтор не удваивает версию.
function fakeTransport(
  puts: ("stored" | "exists" | "expired")[],
  confirmFailures = 0,
  found: VersionOut | null = null,
) {
  const log: string[] = [];
  const presignBodies: unknown[] = [];
  const confirmKeys: string[] = [];
  const confirmBodies: { key: string; comment: string | null; amount: string | null }[] = [];
  let putIndex = 0;
  let failed = 0;
  const transport: VersionTransport = {
    presign: async (body) => {
      presignBodies.push(body);
      log.push("presign");
      const n = presignBodies.length;
      return {
        key: `doc/${n}.pdf`,
        upload_url: `https://s3.test/${n}`,
        headers: { "Content-Type": body.content_type, "If-None-Match": "*" },
        expires_in: 900,
      };
    },
    put: async () => {
      log.push("put");
      const result = puts[putIndex] ?? "stored";
      putIndex += 1;
      return result;
    },
    find: async () => {
      log.push("find");
      return found;
    },
    confirm: async (body, idempotencyKey) => {
      log.push("confirm");
      confirmKeys.push(idempotencyKey);
      confirmBodies.push(body);
      if (failed < confirmFailures) {
        failed += 1;
        throw new Error("network");
      }
      return {
        id: "v1",
        document_id: "d1",
        version_no: 1,
        status: "draft",
        content_type: "application/pdf",
        size_bytes: 3,
        created_at: "2026-10-15T00:00:00Z",
        comment: body.comment,
        checksum_sha256: null,
        author_name: null,
      };
    },
  };
  return { transport, log, presignBodies, confirmKeys, confirmBodies };
}

test("загрузка версии: presign, PUT, confirm с ключом идемпотентности файла", async () => {
  const work = await prepareUpload(new File(["abc"], "a.pdf"), "key-A");
  const fake = fakeTransport(["stored"]);
  const version = await uploadVersion(work, fake.transport, { comment: "скан", amount: null });
  expect(version.id).toBe("v1");
  expect(fake.log).toEqual(["presign", "put", "confirm"]);
  expect(fake.confirmKeys).toEqual(["key-A"]);
  expect(fake.confirmBodies[0]).toEqual({ key: "doc/1.pdf", comment: "скан", amount: null });
});

test("повтор после сбоя confirm: ссылка и ключ те же, presign не повторяется", async () => {
  const work = await prepareUpload(new File(["abc"], "a.pdf"), "key-B");
  const fake = fakeTransport(["stored", "exists"], 1);
  await expect(uploadVersion(work, fake.transport, { comment: null, amount: null })).rejects.toThrow("network");
  await uploadVersion(work, fake.transport, { comment: null, amount: null });

  expect(fake.presignBodies).toHaveLength(1);
  expect(fake.log).toEqual(["presign", "put", "confirm", "put", "confirm"]);
  expect(fake.confirmKeys).toEqual(["key-B", "key-B"]);
  expect(fake.confirmBodies[0]).toEqual(fake.confirmBodies[1]);
});

test("истёкшая ссылка (403) перевыпускается один раз, с новым ключом S3", async () => {
  const work = await prepareUpload(new File(["abc"], "a.pdf"), "key-C");
  const fake = fakeTransport(["expired", "stored"]);
  await uploadVersion(work, fake.transport, { comment: null, amount: null });
  expect(fake.presignBodies).toHaveLength(2);
  expect(fake.confirmBodies[0]?.key).toBe("doc/2.pdf");
});

test("истёкшая ссылка после сбоя confirm: версия находится по контрольной сумме, дубля нет", async () => {
  const work = await prepareUpload(new File(["abc"], "a.pdf"), "key-F");
  const found: VersionOut = {
    id: "v-found",
    document_id: "d1",
    version_no: 1,
    status: "draft",
    content_type: "application/pdf",
    size_bytes: 3,
    created_at: "2026-10-15T00:00:00Z",
    comment: null,
    checksum_sha256: null,
    author_name: null,
  };
  const fake = fakeTransport(["stored"], 1, found);
  await expect(uploadVersion(work, fake.transport, { comment: null, amount: null, now: () => 0 })).rejects.toThrow("network");
  const version = await uploadVersion(work, fake.transport, { comment: null, amount: null, now: () => 10_000_000 });
  expect(version.id).toBe("v-found");
  expect(fake.log).toEqual(["presign", "put", "confirm", "find"]);
});

test("если и вторая ссылка истекла, версия не подтверждается", async () => {
  const work = await prepareUpload(new File(["abc"], "a.pdf"), "key-D");
  const fake = fakeTransport(["expired", "expired"]);
  const error = await uploadVersion(work, fake.transport, { comment: null, amount: null }).catch((reason: unknown) => reason);
  expect(error).toBeInstanceOf(UploadError);
  expect((error as UploadError).code).toBe("not_uploaded");
  expect(fake.log).not.toContain("confirm");
});

test("ссылка presign старше срока (минус запас) перевыпускается по времени", async () => {
  const work = await prepareUpload(new File(["abc"], "a.pdf"), "key-E");
  const fake = fakeTransport(["stored", "stored"]);
  const start = 1_000_000;
  await uploadVersion(work, fake.transport, { comment: null, amount: null, now: () => start });
  await uploadVersion(work, fake.transport, { comment: null, amount: null, now: () => start + 900_000 });
  expect(fake.presignBodies).toHaveLength(2);
});
