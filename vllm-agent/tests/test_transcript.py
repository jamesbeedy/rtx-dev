import json
from vllm_agent.transcript import Transcript


def test_transcript_appends_jsonl(tmp_path):
    t = Transcript(tmp_path / "out" / "t.jsonl")
    t.append({"role": "system", "content": "hi"})
    t.append({"role": "user", "content": "go"})
    lines = (tmp_path / "out" / "t.jsonl").read_text().splitlines()
    rec0 = json.loads(lines[0])
    rec1 = json.loads(lines[1])
    assert rec0["role"] == "system" and rec0["content"] == "hi"
    assert rec1["role"] == "user" and rec1["content"] == "go"
    assert "ts" in rec0  # confirms timestamp injection


def test_transcript_records_tool_call(tmp_path):
    t = Transcript(tmp_path / "t.jsonl")
    t.record_tool_call("read_file", {"path": "x"}, {"content": "..."})
    rec = json.loads((tmp_path / "t.jsonl").read_text().splitlines()[0])
    assert rec["kind"] == "tool_call"
    assert rec["tool"] == "read_file"
    assert rec["args"] == {"path": "x"}
    assert rec["result"] == {"content": "..."}
