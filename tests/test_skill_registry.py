from pathlib import Path

import pytest

from agent_runtime.registry.skill_registry import AgentRegistry, SkillRegistry
from subagents.text2sql.scripts.domain_catalog import Text2SQLDomainCatalog


def test_agent_registry_loads_subagents_only():
    registry = AgentRegistry(subagents_root=Path("subagents"))

    text2sql = registry.get("text2sql")

    assert [agent.name for agent in registry.discover()] == ["rag", "text2sql"]
    assert text2sql.kind == "subagent"
    assert text2sql.location.name == "AGENT.yaml"
    assert text2sql.execution.mode == "worker"
    assert text2sql.execution.model_role == "orchestrator"
    assert text2sql.runtime_env.setup_module == "subagents.text2sql.env"
    assert text2sql.data.roots == ["subagents/text2sql/data"]
    assert text2sql.data.globs == ["*.csv"]
    assert text2sql.data.tables == {
        "resources": "resources.csv",
        "sea_cable_faults": "sea_cable_faults.csv",
    }
    assert "execute_sql" in text2sql.tools
    assert "get_domain_schema" in text2sql.body
    assert "数据库查询" in text2sql.routing_hints[0]
    assert text2sql.domains.file == "domain_catalog.yaml"


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


def test_text2sql_domain_catalog_loads_subagent_local_domains():
    registry = AgentRegistry(subagents_root=Path("subagents"))
    domain_catalog = Text2SQLDomainCatalog.from_agent(registry.get("text2sql"))
    idc = domain_catalog.get_domain("idc_resources")

    assert idc.location is not None
    assert idc.location.as_posix().endswith("subagents/text2sql/domain_catalog.yaml")
    assert idc.table == "resources"
    assert "machine_room" in idc.text_fields


def test_agent_yaml_reads_manifest_and_prompt_body():
    registry = AgentRegistry(subagents_root=Path("subagents"))

    agent = registry.get("text2sql")

    assert agent.location.name == "AGENT.yaml"
    assert agent.name == "text2sql"
    assert agent.tools == [
        "get_current_time",
        "list_domains",
        "get_domain_schema",
        "search_domain_values",
        "generate_readonly_sql",
        "execute_sql",
    ]
    assert agent.routing_hints
    assert "可用数据域" in agent.body


def test_yaml_agent_manifest_reads_prompt_and_runtime_metadata(tmp_path):
    subagents_root = tmp_path / "subagents"
    agent_dir = subagents_root / "demo"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.yaml").write_text(
        "name: demo\n"
        "description: Demo worker\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: orchestrator\n"
        "runtime_env:\n"
        "  kind: local\n"
        "  setup_module: subagents.demo.env\n"
        "  mode: lazy\n"
        "data:\n"
        "  roots:\n"
        "    - data/demo\n"
        "  globs:\n"
        "    - '*.pdf'\n"
        "  tables:\n"
        "    docs: docs.pdf\n"
        "tools:\n"
        "  - search\n",
        encoding="utf-8",
    )
    (agent_dir / "prompt.md").write_text("Prompt body", encoding="utf-8")
    (agent_dir / "tools.py").write_text("", encoding="utf-8")
    (agent_dir / "ENVIRONMENT.md").write_text("Environment", encoding="utf-8")

    agent = AgentRegistry(subagents_root=subagents_root).get("demo")

    assert agent.body == "Prompt body"
    assert agent.runtime_env.kind == "local"
    assert agent.runtime_env.setup_module == "subagents.demo.env"
    assert agent.runtime_env.mode == "lazy"
    assert agent.data.roots == ["data/demo"]
    assert agent.data.globs == ["*.pdf"]
    assert agent.data.tables == {"docs": "docs.pdf"}


