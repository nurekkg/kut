class AuthError(Exception):
    """Базовая ошибка аутентификации; роутер превращает её в ответ без деталей для клиента."""


class InvalidCredentials(AuthError):
    """Неверный логин, пароль или неактивный пользователь: ответ один и тот же."""


class RateLimited(AuthError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"retry after {retry_after}s")
        self.retry_after = retry_after


class InvalidToken(AuthError):
    """Токен (access, mfa или refresh) не принят."""


class InvalidCode(AuthError):
    """Неверный или уже использованный код TOTP / код восстановления."""


class InvalidCurrentPassword(AuthError):
    pass


class PasswordUnchanged(AuthError):
    pass


class MfaAlreadyEnabled(AuthError):
    pass


class MfaNotStarted(AuthError):
    pass


class PermissionDenied(AuthError):
    """У действующего пользователя нет права settings:admin (проверяет функция БД)."""


class Conflict(AuthError):
    """Значение уже занято (email, телефон): код поля для ответа клиенту."""

    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


class InvalidInput(AuthError):
    """БД отвергла данные пользователя (формат телефона, роль и контрагент не сочетаются)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class NotFound(AuthError):
    """Пользователь или роль не найдены."""
