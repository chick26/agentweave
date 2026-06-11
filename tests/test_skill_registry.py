"""Tests for skill and subagent manifest parsing and validation."""

from pathlib import Path

import pytest

from agent_runtime.registry.agent_registry import AgentRegistry
from agent_runtime.registry.skill_registry import SkillRegistry
from subagents.text2sql.core.domain_catalog import Text2SQLDomainCatalog


def test_agent_registry_loads_subagents_only():
    registry = AgentRegistry(subagents_root=Path("subagents"))

    text2sql = registry.get("text2sql")

    assert [agent.name for agent in registry.discover()] == ["rag", "text2sql"]
    assert text2sql.kind == "subagent"
    assert text2sql.location.name == "AGENT.yaml"
    assert text2sql.execution.mode == "worker"
    assert text2sql.extension.module == "subagents.text2sql.extension"
    assert text2sql.tools == []
    assert text2sql.capabilities == ["db.readonly", "schema.inspect", "sql.generate"]
    assert text2sql.policies["db"]["readonly"] is True
    assert text2sql.output_contract.format == "json"
    assert text2sql.output_contract.artifact_types == ["sql_result"]
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
    assert agent.tools == []
    assert agent.extension.module == "subagents.text2sql.extension"
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
        "extension:\n"
        "  module: subagents.demo.extension\n"
        "model:\n"
        "  llm: demo-chat\n"
        "  llm_base_url: http://llm/v1\n"
        "  embedding: demo-embedding\n"
        "  embedding_base_url: http://embedding/v1\n"
        "  extra_body:\n"
        "    temperature: 0\n"
        "capabilities:\n"
        "  - demo.capability\n"
        "policies:\n"
        "  demo:\n"
        "    enabled: true\n"
        "output_contract:\n"
        "  format: json\n"
        "  required_fields:\n"
        "    - answer\n"
        "  artifact_types:\n"
        "    - demo_artifact\n"
        "suggested_questions:\n"
        "  - text: 演示问题\n"
        "    reason: 演示原因\n",
        encoding="utf-8",
    )
    (agent_dir / "prompt.md").write_text("Prompt body", encoding="utf-8")

    agent = AgentRegistry(subagents_root=subagents_root).get("demo")

    assert agent.body == "Prompt body"
    assert agent.extension.module == "subagents.demo.extension"
    assert agent.model.llm == "demo-chat"
    assert agent.model.llm_base_url == "http://llm/v1"
    assert agent.model.embedding == "demo-embedding"
    assert agent.model.embedding_base_url == "http://embedding/v1"
    assert agent.model.extra_body == {"temperature": 0}
    assert agent.capabilities == ["demo.capability"]
    assert agent.policies == {"demo": {"enabled": True}}
    assert agent.output_contract.format == "json"
    assert agent.output_contract.required_fields == ["answer"]
    assert agent.output_contract.artifact_types == ["demo_artifact"]
    assert agent.suggested_questions[0].text == "演示问题"
    assert agent.suggested_questions[0].reason == "演示原因"


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


def test_worker_subagent_accepts_worker_mode_without_model_role(tmp_path):
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

    assert manifest.execution.mode == "worker"


def test_worker_subagent_rejects_removed_model_role_field(tmp_path):
    subagents_root = tmp_path / "subagents"
    agent_dir = subagents_root / "demo"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.yaml").write_text(
        "name: demo\n"
        "description: Wrong role\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: executor\n",
        encoding="utf-8",
    )
    (agent_dir / "prompt.md").write_text("Body\n", encoding="utf-8")

    with pytest.raises(ValueError, match="execution.model_role"):
        AgentRegistry(subagents_root=subagents_root).get("demo")


def test_subagent_manifest_rejects_invalid_policy_shape(tmp_path):
    subagents_root = tmp_path / "subagents"
    agent_dir = subagents_root / "demo"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.yaml").write_text(
        "name: demo\n"
        "description: Bad policies\n"
        "execution:\n"
        "  mode: worker\n"
        "policies:\n"
        "  - not-a-map\n",
        encoding="utf-8",
    )
    (agent_dir / "prompt.md").write_text("Body\n", encoding="utf-8")

    with pytest.raises(ValueError, match="policies"):
        AgentRegistry(subagents_root=subagents_root).get("demo")


def test_extension_only_subagent_does_not_require_tools_py(tmp_path):
    subagents_root = tmp_path / "subagents"
    agent_dir = subagents_root / "demo"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.yaml").write_text(
        "name: demo\n"
        "description: Extension-only worker\n"
        "execution:\n"
        "  mode: worker\n"
        "extension:\n"
        "  module: subagents.demo.extension\n",
        encoding="utf-8",
    )
    (agent_dir / "prompt.md").write_text("Body\n", encoding="utf-8")

    manifest = AgentRegistry(subagents_root=subagents_root).get("demo")

    assert manifest.extension.module == "subagents.demo.extension"
    assert manifest.tools == []


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
