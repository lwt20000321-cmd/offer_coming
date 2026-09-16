from pycore.api import APIConfig, APIServer
from pycore.api.middleware import ErrorHandlerMiddleware
from pycore.core import Logger, LoggerConfig, LogLevel, get_logger

from src.api.handlers import register_exception_handlers
from src.api.routes import (
    applications_router,
    candidates_router,
    conversations_router,
    knowledge_items_router,
    question_sets_router,
    sessions_router,
)
from src.api.routes.question_sets import internal_router as scheduler_internal_router
from src.config.settings import config_is_loaded, get_settings, load_app_config
from src.db.session import close_db, configure_engine_from_settings, init_db
from src.services.knowledge_store import resolve_knowledge_upload_dir
from src.services.scheduler import start_scheduler, stop_scheduler
from src.utils.paths import resolve_sqlite_file, resolve_upload_dir

_app = None


def build_app():
    settings = get_settings()
    Logger.configure(
        LoggerConfig(
            level=LogLevel.DEBUG if settings.debug else LogLevel.INFO,
            app_name="offer_coming",
            json_format=False,
            file_enabled=False,
        )
    )
    logger = get_logger()
    db_path = resolve_sqlite_file(settings.database_path)
    upload_dir = resolve_upload_dir(settings.upload_dir)
    knowledge_dir = resolve_knowledge_upload_dir()
    logger.info(
        "后端配置已加载",
        database=str(db_path),
        upload_dir=str(upload_dir),
        knowledge_dir=str(knowledge_dir),
    )

    server = APIServer(
        APIConfig(
            title="面试陪伴 Agent",
            version="0.1.0",
            host=settings.host,
            port=settings.port,
            debug=settings.debug,
            cors_origins=settings.cors_origins,
        )
    )
    server.on_startup(init_db)
    server.on_startup(start_scheduler)
    server.on_shutdown(stop_scheduler)
    server.on_shutdown(close_db)
    server.include_router(candidates_router)
    server.include_router(sessions_router)
    server.include_router(conversations_router)
    server.include_router(applications_router)
    server.include_router(knowledge_items_router)
    server.include_router(question_sets_router)
    server.include_router(scheduler_internal_router)
    server.add_middleware(ErrorHandlerMiddleware, debug=settings.debug)
    application = server.app
    register_exception_handlers(application)
    return application


def init_runtime():
    if not config_is_loaded():
        load_app_config()
    configure_engine_from_settings()
    return build_app()


class _AppProxy:
    async def __call__(self, scope, receive, send):
        global _app
        if _app is None:
            _app = init_runtime()
        await _app(scope, receive, send)

    def __getattr__(self, name: str):
        global _app
        if _app is None:
            _app = init_runtime()
        return getattr(_app, name)


app = _AppProxy()
