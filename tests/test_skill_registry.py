from pathlib import Path

import pytest

from agent_runtime.registry.skill_registry import AgentRegistry, SkillRegistry
from subagents.text2sql.domain_registry import Text2SQLDomainRegistry


def test_agent_registry_loads_subagents_only():
    registry = AgentRegistry(subagents_root=Path("subagents"))

    text2sql = registry.get("text2sql")

    assert [agent.name for agent in registry.discover()] == ["text2sql"]
    assert text2sql.kind == "subagent"
    assert text2sql.location.name == "AGENT.md"
    assert text2sql.execution.mode == "worker"
    assert text2sql.execution.model_role == "orchestrator"
    assert text2sql.execution.tool_module == "subagents.text2sql.tools"
    assert text2sql.execution.context_module == "subagents.text2sql.domain_registry"
    assert "execute_sql" in text2sql.tools
    assert "plan_sql_query" in text2sql.body
    assert "数据库查询" in text2sql.routing_hints[0]
    assert text2sql.domains.root == "domains"


def test_skill_registry_loads_real_skills_only():
    registry = SkillRegistry(skills_root=Path("skills"))

    data_analysis = registry.get("data_analysis")

    assert [skill.name for skill in registry.discover()] == ["data_analysis"]
    assert data_analysis.kind == "skill"
    assert data_analysis.location.name == "SKILL.md"
    assert data_analysis.execution.mode == "inline"
    assert data_analysis.tools == []
    assert "统计摘要" in data_analysis.description
    assert "Workflow" in data_analysis.body
    with pytest.raises(ValueError, match="Unknown skill"):
        registry.get("text2sql")


def test_text2sql_domain_registry_loads_subagent_local_domains():
    registry = AgentRegistry(subagents_root=Path("subagents"))
    domain_registry = Text2SQLDomainRegistry.from_agent(registry.get("text2sql"))
    idc = domain_registry.get_domain("idc_resources")

    assert idc.location.as_posix().endswith(
        "subagents/text2sql/domains/idc_resources/DOMAIN.md"
    )
    assert idc.table == "resources"
    assert "machine_room" in idc.text_fields


def test_agent_md_parsing_reads_frontmatter_and_body():
    registry = AgentRegistry(subagents_root=Path("subagents"))

    agent = registry.get("text2sql")

    assert agent.location.name == "AGENT.md"
    assert agent.name == "text2sql"
    assert agent.tools == [
        "get_current_time",
        "plan_sql_query",
        "execute_sql",
    ]
    assert agent.routing_hints
    assert "可用数据域" in agent.body


def test_text2sql_prompt_keeps_result_contract_without_tool_permission_noise():
    agent = AgentRegistry(subagents_root=Path("subagents")).get("text2sql")

    assert "plan_sql_query" in agent.body
    assert "严格 JSON" in agent.body
    assert "未查询到符合条件的数据" in agent.body
    assert "run_skill" not in agent.body
    assert "memory_search" not in agent.body


def test_agent_registry_cache_refreshes_when_manifest_changes(tmp_path):
    subagents_root = tmp_path / "subagents"
    agent_dir = subagents_root / "demo"
    agent_dir.mkdir(parents=True)
    manifest_path = agent_dir / "AGENT.md"
    manifest_path.write_text(
        "---\nname: demo\ndescription: First\n---\nBody one\n",
        encoding="utf-8",
    )
    registry = AgentRegistry(subagents_root=subagents_root)

    first = registry.get("demo")
    second = registry.get("demo")
    manifest_path.write_text(
        "---\nname: demo\ndescription: Second\n---\nBody two changed\n",
        encoding="utf-8",
    )
    refreshed = registry.get("demo")
    registry.invalidate()
    after_invalidate = registry.get("demo")

    assert first.description == "First"
    assert second.description == "First"
    assert refreshed.description == "Second"
    assert after_invalidate.description == "Second"


def test_worker_subagent_requires_model_role(tmp_path):
    subagents_root = tmp_path / "subagents"
    agent_dir = subagents_root / "demo"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text(
        "---\n"
        "name: demo\n"
        "description: Missing role\n"
        "execution:\n"
        "  mode: worker\n"
        "---\n"
        "Body\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing execution.model_role"):
        AgentRegistry(subagents_root=subagents_root).discover()


def test_registry_rejects_invalid_frontmatter(tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "broken"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: [broken\n---\nBody\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid YAML frontmatter"):
        SkillRegistry(skills_root=skills_root).discover()


def test_text2sql_domain_registry_cache_refreshes_when_domain_changes(tmp_path):
    domain_root = tmp_path / "domains"
    domain_dir = domain_root / "demo"
    domain_dir.mkdir(parents=True)
    domain_path = domain_dir / "DOMAIN.md"
    domain_path.write_text(
        "---\nname: demo\ndescription: First\ntable: resources\n---\nWorkflow\n",
        encoding="utf-8",
    )
    registry = Text2SQLDomainRegistry(domain_root)

    first = registry.get_domain("demo")
    domain_path.write_text(
        "---\nname: demo\ndescription: Second\ntable: resources\n---\nWorkflow changed\n",
        encoding="utf-8",
    )
    refreshed = registry.get_domain("demo")
    registry.invalidate()
    after_invalidate = registry.get_domain("demo")

    assert first.description == "First"
    assert refreshed.description == "Second"
    assert after_invalidate.description == "Second"
