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
