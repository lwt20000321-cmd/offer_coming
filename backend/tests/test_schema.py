import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

EXPECTED_TABLES = {
    "candidates",
    "sessions",
    "conversations",
    "messages",
    "applications",
    "question_sets",
    "questions",
    "nudge_logs",
}


def test_all_tables_created(client: TestClient, tmp_env: Path) -> None:
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    names = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
    db.close()
    assert EXPECTED_TABLES <= names


def test_backend_src_does_not_read_process_env() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    hits: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "os.getenv" in text or "os.environ" in text:
            hits.append(str(path))
    assert hits == []


def test_relative_paths_resolve_under_backend() -> None:
    from src.utils.paths import resolve_sqlite_file, resolve_upload_dir

    db_path = resolve_sqlite_file("data/offer_coming.db")
    upload_dir = resolve_upload_dir("data/uploads")
    assert db_path.name == "offer_coming.db"
    assert db_path.parent.name == "data"
    assert "backend" in db_path.parts
    assert upload_dir.name == "uploads"
    assert upload_dir.is_dir()
