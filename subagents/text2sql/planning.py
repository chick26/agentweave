from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from subagents.text2sql.domain_registry import BusinessMetric, DomainConfig


class LinkedValue(BaseModel):
    field: str
    value: str
    count: int | None = None
    source: str = "search_values"
    query: str = ""


class BusinessMetricPlan(BaseModel):
    name: str = ""
    description: str = ""
    phrases: list[str] = Field(default_factory=list)
    aggregation: str = "count"
    unit: str = ""
    filters: dict[str, str] = Field(default_factory=dict)
    source: str = "domain.business_metrics"


class SQLPlan(BaseModel):
    """Structured plan passed to the SQL generation model."""

    question: str
    domain: str
    table: str
    selected_columns: list[str] = Field(default_factory=list)
    selected_schema: str = ""
    linked_values: list[LinkedValue] = Field(default_factory=list)
    business_metrics: list[BusinessMetricPlan] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class DomainScore(BaseModel):
    name: str
    score: int
    matched_terms: list[str] = Field(default_factory=list)


SQL_KEYWORDS = {
    "all", "and", "as", "asc", "between", "by", "case", "cast", "desc", "distinct",
    "else", "end", "from", "group", "having", "in", "is", "join", "left", "like",
    "false", "limit", "not", "null", "on", "or", "order", "outer", "real", "right",
    "select", "text", "then", "true", "when", "where", "with",
}

SQL_FUNCTIONS = {
    "avg", "coalesce", "count", "date", "datetime", "ifnull", "instr", "lower",
    "max", "min", "strftime", "sum", "upper",
}


def rank_domain_candidates(question: str, domains: list[DomainConfig]) -> list[DomainScore]:
    """Rank domains with deterministic lexical matching.

    This is intentionally lightweight for V1. It gives the subagent a stable
    routing aid without introducing a vector store dependency.
    """
    normalized_question = _normalize_for_match(question)
    scores: list[DomainScore] = []
    for domain in domains:
        weighted_terms: list[tuple[str, int]] = [
            (domain.name, 4),
            (domain.description, 3),
            (domain.table, 3),
        ]
        weighted_terms.extend((field, 2) for field in domain.text_fields)
        weighted_terms.extend((desc, 2) for desc in domain.field_descriptions.values())
        matched_terms: list[str] = []
        score = 0
        for text, weight in weighted_terms:
            for term in _candidate_terms(text):
                if term and term in normalized_question:
                    score += weight
                    matched_terms.append(term)
        scores.append(
            DomainScore(
                name=domain.name,
                score=score,
                matched_terms=_dedupe_preserve_order(matched_terms)[:8],
            )
        )
    return sorted(scores, key=lambda item: (-item.score, item.name))


def build_sql_plan_from_parts(
    *,
    question: str,
    domain: DomainConfig,
    schema_text: str,
    selected_columns: list[str],
    linked_values: Any = None,
    constraints: Any = None,
) -> SQLPlan:
    parsed_linked_values = parse_linked_values(linked_values)
    return SQLPlan(
        question=question.strip(),
        domain=domain.name,
        table=domain.table,
        selected_columns=list(selected_columns),
        selected_schema=schema_text,
        linked_values=parsed_linked_values,
        business_metrics=business_metrics_to_plan(domain.business_metrics),
        constraints=parse_constraints(constraints),
    )


def parse_linked_values(value: Any) -> list[LinkedValue]:
    if value in (None, "", []):
        return []
    payload = _load_json_if_string(value)
    if isinstance(payload, dict):
        if isinstance(payload.get("linked_values"), list):
            payload = payload["linked_values"]
        elif "field" in payload and "value" in payload:
            payload = [payload]
        else:
            payload = []
    if not isinstance(payload, list):
        return []
    linked: list[LinkedValue] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        raw_value = item.get("value")
        if not field or raw_value is None:
            continue
        count = item.get("count")
        try:
            parsed_count = int(count) if count is not None else None
        except (TypeError, ValueError):
            parsed_count = None
        linked.append(
            LinkedValue(
                field=field,
                value=str(raw_value),
                count=parsed_count,
                source=str(item.get("source") or "search_values"),
                query=str(item.get("query") or ""),
            )
        )
    return linked


