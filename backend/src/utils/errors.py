"""业务异常，由路由统一转成 PyCore 信封。"""


class AppError(Exception):
    def __init__(self, error: str, error_code: str, status_code: int) -> None:
        super().__init__(error)
        self.error = error
        self.error_code = error_code
        self.status_code = status_code


class ValidationFailed(AppError):
    def __init__(self, error: str) -> None:
        super().__init__(error, "VALIDATION_ERROR", 400)


class Unauthorized(AppError):
    def __init__(self, error: str = "接续已失效，请用邮箱重新进入。") -> None:
        super().__init__(error, "UNAUTHORIZED", 401)


class Forbidden(AppError):
    def __init__(self, error: str = "不能查看别人的对话。") -> None:
        super().__init__(error, "FORBIDDEN", 403)


class NotFound(AppError):
    def __init__(self, error: str) -> None:
        super().__init__(error, "NOT_FOUND", 404)


class Conflict(AppError):
    def __init__(self, error: str) -> None:
        super().__init__(error, "CONFLICT", 409)


class InternalError(AppError):
    def __init__(self, error: str = "现在存不下简历，请稍后再试。") -> None:
        super().__init__(error, "INTERNAL_ERROR", 500)
