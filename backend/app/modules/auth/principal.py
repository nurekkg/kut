import uuid
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.context import RequestContext, system_context
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.modules.auth import repository
from app.modules.auth.repository import ContextRecord
from app.modules.rbac.enums import Action, Module


@dataclass(frozen=True, slots=True)
class Principal:
    """Кто делает запрос: личность из токена плюс права и область доступа из БД."""

    user_id: uuid.UUID
    full_name: str
    role_code: str
    role_scope: str
    role_ver: int
    counterparty_id: uuid.UUID | None
    permissions: frozenset[str]
    project_ids: frozenset[uuid.UUID]
    legal_entity_ids: frozenset[uuid.UUID]
    sid: uuid.UUID
    amr: tuple[str, ...]
    otp_at: int | None

    @classmethod
    def from_record(
        cls, record: ContextRecord, *, sid: uuid.UUID, amr: tuple[str, ...], otp_at: int | None
    ) -> "Principal":
        return cls(
            user_id=record.user_id,
            full_name=record.full_name,
            role_code=record.role_code,
            role_scope=record.role_scope,
            role_ver=record.role_ver,
            counterparty_id=record.counterparty_id,
            permissions=record.permissions,
            project_ids=record.project_ids,
            legal_entity_ids=record.legal_entity_ids,
            sid=sid,
            amr=amr,
            otp_at=otp_at,
        )

    @property
    def is_internal(self) -> bool:
        return self.role_scope != "counterparty"

    @property
    def sees_all_users(self) -> bool:
        # Решение PO после S1a: всех пользователей видят роли scope=group и роль с settings:admin.
        return self.role_scope == "group" or "settings:admin" in self.permissions

    def has(self, module: Module, action: Action, qualifier: str | None = None) -> bool:
        suffix = f":{qualifier}" if qualifier else ""
        return f"{module.value}:{action.value}{suffix}" in self.permissions

    def to_context(self, request_id: uuid.UUID | None) -> RequestContext:
        return RequestContext(
            user_id=self.user_id,
            is_internal=self.is_internal,
            project_ids=self.project_ids,
            legal_entity_ids=self.legal_entity_ids,
            counterparty_id=self.counterparty_id,
            request_id=request_id,
            sees_all_users=self.sees_all_users,
        )


def _role_ver_key(user_id: uuid.UUID) -> str:
    return f"rv:{user_id}"


def _context_key(user_id: uuid.UUID, role_ver: int) -> str:
    return f"ctx:{user_id}:{role_ver}"


# Поколение области доступа: общий счётчик растёт, когда у многих пользователей сразу меняется
# список объектов или юрлиц (создан объект или юрлицо: роли scope=group видят все). Контекст,
# записанный при другом поколении, не используется - его перечитывают из БД.
_GENERATION_KEY = "scope_gen"


def _user_generation_key(user_id: uuid.UUID) -> str:
    return f"cg:{user_id}"


class PrincipalStore:
    """Кэш прав и области доступа в Redis по (user_id, role_ver) с коротким TTL (ADR-001 §3).

    Актуальный role_ver лежит в отдельном ключе rv:{user}: сервис обновляет его сразу при смене
    роли, деактивации и входе; при промахе источник истины - БД (app.auth_context). Поколение
    области доступа (scope_gen) снимает устаревшие контексты разом: bump_scope().
    """

    async def snapshot(self, user_id: uuid.UUID) -> ContextRecord | None:
        redis = get_redis()
        current, user_generation, generation = await redis.mget(
            _role_ver_key(user_id), _user_generation_key(user_id), _GENERATION_KEY
        )
        if current is not None and (user_generation or "0") == (generation or "0"):
            cached = await redis.get(_context_key(user_id, int(current)))
            if cached is not None:
                return ContextRecord.from_json(cached)
        return await self.reload(user_id)

    async def bump_scope(self) -> None:
        """После создания объекта или юрлица: контексты scope=group всех пользователей устарели."""
        await get_redis().incr(_GENERATION_KEY)

    async def reload(self, user_id: uuid.UUID) -> ContextRecord | None:
        # Поколение читаем до обращения к БД: если оно вырастет во время загрузки, запись
        # получит старый номер и при следующем запросе будет перечитана.
        generation = await get_redis().get(_GENERATION_KEY) or "0"
        with system_context():
            async with get_sessionmaker()() as session:
                record = await repository.auth_context(session, user_id)
        if record is None or not record.is_active:
            await self.forget(user_id)
            return None
        await self.prime(record, generation)
        return record

    async def prime(self, record: ContextRecord, generation: str) -> None:
        ttl = get_settings().principal_cache_ttl_seconds
        redis = get_redis()
        async with redis.pipeline(transaction=False) as pipeline:
            pipeline.set(_context_key(record.user_id, record.role_ver), record.to_json(), ex=ttl)
            pipeline.set(_role_ver_key(record.user_id), record.role_ver, ex=ttl)
            pipeline.set(_user_generation_key(record.user_id), generation, ex=ttl)
            await pipeline.execute()

    async def set_role_ver(self, user_id: uuid.UUID, role_ver: int) -> None:
        """После смены роли или деактивации: токены со старым role_ver отклоняются сразу."""
        await get_redis().set(
            _role_ver_key(user_id), role_ver, ex=get_settings().principal_cache_ttl_seconds
        )

    async def forget(self, user_id: uuid.UUID) -> None:
        await get_redis().delete(_role_ver_key(user_id))


principal_store = PrincipalStore()
