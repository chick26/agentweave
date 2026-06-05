"""Read-only SQL validation helpers for the Text2SQL subagent."""

from __future__ import annotations

import re


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


def validate_sql_uses_selected_schema(
    sql: str,
    *,
    selected_columns: list[str],
    allowed_tables: list[str],
) -> None:
    """Fail fast when generated SQL references fields outside the active schema."""
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
    sql = re.sub(r"--.*?$", " ", sql, flags=re.MULTILINE)
    return re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)


def _mask_quoted_literals(sql: str) -> str:
    sql = re.sub(r"'(?:''|[^'])*'", "''", sql)
    return re.sub(r'"(?:\"\"|[^"])*"', '""', sql)
