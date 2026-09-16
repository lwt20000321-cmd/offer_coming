from pathlib import Path

from fastapi.testclient import TestClient


def test_parse_failure_still_saves_original(client: TestClient, tmp_env: Path) -> None:
    response = client.post(
        "/api/candidates",
        data={"email": "parse-fail@example.com"},
        files={"resume": ("resume.pdf", b"this is not a pdf", "application/pdf")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["candidate"]["resume_parse_ok"] is False
    assert body["data"]["candidate"]["resume_filename"] == "resume.pdf"
    saved = list((tmp_env / "uploads").rglob("resume.pdf"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"this is not a pdf"
