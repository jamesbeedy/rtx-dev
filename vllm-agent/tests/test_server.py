import json
import pytest
import respx
from httpx import Response, AsyncClient
from vllm_agent.server import app as fastapi_app


@pytest.fixture
def client():
    """Return a TestClient that always uses a freshly-reset server module.

    The auth tests call `importlib.reload(srv)` while VLLM_AGENT_API_KEY is
    set, which mutates the shared module dict.  Re-importing here (with the
    key absent from the environment) restores the clean state before every
    test that uses this fixture.
    """
    import importlib
    import vllm_agent.server as srv
    importlib.reload(srv)
    from fastapi.testclient import TestClient
    return TestClient(srv.app)


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


def test_run_401_when_key_set_and_missing(client, tmp_path, monkeypatch):
    """When VLLM_AGENT_API_KEY is set, /run requires Bearer header."""
    monkeypatch.setenv("VLLM_AGENT_API_KEY", "sekret")
    import importlib
    import vllm_agent.server as srv
    importlib.reload(srv)
    from fastapi.testclient import TestClient
    c = TestClient(srv.app)
    r = c.post("/run", json={"task": "x", "workdir": str(tmp_path)})
    assert r.status_code == 401


def test_run_401_when_key_wrong(tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_AGENT_API_KEY", "sekret")
    import importlib
    import vllm_agent.server as srv
    importlib.reload(srv)
    from fastapi.testclient import TestClient
    c = TestClient(srv.app)
    r = c.post("/run", json={"task": "x", "workdir": str(tmp_path)},
               headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_health_open_even_when_key_set(monkeypatch):
    """`/health` is intentionally unauthenticated so probes can reach it."""
    monkeypatch.setenv("VLLM_AGENT_API_KEY", "sekret")
    import importlib
    import vllm_agent.server as srv
    importlib.reload(srv)
    from fastapi.testclient import TestClient
    c = TestClient(srv.app)
    r = c.get("/health")
    assert r.status_code == 200


def test_artifacts_returns_summary_files_changed_transcript_tail(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "summary.md").write_text("did the thing\n")
    (out / "files_changed.txt").write_text("a.py\nb.py\n")
    (out / "transcript.jsonl").write_text(
        '{"kind":"message","role":"system","content":"sys"}\n'
        '{"kind":"message","role":"user","content":"go"}\n'
        '{"kind":"tool_call","tool":"finish","args":{},"result":{"status":"finished"}}\n'
    )
    r = client.get(f"/artifacts?out_dir={out}")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == "did the thing\n"
    assert body["files_changed"] == ["a.py", "b.py"]
    assert len(body["transcript_tail"]) == 3
    assert body["transcript_tail"][-1]["kind"] == "tool_call"


def test_artifacts_404_when_dir_missing(client, tmp_path):
    r = client.get(f"/artifacts?out_dir={tmp_path}/nonexistent")
    assert r.status_code == 404


def test_artifacts_tail_lines_caps_transcript(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "summary.md").write_text("ok")
    (out / "files_changed.txt").write_text("")
    (out / "transcript.jsonl").write_text(
        "\n".join(f'{{"kind":"message","i":{i}}}' for i in range(50)) + "\n"
    )
    r = client.get(f"/artifacts?out_dir={out}&tail_lines=10")
    assert r.status_code == 200
    assert len(r.json()["transcript_tail"]) == 10
    assert r.json()["transcript_tail"][0]["i"] == 40


def test_artifacts_401_when_key_set_and_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_AGENT_API_KEY", "sekret")
    import importlib
    import vllm_agent.server as srv
    importlib.reload(srv)
    from fastapi.testclient import TestClient
    out = tmp_path / "out"
    out.mkdir()
    (out / "summary.md").write_text("ok")
    c = TestClient(srv.app)
    r = c.get(f"/artifacts?out_dir={out}")
    assert r.status_code == 401


@respx.mock
def test_run_endpoint_accepts_skill_content(client, tmp_path, monkeypatch):
    """POST /run with skill_content threads it into the worker's system prompt."""
    monkeypatch.setenv("VLLM_BASE_URL", "https://vllm.example")
    monkeypatch.setenv("VLLM_MODEL", "qwen3-coder")
    captured: dict = {}

    def _capture(request):
        captured["body"] = json.loads(request.content)
        return Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx.post("https://vllm.example/v1/chat/completions").mock(side_effect=_capture)

    r = client.post("/run", json={
        "task": "x",
        "skill": "fake:skill",
        "skill_content": "--- INJECTED FROM HTTP BODY ---",
        "mode": "remote",
        "workdir": str(tmp_path),
        "out_dir": str(tmp_path / "out"),
        "max_iterations": 1,
        "max_tokens": 64,
    })
    assert r.status_code == 200
    sys_msg = next(m for m in captured["body"]["messages"] if m["role"] == "system")
    assert "INJECTED FROM HTTP BODY" in sys_msg["content"]
