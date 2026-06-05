"""Tests for Text2SQL domain catalog loading and formatting."""

from pathlib import Path

import pytest

from agent_runtime.registry.skill_registry import AgentRegistry
from subagents.text2sql.core.domain_catalog import Text2SQLDomainCatalog
from subagents.text2sql.core.sql_safety import validate_sql_uses_selected_schema


def _domains():
    registry = AgentRegistry(subagents_root=Path("subagents"))
    return Text2SQLDomainCatalog.from_agent(registry.get("text2sql")).list_domains()


def test_domain_catalog_loads_single_yaml_file():
    domains = {domain.name: domain for domain in _domains()}

    assert set(domains) == {"idc_resources", "sea_cable_faults"}
    assert domains["idc_resources"].table == "resources"
    assert "machine_room" in domains["idc_resources"].text_fields
    assert domains["sea_cable_faults"].table == "sea_cable_faults"
    assert "sea_cable_no" in domains["sea_cable_faults"].text_fields


def test_domain_catalog_loads_business_metrics_and_notes():
    idc = next(domain for domain in _domains() if domain.name == "idc_resources")

    metrics = {metric.name: metric for metric in idc.business_metrics}

    assert metrics["idle_cabinet_count"].filters == {"operation_status": "空闲"}
    assert metrics["available_cabinet_count"].filters == {
        "cabinet_business_status": "Available"
    }
    assert "空闲机柜" in idc.notes


def test_domain_catalog_formats_lightweight_prompt_context():
    registry = AgentRegistry(subagents_root=Path("subagents"))
    catalog = Text2SQLDomainCatalog.from_agent(registry.get("text2sql"))

    prompt_context = catalog.format_domains_for_prompt()

    assert '<domain name="idc_resources"' in prompt_context
    assert 'table="resources"' in prompt_context
    assert "machine_room" not in prompt_context


def test_sql_validation_rejects_fields_outside_selected_schema():
    with pytest.raises(ValueError, match="outside the selected schema"):
        validate_sql_uses_selected_schema(
            "SELECT hallucinated_field FROM resources",
            selected_columns=["machine_room"],
            allowed_tables=["resources"],
        )


def test_sql_validation_allows_selected_fields_and_aliases():
    validate_sql_uses_selected_schema(
        "SELECT machine_room, COUNT(*) AS cnt FROM resources GROUP BY machine_room ORDER BY cnt DESC LIMIT 10",
        selected_columns=["machine_room"],
        allowed_tables=["resources"],
    )
