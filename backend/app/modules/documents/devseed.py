"""Демо-документы S5a (ADR-015 часть 3 раздел 12, DoD S5a) для dev-сида.

По одному документу каждого вида на объект «Восток» и «Запад», юридические виды - на каждое юрлицо;
утверждённые примеры: договор заказчика с КС-2 и КС-3, акт субподрядчика, акт скрытых работ,
накладная. Файлы - небольшие корректные PDF, XLSX, DOCX, PNG - лежат в S3. Сид идёт от kut_worker
(как остальной сид): документы и версии пишутся напрямую, статусы меняет определитель.
Повтор не создаёт дублей: документ с тем же заголовком и юрлицом уже есть - пропуск.
"""

import hashlib
import io
import uuid
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Connection, text

from app.core import storage
from app.core.config import get_settings
from app.modules.documents.types import EXTENSIONS, KINDS, Link, Scope


def _xlsx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return buffer.getvalue()


def _docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


SAMPLE_FILES: Mapping[str, bytes] = {
    "application/pdf": b"%PDF-1.4\n%demo\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": _xlsx(),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _docx(),
    "image/png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 24,
}


@dataclass(frozen=True, slots=True)
class Demo:
    kind: str
    where: str  # «Восток», «Запад» или код юрлица, если вид юридический
    counterparty: str | None = None
    amount: str | None = None
    approve: bool = False
    links: tuple[Link, ...] = ()


# Контрагент вида по роли в ТЗ: заказчик, субподрядчик, поставщик, арендодатель (имена из сида).
_COUNTERPARTY = {
    "contract_customer": "Заказчик (dev)",
    "contract_subcontract": "Субподрядчик А (dev)",
    "contract_supply": "Поставщик (dev)",
    "contract_rental": "Арендодатель А (dev)",
    "ks2": "Заказчик (dev)",
    "ks3": "Заказчик (dev)",
    "ks6": "Субподрядчик А (dev)",
    "waybill_torg12": "Поставщик (dev)",
    "waybill_m15": "Поставщик (dev)",
    "invoice_esf": "Поставщик (dev)",
    "material_request": "Поставщик (dev)",
}


def _default(kind: str, where: str) -> Demo:
    spec = KINDS[kind]
    links = tuple(sorted(spec.links, key=lambda item: item.value)) if where == "Восток" else ()
    counterparty = _COUNTERPARTY[kind] if spec.counterparty else None
    amount = "10000.00" if spec.financial else None
    return Demo(kind, where, counterparty, amount, links=links)


def _plan() -> list[Demo]:
    """Утверждённые примеры (договор заказчика с КС-2 и КС-3, акт субподрядчика, акт скрытых работ,
    накладная), затем по одному документу каждого объектного вида на «Восток» и «Запад».
    Виды с узлом или работой на «Западе» пропускаются: узлов дерева там нет
    (сид дерева - только «Восток»)."""
    demos: list[Demo] = [
        Demo("contract_customer", "Восток", "Заказчик (dev)", "5000000.00", approve=True),
        Demo("ks2", "Восток", "Заказчик (dev)", "1250000.00", approve=True),
        Demo("ks2", "Восток", "Субподрядчик А (dev)", "800000.00", approve=True),
        Demo("ks3", "Восток", "Заказчик (dev)", "1250000.00", approve=True),
        Demo("hidden_works_act", "Восток", approve=True, links=(Link.PROJECT_NODE, Link.WORK)),
        Demo("commissioning_act", "Восток", approve=True),
        Demo("waybill_torg12", "Восток", "Поставщик (dev)", "120000.00", approve=True),
    ]
    taken = {(demo.kind, demo.where) for demo in demos}
    for where in ("Восток", "Запад"):
        for kind, spec in KINDS.items():
            if spec.scope is Scope.LEGAL_ENTITY or (kind, where) in taken:
                continue
            if where == "Запад" and spec.links:
                continue
            demos.append(_default(kind, where))
    return demos


def _legal_demos() -> list[Demo]:
    return [
        Demo("contract_supply", "ЮЛ1", "Поставщик (dev)", "250000.00"),
        Demo("contract_rental", "ЮЛ1", "Арендодатель А (dev)", "300000.00", approve=True),
        Demo("addendum", "ЮЛ1", None, "300000.00"),
        Demo("invoice_esf", "ЮЛ1", "Поставщик (dev)", "18000.00"),
        Demo("equipment_passport", "ЮЛ1", links=(Link.EQUIPMENT_UNIT,)),
        Demo("equipment_technical_inspection", "ЮЛ1", links=(Link.EQUIPMENT_UNIT,)),
        Demo("equipment_insurance", "ЮЛ1", links=(Link.EQUIPMENT_UNIT,)),
        Demo("equipment_permit", "ЮЛ1", links=(Link.EQUIPMENT_UNIT,)),
        Demo("equipment_other", "ЮЛ1", links=(Link.EQUIPMENT_UNIT,)),
    ]


def _first(connection: Connection, sql: str, **params: object) -> uuid.UUID | None:
    value = connection.execute(text(sql), params).scalar()
    return uuid.UUID(str(value)) if value is not None else None


