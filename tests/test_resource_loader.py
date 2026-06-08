"""Tests for aggregate resource discovery used by UI and prompts."""

from pathlib import Path

from agent_runtime.worker.subagent_runner import SubagentRunner
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


def test_resource_loader_reload_invalidates_extension_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_PROJECT_RULES_PATH", raising=False)
    subagent_dir = tmp_path / "subagents" / "reload_worker"
    _write(
        subagent_dir / "AGENT.yaml",
        "name: reload_worker\n"
        "description: Reload worker.\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: orchestrator\n"
        "extension:\n"
        "  module: subagents.reload_worker.extension\n",
    )
    _write(subagent_dir / "prompt.md", "Context: {value}\n")
    _write(
        subagent_dir / "extension.py",
        "def context(manifest):\n"
        "    return {'value': 'v1'}\n\n"
        "def register(api):\n"
        "    api.prompt_context(context)\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    agent_registry = AgentRegistry(subagents_root=tmp_path / "subagents")
    loader = ResourceLoader(
        root=tmp_path,
        skill_registry=SkillRegistry(skills_root=tmp_path / "skills"),
        agent_registry=agent_registry,
    )
    runner = SubagentRunner(registry=agent_registry, root=tmp_path)

    assert runner._build_worker_prompt(agent_registry.get("reload_worker")) == "Context: v1"

    _write(
        subagent_dir / "extension.py",
        "def context(manifest):\n"
        "    return {'value': 'v2'}\n\n"
        "def register(api):\n"
        "    api.prompt_context(context)\n",
    )
    loader.reload()

    assert runner._build_worker_prompt(agent_registry.get("reload_worker")) == "Context: v2"


def test_resource_loader_includes_bot_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_PROJECT_RULES_PATH", raising=False)
    loader = ResourceLoader(
        root=tmp_path,
        skill_registry=SkillRegistry(skills_root=tmp_path / "skills"),
        agent_registry=AgentRegistry(subagents_root=tmp_path / "subagents"),
    )

    assert [bot.id for bot in loader.discover().bots] == ["default"]

    _write(
        tmp_path / "bots" / "one" / "BOT.yaml",
        "id: one\nname: One\nsubagents: []\nskills: []\n",
    )
    summary = loader.reload()

    assert summary["bots"]["added"] == ["one"]
    assert [bot.id for bot in loader.discover().bots] == ["default", "one"]
