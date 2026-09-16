"""路径解析：相对路径相对 backend/ 目录，并创建父目录。"""

from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def resolve_backend_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = _BACKEND_ROOT / path
    return path.resolve()


def resolve_sqlite_file(database_path: str) -> Path:
    path = resolve_backend_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def resolve_upload_dir(upload_dir: str) -> Path:
    path = resolve_backend_path(upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sqlite_url(database_path: str) -> str:
    path = resolve_sqlite_file(database_path)
    return f"sqlite+aiosqlite:///{path}"