def parse_constraints(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    payload = _load_json_if_string(value)
    if isinstance(payload, str):
        return [payload.strip()] if payload.strip() else []
    if isinstance(payload, list):
        return [str(item).strip() for item in payload if str(item).strip()]
    if isinstance(payload, dict):
        return [
            f"{key}: {val}"
            for key, val in payload.items()
            if str(key).strip() and str(val).strip()
        ]
    return [str(payload).strip()]


def business_metrics_to_plan(metrics: list[BusinessMetric]) -> list[BusinessMetricPlan]:
    return [
        BusinessMetricPlan(
            name=metric.name,
            description=metric.description,
            phrases=list(metric.phrases),
            aggregation=metric.aggregation or "count",
            unit=metric.unit,
            filters=dict(metric.filters),
        )
        for metric in metrics
    ]


def sql_plan_to_prompt(plan: SQLPlan) -> str:
    return json.dumps(
        plan.model_dump(exclude={"selected_schema"}),
        ensure_ascii=False,
        indent=2,
    )


def validate_sql_uses_selected_schema(
    sql: str,
    *,
    selected_columns: list[str],
    allowed_tables: list[str],
) -> None:
    """Fail fast when generated SQL references fields outside selected schema.

    The check is intentionally conservative and complements database execution;
    it catches common hallucinated-field errors before the backend runs SQL.
    """
    selected = set(selected_columns)
    tables = set(allowed_tables)
    if not selected:
        return
    identifiers = _extract_identifier_candidates(sql)
    aliases = _extract_aliases(sql, tables)
    allowed = selected | tables | aliases | SQL_KEYWORDS | SQL_FUNCTIONS
    unknown = sorted(
        identifier
        for identifier in identifiers
        if identifier.lower() not in allowed and identifier not in allowed
    )
    if unknown:
        raise ValueError(
            "SQL references fields outside the selected schema: "
            + ", ".join(unknown)
        )


def _extract_identifier_candidates(sql: str) -> set[str]:
    without_literals = _mask_quoted_literals(_strip_sql_comments(sql))
    quoted = set(re.findall(r'["`]([A-Za-z_][A-Za-z0-9_]*)["`]', without_literals))
    bare = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", without_literals))
    return {identifier for identifier in quoted | bare if identifier}


def _extract_aliases(sql: str, tables: set[str]) -> set[str]:
    text = _mask_quoted_literals(_strip_sql_comments(sql))
    aliases = set(re.findall(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b", text, flags=re.IGNORECASE))
    table_alias_pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+[`\"]?([A-Za-z_][A-Za-z0-9_]*)[`\"]?(?:\s+([A-Za-z_][A-Za-z0-9_]*))?",
        flags=re.IGNORECASE,
    )
    for table, alias in table_alias_pattern.findall(text):
        if table in tables and alias and alias.lower() not in SQL_KEYWORDS:
            aliases.add(alias)
    return aliases


def _strip_sql_comments(sql: str) -> str:
    without_line_comments = re.sub(r"--[^\n\r]*", " ", sql)
    return re.sub(r"/\*.*?\*/", " ", without_line_comments, flags=re.DOTALL)


def _mask_quoted_literals(sql: str) -> str:
    return re.sub(r"'(?:''|[^'])*'", "''", sql)


def _load_json_if_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _candidate_terms(text: str) -> list[str]:
    normalized = _normalize_for_match(text)
    terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", normalized)
    return terms + [normalized] if normalized else terms


def _normalize_for_match(text: str) -> str:
    return str(text).lower().replace(" ", "")


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
