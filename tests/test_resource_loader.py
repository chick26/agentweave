from pathlib import Path

from agent_runtime.registry.resources import ResourceLoader
from agent_runtime.registry.skill_registry import AgentRegistry, SkillRegistry


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_resource_loader_prefers_agents_md_over_project_md(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_PROJECT_RULES_PATH", raising=False)
    _write(tmp_path / "PROJECT.md", "project rules")
    _write(tmp_path / "AGENTS.md", "agents rules")

    loader = ResourceLoader(
        root=tmp_path,
        skill_registry=SkillRegistry(skills_root=tmp_path / "skills"),
        agent_registry=AgentRegistry(subagents_root=tmp_path / "subagents"),
    )

    rules, source = loader.get_project_rules()

    assert rules == "agents rules"
    assert source.endswith("AGENTS.md")


def test_resource_loader_reload_invalidates_registry_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_PROJECT_RULES_PATH", raising=False)
    _write(
        tmp_path / "skills" / "one" / "SKILL.md",
        "---\nname: one\ndescription: One\n---\nBody",
    )
    loader = ResourceLoader(
        root=tmp_path,
        skill_registry=SkillRegistry(skills_root=tmp_path / "skills"),
        agent_registry=AgentRegistry(subagents_root=tmp_path / "subagents"),
    )

    assert [skill.name for skill in loader.discover().skills] == ["one"]

    _write(
        tmp_path / "skills" / "two" / "SKILL.md",
        "---\nname: two\ndescription: Two\n---\nBody",
    )
    summary = loader.reload()

    assert summary["skills"]["added"] == ["two"]
    assert [skill.name for skill in loader.discover().skills] == ["one", "two"]