def test_legacy_agent_md_manifest_is_not_a_subagent(tmp_path):
    subagents_root = tmp_path / "subagents"
    agent_dir = subagents_root / "legacy"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text(
        "---\n"
        "name: legacy\n"
        "description: Legacy worker\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: orchestrator\n"
        "---\n"
        "Legacy prompt\n",
        encoding="utf-8",
    )

    registry = AgentRegistry(subagents_root=subagents_root)

    assert registry.discover() == []
    with pytest.raises(ValueError, match="Unknown subagent"):
        registry.get("legacy")


def test_text2sql_prompt_keeps_result_contract_without_tool_permission_noise():
    agent = AgentRegistry(subagents_root=Path("subagents")).get("text2sql")

    assert "get_domain_schema" in agent.body
    assert "generate_readonly_sql" in agent.body
    assert "严格 JSON" in agent.body
    assert "未查询到符合条件的数据" in agent.body
    assert "run_skill" not in agent.body
    assert "memory_search" not in agent.body


def test_agent_registry_cache_refreshes_when_manifest_changes(tmp_path):
    subagents_root = tmp_path / "subagents"
    agent_dir = subagents_root / "demo"
    agent_dir.mkdir(parents=True)
    manifest_path = agent_dir / "AGENT.yaml"
    manifest_path.write_text(
        "name: demo\ndescription: First\nexecution:\n  mode: worker\n",
        encoding="utf-8",
    )
    prompt_path = agent_dir / "prompt.md"
    prompt_path.write_text("Body one", encoding="utf-8")
    registry = AgentRegistry(subagents_root=subagents_root)

    first = registry.get("demo")
    second = registry.get("demo")
    prompt_path.write_text("Body two changed\n", encoding="utf-8")
    refreshed = registry.get("demo")
    registry.invalidate()
    after_invalidate = registry.get("demo")

    assert first.description == "First"
    assert second.description == "First"
    assert refreshed.body == "Body two changed"
    assert after_invalidate.body == "Body two changed"


def test_worker_subagent_defaults_to_orchestrator_model_role(tmp_path):
    subagents_root = tmp_path / "subagents"
    agent_dir = subagents_root / "demo"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.yaml").write_text(
        "name: demo\n"
        "description: Missing role\n"
        "execution:\n"
        "  mode: worker\n",
        encoding="utf-8",
    )
    (agent_dir / "prompt.md").write_text("Body\n", encoding="utf-8")

    manifest = AgentRegistry(subagents_root=subagents_root).get("demo")

    assert manifest.execution.model_role == "orchestrator"


def test_subagent_contract_rejects_missing_prompt(tmp_path):
    subagents_root = tmp_path / "subagents"
    agent_dir = subagents_root / "broken"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.yaml").write_text(
        "name: broken\nexecution:\n  mode: worker\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="prompt.md"):
        AgentRegistry(subagents_root=subagents_root).discover()


def test_subagent_contract_rejects_module_overrides(tmp_path):
    subagents_root = tmp_path / "subagents"
    agent_dir = subagents_root / "broken"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.yaml").write_text(
        "name: broken\n"
        "execution:\n"
        "  mode: worker\n"
        "  tool_module: custom.tools\n",
        encoding="utf-8",
    )
    (agent_dir / "prompt.md").write_text("Body\n", encoding="utf-8")

    with pytest.raises(ValueError, match="tool_module/context_module"):
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


def test_text2sql_domain_catalog_cache_refreshes_when_domain_changes(tmp_path):
    domain_path = tmp_path / "domain_catalog.yaml"
    domain_path.write_text(
        "domains:\n"
        "  - name: demo\n"
        "    description: First\n"
        "    table: resources\n",
        encoding="utf-8",
    )
    catalog = Text2SQLDomainCatalog(domain_path)

    first = catalog.get_domain("demo")
    domain_path.write_text(
        "domains:\n"
        "  - name: demo\n"
        "    description: Second\n"
        "    table: resources\n",
        encoding="utf-8",
    )
    refreshed = catalog.get_domain("demo")
    catalog.invalidate()
    after_invalidate = catalog.get_domain("demo")

    assert first.description == "First"
    assert refreshed.description == "Second"
    assert after_invalidate.description == "Second"
