from vllm_agent.tools import Tool, WORKER_TOOLS, register


async def _noop(args, ctx):
    return {"ok": True}


def test_register_and_lookup():
    t = Tool(name="x_demo", schema={"type": "function"}, execute=_noop)
    register(t)
    assert WORKER_TOOLS["x_demo"] is t
    # cleanup so other tests aren't affected
    WORKER_TOOLS.pop("x_demo", None)


def test_double_register_raises():
    t = Tool(name="x_dup", schema={}, execute=_noop)
    register(t)
    import pytest
    with pytest.raises(ValueError):
        register(t)
    WORKER_TOOLS.pop("x_dup", None)
