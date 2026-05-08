import json
import pytest
import respx
from httpx import Response, AsyncClient
from vllm_agent.server import app as fastapi_app


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(fastapi_app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@respx.mock
def test_run_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": "done"}}]
        })
    )
    r = client.post("/run", json={
        "task": "do nothing",
        "mode": "remote",
        "workdir": str(tmp_path),
        "out_dir": str(tmp_path / "out"),
        "max_iterations": 1,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["run_id"]


def test_skills_endpoint(client):
    r = client.get("/skills")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_session_step_404_for_unknown(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))
    r = client.post("/session/does-not-exist/step", json={"max_iterations": 1})
    assert r.status_code == 404
    assert "does-not-exist" in r.json().get("detail", "")


def test_session_stop_404_for_unknown(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))
    r = client.post("/session/does-not-exist/stop")
    assert r.status_code == 404


@respx.mock
def test_session_full_lifecycle(client, tmp_path, monkeypatch):
    """Start → step → status → stop, all via HTTP."""
    respx.post("https://vllm.example/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": "ok", "tool_calls": []}}]
        })
    )
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))

    r = client.post("/session", json={"goal": "g", "workdir": str(tmp_path)})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert r.json()["status"] == "running"

    r = client.post(f"/session/{sid}/step", json={"max_iterations": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["status"] == "completed"
    assert body["step_status"] == "ok"

    r = client.get(f"/session/{sid}")
    assert r.status_code == 200
    assert r.json()["iterations_total"] == 1

    r = client.post(f"/session/{sid}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


def test_session_status_404(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_AGENT_SESSION_ROOT", str(tmp_path / "sessions"))
    r = client.get("/session/does-not-exist")
    assert r.status_code == 404