def ensure_documents(
    connection: Connection,
    *,
    legal_entities: Mapping[str, uuid.UUID],
    projects: Mapping[str, uuid.UUID],
    counterparties: Mapping[str, uuid.UUID],
    transitions: list[tuple[uuid.UUID, str]],
) -> dict[str, int]:
    """Создаёт демо-документы; возвращает число строк по таблицам (для отчёта сида).
    Переходы статусов не выполняет: kut_worker не вызывает app.version_move (S6b), их добавляет в
    transitions, а сид выполняет владелец под kut_auth после коммита вставок."""
    counts = {"document": 0, "document_version": 0, "document_amount": 0, "document_link": 0}
    east, west = projects["east"], projects["west"]
    le_east, le_west = legal_entities["east"], legal_entities["west"]
    node = _first(
        connection,
        "SELECT id FROM project_node WHERE project_id = :p AND deleted_at IS NULL"
        " ORDER BY created_at, id LIMIT 1",
        p=east,
    )
    work = _first(connection, "SELECT id FROM work WHERE deleted_at IS NULL ORDER BY id LIMIT 1")
    unit = _first(
        connection,
        "SELECT id FROM equipment_unit WHERE legal_entity_id = :le AND deleted_at IS NULL"
        " ORDER BY id LIMIT 1",
        le=le_east,
    )
    contract = _first(
        connection,
        "SELECT id FROM contract WHERE legal_entity_id = :le AND deleted_at IS NULL"
        " ORDER BY id LIMIT 1",
        le=le_east,
    )
    target = {"Восток": (east, le_east), "Запад": (west, le_west), "ЮЛ1": (None, le_east)}
    for demo in [*_plan(), *_legal_demos()]:
        project_id, legal = target[demo.where]
        if demo.where == "ЮЛ1" and demo.kind.startswith("equipment") and unit is None:
            continue
        if demo.kind in {"contract_rental", "addendum"} and contract is None:
            continue
        if Link.PROJECT_NODE in demo.links and node is None:
            continue
        if Link.WORK in demo.links and work is None:
            continue
        title = f"Демо: {KINDS[demo.kind].prefix} {demo.where}"
        if demo.counterparty is not None:
            title = f"{title} ({demo.counterparty})"
        if _first(
            connection,
            "SELECT id FROM document WHERE title = :title AND legal_entity_id = :le"
            " AND deleted_at IS NULL",
            title=title,
            le=legal,
        ):
            continue
        counterparty_id = counterparties[demo.counterparty] if demo.counterparty else None
        spec = KINDS[demo.kind]
        year = connection.execute(
            text("SELECT EXTRACT(YEAR FROM app.local_today())::int")
        ).scalar_one()
        code = connection.execute(
            text("SELECT code FROM legal_entity WHERE id = :le"), {"le": legal}
        ).scalar_one()
        seq = connection.execute(
            text("SELECT app.next_document_number(:le, :kind, :year)"),
            {"le": legal, "kind": demo.kind, "year": year},
        ).scalar_one()
        document_id = connection.execute(
            text(
                "INSERT INTO document (legal_entity_id, project_id, counterparty_id, contract_id,"
                " kind, financial, number_year, number_seq, registration_no, title)"
                " VALUES (:le, :project, :counterparty, :contract, :kind, :financial, :year, :seq,"
                " :registration, :title) RETURNING id"
            ),
            {
                "le": legal,
                "project": project_id,
                "counterparty": counterparty_id,
                "contract": contract if demo.kind in {"contract_rental", "addendum"} else None,
                "kind": demo.kind,
                "financial": spec.financial,
                "year": year,
                "seq": seq,
                "registration": f"{spec.prefix}-{code}-{year}-{seq:04d}",
                "title": title,
            },
        ).scalar_one()
        counts["document"] += 1
        for link in demo.links:
            target_id = {
                Link.PROJECT_NODE: node,
                Link.WORK: work,
                Link.EQUIPMENT_UNIT: unit,
            }.get(link)
            column = {
                Link.PROJECT_NODE: "project_node_id",
                Link.WORK: "work_id",
                Link.EQUIPMENT_UNIT: "equipment_unit_id",
            }[link]
            connection.execute(
                text(
                    f"INSERT INTO document_link (document_id, legal_entity_id, {column})"  # noqa: S608
                    " VALUES (:d, :le, :t)"
                ),
                {"d": document_id, "le": legal, "t": target_id},
            )
            counts["document_link"] += 1
        content_type = _content_type(demo.kind)
        content = SAMPLE_FILES[content_type]
        scope_root = f"project/{project_id}" if project_id else f"legal_entity/{legal}"
        key = f"{scope_root}/document/{document_id}/{uuid.uuid4()}.{EXTENSIONS[content_type]}"
        storage.get_s3_client().put_object(
            Bucket=get_settings().s3_bucket, Key=key, Body=content, ContentType=content_type
        )
        version_id = connection.execute(
            text(
                "INSERT INTO document_version"
                " (document_id, legal_entity_id, project_id, version_no,"
                " s3_key, content_type, size_bytes, checksum_sha256, status)"
                " VALUES (:d, :le, :p, 1, :key, :ct, :size, :sha, 'draft') RETURNING id"
            ),
            {
                "d": document_id,
                "le": legal,
                "p": project_id,
                "key": key,
                "ct": content_type,
                "size": len(content),
                "sha": hashlib.sha256(content).hexdigest(),
            },
        ).scalar_one()
        counts["document_version"] += 1
        if demo.amount is not None:
            # Сумма принадлежит версии (R1): пишется в черновик, до утверждения.
            connection.execute(
                text(
                    "INSERT INTO document_version_amount"
                    " (version_id, document_id, legal_entity_id, amount)"
                    " VALUES (:v, :d, :le, CAST(:amount AS numeric))"
                ),
                {"v": version_id, "d": document_id, "le": legal, "amount": demo.amount},
            )
            counts["document_amount"] += 1
        if demo.approve:
            for step in ("on_approval", "approved"):
                transitions.append((version_id, step))
    return counts


def _content_type(kind: str) -> str:
    if kind in {"ks2", "ks3", "estimate_local", "estimate_summary"}:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if kind in {"hidden_works_act", "ird", "commissioning_act"}:
        return "image/png"
    if kind.startswith("waybill") or kind == "m7_act":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/pdf"
