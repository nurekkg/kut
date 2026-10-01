import uuid
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.auth import service
from app.modules.auth.deps import (
    client_ip,
    get_access_claims,
    get_account_session,
    get_principal,
    get_system_session,
    unauthorized,
)
from app.modules.auth.errors import (
    InvalidCode,
    InvalidCredentials,
    InvalidCurrentPassword,
    InvalidToken,
    MfaAlreadyEnabled,
    MfaNotStarted,
    PasswordUnchanged,
    RateLimited,
)
from app.modules.auth.principal import Principal, principal_store
from app.modules.auth.schemas import (
    LoginRequest,
    MeResponse,
    MfaConfirmRequest,
    MfaConfirmResponse,
    MfaRequiredResponse,
    MfaSetupRequest,
    MfaSetupResponse,
    MfaVerifyRequest,
    PasswordChangeRequest,
    TokenResponse,
)
from app.modules.auth.service import IssuedSession, MfaChallenge
from app.modules.auth.tokens import InvalidTokenError, decode_access, decode_mfa

REFRESH_COOKIE = "kut_refresh"
COOKIE_PATH = "/api/v1/auth"

router = APIRouter(prefix="/auth", tags=["auth"])
_optional_bearer = HTTPBearer(auto_error=False)
Session = Annotated[AsyncSession, Depends(get_system_session)]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _set_refresh_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=get_settings().cookies_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        httponly=True,
        secure=get_settings().cookies_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )


def _token_response(issued: IssuedSession, response: Response) -> TokenResponse:
    _set_refresh_cookie(response, issued.refresh_token, issued.refresh_max_age)
    return TokenResponse(
        access_token=issued.access_token, expires_in=issued.expires_in, user=issued.me
    )


def _too_many(error: RateLimited) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "too_many_attempts",
        headers={"Retry-After": str(error.retry_after)},
    )


@router.post("/login", response_model=TokenResponse | MfaRequiredResponse)
async def login(
    body: LoginRequest, request: Request, response: Response, session: Session
) -> TokenResponse | MfaRequiredResponse:
    _no_store(response)
    try:
        result = await service.login(
            session,
            identifier=body.identifier,
            password=body.password,
            ip=client_ip(request),
        )
    except InvalidCredentials as error:
        # Один и тот же ответ для неверного логина, пароля и неактивного пользователя.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials") from error
    except RateLimited as error:
        raise _too_many(error) from error
    if isinstance(result, MfaChallenge):
        return MfaRequiredResponse(
            status=result.status, mfa_token=result.mfa_token, expires_in=result.expires_in
        )
    return _token_response(result, response)


@router.post("/mfa/verify", response_model=TokenResponse)
async def verify_mfa(
    body: MfaVerifyRequest, request: Request, response: Response, session: Session
) -> TokenResponse:
    _no_store(response)
    try:
        issued = await service.verify_mfa(
            session,
            mfa_token=body.mfa_token,
            code=body.code,
            recovery_code=body.recovery_code,
            ip=client_ip(request),
        )
    except InvalidToken as error:
        raise unauthorized() from error
    except InvalidCode as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_code") from error
    except InvalidCredentials as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials") from error
    except RateLimited as error:
        raise _too_many(error) from error
    return _token_response(issued, response)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    session: Session,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> Response:
    if not refresh_token:
        return _refresh_failed()
    try:
        issued = await service.refresh(session, refresh_token=refresh_token)
    except InvalidToken:
        return _refresh_failed()
    body = TokenResponse(
        access_token=issued.access_token, expires_in=issued.expires_in, user=issued.me
    )
    reply = JSONResponse(body.model_dump(mode="json"))
    _no_store(reply)
    _set_refresh_cookie(reply, issued.refresh_token, issued.refresh_max_age)
    return reply


def _refresh_failed() -> Response:
    reply = JSONResponse({"detail": "invalid_token"}, status_code=status.HTTP_401_UNAUTHORIZED)
    _no_store(reply)
    _clear_refresh_cookie(reply)
    return reply


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> Response:
    sid: uuid.UUID | None = None
    if credentials is not None:
        try:
            sid = decode_access(credentials.credentials).sid
        except InvalidTokenError:
            sid = None  # просроченный access не мешает выйти по refresh-cookie
    await service.logout(session, refresh_token=refresh_token, access_sid=sid)
    reply = Response(status_code=status.HTTP_204_NO_CONTENT)
    _no_store(reply)
    _clear_refresh_cookie(reply)
    return reply


@router.get("/me", response_model=MeResponse)
async def me(response: Response, principal: Principal = Depends(get_principal)) -> MeResponse:
    _no_store(response)
    return service.me_response(principal, amr=principal.amr)


async def _mfa_actor(
    body_token: str | None,
    credentials: HTTPAuthorizationCredentials | None,
) -> tuple[uuid.UUID, str, bool]:
    """Кто настраивает 2FA: владелец access-токена или обладатель enroll-токена (вход по паролю)."""
    if body_token:
        try:
            user_id = decode_mfa(body_token, scope="enroll").user_id
        except InvalidTokenError as error:
            raise unauthorized() from error
        record = await principal_store.snapshot(user_id)
        if record is None:
            raise unauthorized()
        return user_id, record.full_name, True
    principal = await get_principal(await get_access_claims(credentials))
    return principal.user_id, principal.full_name, False


@router.post("/mfa/setup", response_model=MfaSetupResponse)
async def mfa_setup(
    body: MfaSetupRequest,
    response: Response,
    session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
) -> MfaSetupResponse:
    _no_store(response)
    user_id, account, _ = await _mfa_actor(body.mfa_token, credentials)
    try:
        secret, uri = await service.start_mfa_setup(session, user_id=user_id, account=account)
    except MfaAlreadyEnabled as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "mfa_already_enabled") from error
    return MfaSetupResponse(secret=secret, otpauth_uri=uri)


@router.post("/mfa/confirm", response_model=MfaConfirmResponse)
async def mfa_confirm(
    body: MfaConfirmRequest,
    request: Request,
    response: Response,
    session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
) -> MfaConfirmResponse:
    _no_store(response)
    user_id, _account, enrolling = await _mfa_actor(body.mfa_token, credentials)
    ip = client_ip(request)
    try:
        codes = await service.confirm_mfa(session, user_id=user_id, code=body.code, ip=ip)
    except InvalidCode as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_code") from error
    except MfaNotStarted as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "mfa_not_started") from error
    except MfaAlreadyEnabled as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "mfa_already_enabled") from error
    except RateLimited as error:
        raise _too_many(error) from error
    session_body: TokenResponse | None = None
    if enrolling:
        issued = await service.start_session_after_enrollment(session, user_id=user_id, ip=ip)
        session_body = _token_response(issued, response)
    return MfaConfirmResponse(recovery_codes=codes, session=session_body)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    session: Annotated[AsyncSession, Depends(get_account_session)],
    principal: Principal = Depends(get_principal),
) -> Response:
    try:
        await service.change_password(
            session, principal=principal, current=body.current_password, new=body.new_password
        )
    except InvalidCurrentPassword as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_current_password") from error
    except PasswordUnchanged as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "password_unchanged") from error
    except RateLimited as error:
        raise _too_many(error) from error
    reply = Response(status_code=status.HTTP_204_NO_CONTENT)
    _no_store(reply)
    return reply
