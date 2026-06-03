from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_runtime.common import file_signature, xml_escape
from agent_runtime.registry.skill_registry import AgentManifest


@dataclass(frozen=True)
class BusinessMetric:
    """Domain-owned business metric used as a preferred SQL assumption."""

    name: str
    description: str = ""
    phrases: list[str] = field(default_factory=list)
    aggregation: str = "count"
    unit: str = ""
    filters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DomainConfig:
    """Text2SQL table/domain configuration from domain_catalog.yaml."""

    name: str
    description: str
    table: str
    text_fields: list[str] = field(default_factory=list)
    field_descriptions: dict[str, str] = field(default_factory=dict)
    business_metrics: list[BusinessMetric] = field(default_factory=list)
    notes: str = ""
    location: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Text2SQLDomainCatalog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._cache: list[DomainConfig] | None = None

    @classmethod
    def from_agent(cls, manifest: AgentManifest) -> "Text2SQLDomainCatalog":
        catalog_file = manifest.domains.file or "domain_catalog.yaml"
        path = Path(catalog_file).expanduser()
        if not path.is_absolute():
            path = manifest.location.parent / path
        return cls(path)

    def list_domains(self) -> list[DomainConfig]:
        signature = file_signature([self.path])
        if self._cache is not None and self._cache_signature == signature:
            return list(self._cache)
        if not self.path.exists():
            raise FileNotFoundError(f"Text2SQL domain catalog not found: {self.path}")
        payload = _read_yaml_mapping(self.path)
        raw_domains = payload.get("domains", [])
        if not isinstance(raw_domains, list):
            raise ValueError(f"Invalid Text2SQL domain catalog {self.path}: domains must be a list")
        domains = [
            _parse_domain(item, location=self.path)
            for item in raw_domains
            if isinstance(item, dict)
        ]
        self._cache = domains
        self._cache_signature = signature
        return list(domains)

    def invalidate(self) -> None:
        self._cache = None
        self._cache_signature = None

    def get_domain(self, name: str) -> DomainConfig:
        for domain in self.list_domains():
            if domain.name == name:
                return domain
        raise ValueError(f"Unknown Text2SQL domain: {name}")

    def format_domains_for_prompt(self) -> str:
        domains = self.list_domains()
        if not domains:
            return "<domains></domains>"
        lines = ["<domains>"]
        for domain in domains:
            lines.append(
                f'  <domain name="{xml_escape(domain.name)}" '
                f'description="{xml_escape(domain.description)}" '
                f'table="{xml_escape(domain.table)}" />'
            )
        lines.append("</domains>")
        return "\n".join(lines)


def build_prompt_context(manifest: AgentManifest) -> dict[str, str]:
    return {
        "domains": Text2SQLDomainCatalog.from_agent(manifest).format_domains_for_prompt(),
    }


def business_metrics_to_prompt(metrics: list[BusinessMetric]) -> list[dict[str, Any]]:
    return [
        {
            "name": metric.name,
            "description": metric.description,
            "phrases": list(metric.phrases),
            "aggregation": metric.aggregation,
            "unit": metric.unit,
            "filters": dict(metric.filters),
        }
        for metric in metrics
    ]


def domain_schema_payload(
    *,
    domain: DomainConfig,
    schema_text: str,
    columns: list[str],
) -> dict[str, Any]:
    return {
        "domain": domain.name,
        "description": domain.description,
        "table": domain.table,
        "schema": schema_text,
        "columns": list(columns),
        "text_fields": list(domain.text_fields),
        "field_descriptions": dict(domain.field_descriptions),
        "business_metrics": business_metrics_to_prompt(domain.business_metrics),
        "notes": domain.notes,
        "error": "",
    }


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid Text2SQL domain catalog {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid Text2SQL domain catalog {path}: expected a mapping")
    return payload


def _parse_domain(payload: dict[str, Any], *, location: Path) -> DomainConfig:
    name = str(payload.get("name") or "").strip()
    table = str(payload.get("table") or "").strip()
    if not name:
        raise ValueError(f"Invalid Text2SQL domain catalog {location}: domain name is required")
    if not table:
        raise ValueError(f"Invalid Text2SQL domain catalog {location}: table is required for {name}")
    field_descriptions = payload.get("field_descriptions", {})
    if not isinstance(field_descriptions, dict):
        field_descriptions = {}
    return DomainConfig(
        name=name,
        description=str(payload.get("description") or ""),
        table=table,
        text_fields=_as_str_list(payload.get("text_fields", [])),
        field_descriptions={str(key): str(value) for key, value in field_descriptions.items()},
        business_metrics=_parse_business_metrics(payload.get("business_metrics", {})),
        notes=str(payload.get("notes") or ""),
        location=location,
        metadata=payload,
    )


def _parse_business_metrics(raw: Any) -> list[BusinessMetric]:
    if not raw:
        return []
    items: list[tuple[str, Any]]
    if isinstance(raw, dict):
        items = [(str(name), payload) for name, payload in raw.items()]
    elif isinstance(raw, list):
        items = [
            (str(item.get("name") or f"metric_{index}"), item)
            for index, item in enumerate(raw, start=1)
            if isinstance(item, dict)
        ]
    else:
        return []

    metrics: list[BusinessMetric] = []
    for name, payload in items:
        if not isinstance(payload, dict):
            continue
        filters = payload.get("filters", {})
        if not isinstance(filters, dict):
            filters = {}
        metrics.append(
            BusinessMetric(
                name=name,
                description=str(payload.get("description") or ""),
                phrases=_as_str_list(payload.get("phrases", [])),
                aggregation=str(payload.get("aggregation") or "count"),
                unit=str(payload.get("unit") or ""),
                filters={
                    str(key): str(value)
                    for key, value in filters.items()
                    if str(key).strip() and value is not None
                },
            )
        )
    return metrics


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []
