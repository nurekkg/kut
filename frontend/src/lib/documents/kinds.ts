import { DOCUMENT_KIND_CODES, type DocumentKindCode, type KindOut } from "./types";

export function isKindCode(value: string): value is DocumentKindCode {
  return (DOCUMENT_KIND_CODES as readonly string[]).includes(value);
}

// В реестре объекта создаются виды без обязательных связей и договора: остальные создаются из своих
// разделов (узел, работа, техника, договор). Виды юрлица (без объекта) в реестр объекта не попадают.
export function creatableKinds(kinds: readonly KindOut[], canCounterparty: boolean): KindOut[] {
  return kinds.filter(
    (kind) =>
      kind.links.length === 0 &&
      !kind.contract_required &&
      kind.scope !== "legal_entity" &&
      (!kind.counterparty_required || canCounterparty),
  );
}
