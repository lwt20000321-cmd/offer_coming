from fastapi.testclient import TestClient


def _token(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": "reader@example.com"},
        files={"resume": ("resume.txt", b"resume text", "text/plain")},
    )
    data = response.json()["data"]
    return data["session_token"], data["conversation_id"]


def test_current_candidate_and_conversation(client: TestClient) -> None:
    token, conversation_id = _token(client)
    headers = {"Authorization": f"Bearer {token}"}
    current = client.get("/api/candidates/current", headers=headers)
    assert current.status_code == 200
    body = current.json()["data"]
    assert body["email"] == "reader@example.com"
    assert body["application_count"] == 0
    assert body["today"]["question_set_status"] == "none"

    conversation = client.get("/api/conversations/current", headers=headers)
    assert conversation.status_code == 200
    assert conversation.json()["data"]["id"] == conversation_id


def test_missing_or_wrong_token_is_unauthorized(client: TestClient) -> None:
    missing = client.get("/api/candidates/current")
    assert missing.status_code == 401
    assert missing.json()["error_code"] == "UNAUTHORIZED"

    wrong = client.get("/api/conversations/current", headers={"Authorization": "Bearer not-a-token"})
    assert wrong.status_code == 401
    assert wrong.json()["error_code"] == "UNAUTHORIZED"


def test_empty_reads_match_envelope(client: TestClient) -> None:
    token, conversation_id = _token(client)
    headers = {"Authorization": f"Bearer {token}"}

    messages = client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
    assert messages.status_code == 200
    assert messages.json()["success"] is True
    assert messages.json()["data"]["messages"] == []

    applications = client.get("/api/applications", headers=headers)
    assert applications.status_code == 200
    assert applications.json()["data"]["applications"] == []

    question_set = client.get("/api/question-sets/current", headers=headers)
    assert question_set.status_code == 200
    data = question_set.json()["data"]
    assert data["status"] == "none"
    assert data["questions"] == []
    assert data["beijing_date"]


def test_cors_allows_agent_and_gate_origins(client: TestClient) -> None:
    origins = [
        "http://localhost:5199",
        "http://127.0.0.1:5199",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ]
    for origin in origins:
        response = client.options(
            "/api/candidates/current",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == origin
