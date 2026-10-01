import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.context import system_context, use_context
from app.core.db import get_portal_sessionmaker, get_sessionmaker
from app.core.request_id import get_request_id
from app.modules.auth import denylist
from app.modules.auth.principal import Principal, principal_store
from app.modules.auth.tokens import AccessClaims, InvalidTokenError, decode_access
from app.modules.rbac.enums import Action, Module

_bearer = HTTPBearer(auto_error=False)


def unauthorized(detail: str = "invalid_token") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


def client_ip(request: Request) -> str:
    """Адрес клиента; X-Forwarded-For (последний хоп) учитывается только за доверенным прокси."""
    if get_settings().trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


async def get_access_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AccessClaims:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    try:
        return decode_access(credentials.credentials)
    except InvalidTokenError as error:
        raise unauthorized() from error


async def get_principal(claims: AccessClaims = Depends(get_access_claims)) -> Principal:
    if await denylist.is_denied(claims.sid):
        raise unauthorized()
    record = await principal_store.snapshot(claims.user_id)
    # role_ver в токене должен совпасть с текущим: смена роли, деактивация и отзыв прав
    # делают все выданные раньше токены недействительными.
    if record is None or not record.is_active or record.role_ver != claims.role_ver:
        raise unauthorized()
    return Principal.from_record(record, sid=claims.sid, amr=claims.amr, otp_at=claims.otp_at)


async def get_system_session(
    request_id: uuid.UUID = Depends(get_request_id),
) -> AsyncIterator[AsyncSession]:
    """Сессия kut_app без пользователя: вход, обновление токена, функции app.*."""
    with system_context(request_id):
        async with get_sessionmaker()() as session:
            yield session


async def get_db(
    principal: Principal = Depends(get_principal),
    request_id: uuid.UUID = Depends(get_request_id),
) -> AsyncIterator[AsyncSession]:
    """Сессия под контекстом пользователя; внешние роли идут через отдельный пул kut_portal."""
    maker = get_sessionmaker() if principal.is_internal else get_portal_sessionmaker()
    with use_context(principal.to_context(request_id)):
        async with maker() as session:
            yield session


async def get_account_session(
    principal: Principal = Depends(get_principal),
    request_id: uuid.UUID = Depends(get_request_id),
) -> AsyncIterator[AsyncSession]:
    """Операции над своей учётной записью: функции app.* выданы только kut_app, в том числе для
    внешних ролей, поэтому пул всегда kut_app, а контекст принадлежит пользователю."""
    with use_context(principal.to_context(request_id)):
        async with get_sessionmaker()() as session:
            yield session


def require(
    module: Module,
    action: Action,
    *,
    qualifier: str | None = None,
    project_param: str | None = None,
) -> Callable[..., Awaitable[Principal]]:
    """Право по матрице: нет права - 403, чужой объект - 404 (его существование не раскрываем)."""

    async def dependency(
        request: Request, principal: Principal = Depends(get_principal)
    ) -> Principal:
        if project_param is not None:
            try:
                project_id = uuid.UUID(str(request.path_params.get(project_param)))
            except ValueError as error:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from error
            if project_id not in principal.project_ids:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        if not principal.has(module, action, qualifier):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return principal

    return dependency
