"""
数据库会话管理。基于 pycore/integrations/db/session.py 模板扩展。
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from pycore.core.logger import get_logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.utils.paths import sqlite_url

logger = get_logger()

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def configure_engine(database_url: str, *, echo: bool = False) -> None:
    global _engine, _session_maker
    _engine = create_async_engine(database_url, echo=echo, future=True)
    _session_maker = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


def configure_engine_from_settings() -> None:
    from src.config.settings import get_settings

    settings = get_settings()
    configure_engine(sqlite_url(settings.database_path), echo=False)


def get_engine() -> AsyncEngine:
    if _engine is None:
        configure_engine_from_settings()
    assert _engine is not None
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    if _session_maker is None:
        configure_engine_from_settings()
    assert _session_maker is not None
    return _session_maker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话（用于 FastAPI Depends）。"""
    async with get_session_maker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """上下文管理器形式的数据库会话。"""
    async with get_session_maker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """初始化数据库（创建表）。"""
    from src.db.models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库已初始化")


async def close_db() -> None:
    """关闭数据库连接。"""
    global _engine, _session_maker
    if _engine is not None:
        await _engine.dispose()
        logger.info("数据库连接已关闭")
    _engine = None
    _session_maker = None
