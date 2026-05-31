from pathlib import Path

import pytest

from agent_runtime.registry.skill_registry import AgentRegistry
from subagents.text2sql.domain_registry import Text2SQLDomainRegistry
from subagents.text2sql.planning import (
    build_sql_plan_from_parts,
    rank_domain_candidates,
    sql_plan_to_prompt,
    validate_sql_uses_selected_schema,
)


def _domains():
    registry = AgentRegistry(subagents_root=Path("subagents"))
    return Text2SQLDomainRegistry.from_agent(registry.get("text2sql")).list_domains()


def test_domain_routing_prefers_matching_business_terms():
    ranked = rank_domain_candidates("NCP 海缆最近有哪些故障？", _domains())

    assert ranked[0].name == "sea_cable_faults"
    assert ranked[0].score > 0


def test_sql_plan_captures_linked_values_metric_without_implicit_limit():
    domain = next(domain for domain in _domains() if domain.name == "idc_resources")

    plan = build_sql_plan_from_parts(
        question="403机房有多少可用机柜？",
        domain=domain,
        schema_text="表名: resources\n字段:\n- machine_room (text): 机房名称",
        selected_columns=["machine_room", "cabinet_business_status"],
        linked_values='[{"field":"machine_room","value":"Room-403","count":12,"query":"403"}]',
    )

    assert plan.domain == "idc_resources"
    assert plan.table == "resources"
    assert not hasattr(plan, "metric_intent")
    assert not hasattr(plan, "limit")
    assert not hasattr(plan, "confidence")
    metrics = {metric.name: metric for metric in plan.business_metrics}
    assert metrics["available_cabinet_count"].filters == {
        "cabinet_business_status": "Available"
    }
    assert plan.linked_values[0].field == "machine_room"
    assert plan.linked_values[0].value == "Room-403"


def test_sql_plan_does_not_match_business_metric_in_code():
    domain = next(domain for domain in _domains() if domain.name == "idc_resources")

    plan = build_sql_plan_from_parts(
        question="哪些机柜不是空闲的？",
        domain=domain,
        schema_text="表名: resources",
        selected_columns=["machine_room", "operation_status"],
        linked_values='[{"field":"machine_room","value":"Room-403","count":12,"query":"403"}]',
    )

    assert not hasattr(plan, "business_metric")
    assert any(metric.name == "idle_cabinet_count" for metric in plan.business_metrics)


def test_idc_domain_loads_business_metrics():
    domain = next(domain for domain in _domains() if domain.name == "idc_resources")

    metrics = {metric.name: metric for metric in domain.business_metrics}

    assert metrics["idle_cabinet_count"].filters == {"operation_status": "空闲"}
    assert metrics["available_cabinet_count"].filters == {
        "cabinet_business_status": "Available"
    }


def test_sql_plan_infers_ranking_limit():
    domain = next(domain for domain in _domains() if domain.name == "sea_cable_faults")

    plan = build_sql_plan_from_parts(
        question="故障次数最多的前3条海缆是什么？",
        domain=domain,
        schema_text="表名: sea_cable_faults",
        selected_columns=["sea_cable_no", "fault_id"],
    )

    assert not hasattr(plan, "metric_intent")
    assert not hasattr(plan, "limit")
    assert not hasattr(plan, "display_intent")


def test_sql_plan_does_not_add_limit_without_explicit_n():
    domain = next(domain for domain in _domains() if domain.name == "idc_resources")

    plan = build_sql_plan_from_parts(
        question="查询中国联通（香港）将军澳智云数据中心下所有机房的可用机柜数量，按机房分组，按可用机柜数量从高到低排序",
        domain=domain,
        schema_text="表名: resources",
        selected_columns=["machine_room", "cabinet", "cabinet_business_status"],
    )

    assert not hasattr(plan, "metric_intent")
    assert not hasattr(plan, "limit")


def test_sql_plan_prompt_omits_duplicate_schema_text():
    domain = next(domain for domain in _domains() if domain.name == "idc_resources")

    plan = build_sql_plan_from_parts(
        question="403机房有哪些机柜？",
        domain=domain,
        schema_text="表名: resources\n字段:\n- machine_room (text): 机房名称",
        selected_columns=["machine_room", "cabinet"],
    )

    prompt = sql_plan_to_prompt(plan)

    assert "selected_schema" not in prompt
    assert "表名: resources" not in prompt
    assert '"selected_columns"' in prompt


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
