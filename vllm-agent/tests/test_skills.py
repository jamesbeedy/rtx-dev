from pathlib import Path
import pytest
from vllm_agent.skills import SkillLoader, SkillNotFound


@pytest.fixture
def fake_roots(tmp_path):
    """Build a fake skill-root layout under tmp_path."""
    proj = tmp_path / "project_skills"
    user = tmp_path / "user_skills"
    sp = tmp_path / "superpowers" / "claude-plugins-official" / "superpowers" / "5.1.0" / "skills"
    sp.mkdir(parents=True)
    user.mkdir()
    proj.mkdir()

    # superpowers:test-driven-development
    (sp / "tdd").mkdir()
    (sp / "tdd" / "SKILL.md").write_text(
        "---\nname: test-driven-development\ndescription: Use when implementing\n---\n\nbody"
    )
    # user-level skill
    (user / "my-skill").mkdir()
    (user / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: user skill\n---\n\nuser body"
    )
    # project-level skill (overrides user if same name)
    (proj / "my-skill").mkdir()
    (proj / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: project skill\n---\n\nproject body"
    )
    return [proj, user, sp]


def test_list_skills(fake_roots):
    loader = SkillLoader(roots=fake_roots)
    skills = loader.list_skills()
    names = {s["name"] for s in skills}
    assert "project:my-skill" in names
    assert "user:my-skill" in names
    assert "superpowers:test-driven-development" in names


def test_list_skills_includes_metadata(fake_roots):
    loader = SkillLoader(roots=fake_roots)
    by_name = {s["name"]: s for s in loader.list_skills()}
    assert by_name["superpowers:test-driven-development"]["description"] == "Use when implementing"
    assert "path" in by_name["superpowers:test-driven-development"]


def test_load_skill_returns_full_content(fake_roots):
    loader = SkillLoader(roots=fake_roots)
    content = loader.load_skill("superpowers:test-driven-development")
    assert "name: test-driven-development" in content
    assert "body" in content


def test_load_unknown_skill_raises(fake_roots):
    loader = SkillLoader(roots=fake_roots)
    with pytest.raises(SkillNotFound):
        loader.load_skill("project:does-not-exist")


def test_project_overrides_user_when_same_name(fake_roots):
    loader = SkillLoader(roots=fake_roots)
    # Both `project:my-skill` and `user:my-skill` should be listed (different namespaces).
    names = {s["name"] for s in loader.list_skills()}
    assert "project:my-skill" in names
    assert "user:my-skill" in names
    # And content is distinguishable.
    assert "project body" in loader.load_skill("project:my-skill")
    assert "user body" in loader.load_skill("user:my-skill")
