from pathlib import Path
import json
import pytest
from vllm_agent.sessions import SessionStore, Session, SessionStatus


def test_create_and_load_session(tmp_path):
    store = SessionStore(root=tmp_path)
    s = store.create(goal="do X", skill="superpowers:tdd", skill_content=None,
                     mode="remote", workdir="/tmp/repo", model=None)
    assert s.session_id
    assert (tmp_path / s.session_id / "session.json").exists()

    s2 = store.load(s.session_id)
    assert s2.goal == "do X"
    assert s2.status == SessionStatus.RUNNING


def test_append_and_load_messages(tmp_path):
    store = SessionStore(root=tmp_path)
    s = store.create(goal="g", skill=None, skill_content=None, mode="local", workdir="/tmp", model=None)
    store.append_message(s.session_id, {"role": "user", "content": "go"})
    store.append_message(s.session_id, {"role": "assistant", "content": "ok"})
    msgs = store.load_messages(s.session_id)
    assert msgs == [{"role": "user", "content": "go"},
                    {"role": "assistant", "content": "ok"}]


def test_set_status(tmp_path):
    store = SessionStore(root=tmp_path)
    s = store.create(goal="g", skill=None, skill_content=None, mode="local", workdir="/tmp", model=None)
    store.set_status(s.session_id, SessionStatus.STOPPED)
    assert store.load(s.session_id).status == SessionStatus.STOPPED


def test_unknown_session_raises(tmp_path):
    store = SessionStore(root=tmp_path)
    with pytest.raises(KeyError):
        store.load("does-not-exist")


def test_session_persists_skill_content(tmp_path):
    """skill_content survives session.json round-trip."""
    store = SessionStore(root=tmp_path)
    s = store.create(
        goal="g",
        skill="superpowers:tdd",
        skill_content="--- TDD SKILL TEXT ---",
        mode="local",
        workdir="/tmp",
        model=None,
    )
    s2 = store.load(s.session_id)
    assert s2.skill == "superpowers:tdd"
    assert s2.skill_content == "--- TDD SKILL TEXT ---"


def test_session_skill_content_defaults_to_none(tmp_path):
    """Sessions created without skill_content default to None (back-compat)."""
    store = SessionStore(root=tmp_path)
    s = store.create(
        goal="g",
        skill=None,
        skill_content=None,
        mode="local",
        workdir="/tmp",
        model=None,
    )
    s2 = store.load(s.session_id)
    assert s2.skill_content is None
