from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pycore.core.config import ConfigManager
from src.config.settings import load_settings_from_dict
from src.db.session import configure_engine_from_settings

TEST_SECRET = "test-secret-key-not-for-production"

_CORS = [
    "http://localhost:5199",
    "http://127.0.0.1:5199",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
]


@pytest.fixture
def tmp_env(tmp_path: Path) -> Iterator[Path]:
    ConfigManager.reset()
    load_settings_from_dict(
        {
            "debug": True,
            "secret_key": TEST_SECRET,
            "host": "127.0.0.1",
            "port": 8099,
            "cors_origins": _CORS,
            "database_path": str(tmp_path / "offer_coming.db"),
            "upload_dir": str(tmp_path / "uploads"),
            "resume_max_bytes": 2048,
            "session_ttl_days": 30,
            "timezone": "Asia/Shanghai",
            "llm_api_key": "",
            "llm_orchestrator_model": "test-orchestrator",
            "llm_nudge_model": "test-nudge",
            "llm_interview_model": "test-interview",
            "llm_knowledge_model": "test-knowledge",
            "llm_counseling_model": "test-counseling",
            "smtp_host": "",
            "smtp_from": "",
        }
    )
    configure_engine_from_settings()
    yield tmp_path
    ConfigManager.reset()


@pytest.fixture
def client(tmp_env: Path) -> Iterator[TestClient]:
    import src.main as main_mod

    application = main_mod.build_app()
    main_mod._app = application
    with TestClient(application) as test_client:
        yield test_client
    main_mod._app = None
