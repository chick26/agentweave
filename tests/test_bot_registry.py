"""Tests for bot manifest discovery and capability scoping."""

from pathlib import Path

import pytest

from agent_runtime.registry.bot_registry import BotRegistry
from agent_runtime.registry.skill_registry import AgentRegistry, SkillRegistry


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_skill(root: Path, name: str) -> None:
    _write(
        root / "skills" / name / "SKILL.md",
        f"---\nname: {name}\ndescription: {name} skill\n---\nBody",
    )


def _write_subagent(root: Path, name: str) -> None:
    _write(
        root / "subagents" / name / "AGENT.yaml",
        f"name: {name}\ndescription: {name} subagent\nexecution:\n  mode: worker\n",
    )
    _write(root / "subagents" / name / "prompt.md", "Prompt")


def _registry(root: Path) -> BotRegistry:
    agent_registry = AgentRegistry(subagents_root=root / "subagents")
    skill_registry = SkillRegistry(skills_root=root / "skills")
    return BotRegistry(
        bots_root=root / "bots",
        agent_registry=agent_registry,
        skill_registry=skill_registry,
    )


def test_bot_registry_reads_bot_yaml(tmp_path: Path) -> None:
    _write_skill(tmp_path, "data_analysis")
    _write_subagent(tmp_path, "text2sql")
    _write(
        tmp_path / "bots" / "data_analyst" / "BOT.yaml",
        "id: data_analyst\n"
        "name: 数据分析机器人\n"
        "description: 数据分析\n"
        "instructions: 使用结构化数据事实。\n"
        "subagents:\n"
        "  - text2sql\n"
        "skills:\n"
        "  - data_analysis\n"
        "welcome:\n"
        "  mode: static\n"
        "  preset_questions:\n"
        "    - domain_name: idc_resources\n"
        "      title: IDC 资源\n"
        "      questions:\n"
        "        - 403机房有多少可用机柜？\n"
        "        - 各机房可用机柜数量排行是什么？\n",
    )

    registry = _registry(tmp_path)
    bot = registry.get("data_analyst")

    assert [item.id for item in registry.discover()] == ["default", "data_analyst"]
    assert bot.name == "数据分析机器人"
    assert bot.subagents == ["text2sql"]
    assert bot.skills == ["data_analysis"]
    assert bot.welcome.mode == "static"
    assert bot.welcome.preset_questions == [
        {
            "domain_name": "idc_resources",
            "title": "IDC 资源",
            "questions": [
                "403机房有多少可用机柜？",
                "各机房可用机柜数量排行是什么？",
            ],
        }
    ]


def test_bot_registry_generates_default_when_no_bot_files(tmp_path: Path) -> None:
    _write_skill(tmp_path, "data_analysis")
    _write_subagent(tmp_path, "text2sql")

    bot = _registry(tmp_path).get("default")

    assert bot.generated is True
    assert bot.subagents == ["text2sql"]
    assert bot.skills == ["data_analysis"]


def test_bot_registry_rejects_unknown_resources(tmp_path: Path) -> None:
    _write(
        tmp_path / "bots" / "broken" / "BOT.yaml",
        "id: broken\nsubagents:\n  - missing_agent\nskills:\n  - missing_skill\n",
    )

    with pytest.raises(ValueError, match="missing_agent"):
        _registry(tmp_path).discover()
