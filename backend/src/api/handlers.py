from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pycore.api.responses import error_response
from pycore.core.logger import get_logger

from src.utils.errors import AppError

logger = get_logger()

_ONBOARDING_HINT = "请先填写邮箱并上传简历，才能和小凹对话。"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        logger.info("接口返回业务错误", error_code=exc.error_code, detail=exc.error)
        resp, status = error_response(
            error=exc.error,
            error_code=exc.error_code,
            status_code=exc.status_code,
        )
        return JSONResponse(status_code=status, content=jsonable_encoder(resp))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, _exc: RequestValidationError) -> JSONResponse:
        path = request.url.path
        if path.rstrip("/").endswith("/sessions"):
            message = "请填写邮箱。"
        else:
            message = _ONBOARDING_HINT
        logger.info("请求参数校验失败", path=path)
        resp, status = error_response(
            error=message,
            error_code="VALIDATION_ERROR",
            status_code=400,
        )
        return JSONResponse(status_code=status, content=jsonable_encoder(resp))
