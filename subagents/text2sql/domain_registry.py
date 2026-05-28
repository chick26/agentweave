from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime.common import file_signature, split_frontmatter, xml_escape
from agent_runtime.skill_registry import AgentManifest


@dataclass(frozen=True)
class BusinessMetric:
    """Domain-owned business metric used as a preferred planning assumption."""

    name: str
    description: str = ""
    phrases: list[str] = field(default_factory=list)
    aggregation: str = "count"
    unit: str = ""
    filters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DomainConfig:
    """Text2SQL-internal table/domain configuration."""

    name: str
    description: str
    location: Path
    body: str
    table: str = ""
    text_fields: list[str] = field(default_factory=list)
    field_descriptions: dict[str, str] = field(default_factory=dict)
    business_metrics: list[BusinessMetric] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Text2SQLDomainRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._cache: list[DomainConfig] | None = None

    @classmethod
    def from_agent(cls, manifest: AgentManifest) -> "Text2SQLDomainRegistry":
        root = manifest.domains.root or "domains"
        path = Path(root).expanduser()
        if not path.is_absolute():
            path = manifest.location.parent / path
        return cls(path)

    def list_domains(self) -> list[DomainConfig]:
        paths = self._domain_paths()
        signature = file_signature(paths)
        if self._cache is not None and self._cache_signature == signature:
            return list(self._cache)
        if not paths:
            self._cache = []
            self._cache_signature = signature
            return []
        domains = [
            self._read_domain_config(path)
            for path in paths
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

    def _read_domain_config(self, path: Path) -> DomainConfig:
        text = path.read_text(encoding="utf-8")
        metadata, body = split_frontmatter(text)
        raw_text_fields = metadata.get("text_fields", [])
        if isinstance(raw_text_fields, str):
            text_fields = [item.strip() for item in raw_text_fields.split(",") if item.strip()]
        else:
            text_fields = [str(item) for item in raw_text_fields]
        field_descriptions = metadata.get("field_descriptions", {})
        if not isinstance(field_descriptions, dict):
            field_descriptions = {}
        return DomainConfig(
            name=str(metadata.get("name") or path.parent.name),
            description=str(metadata.get("description", "")),
            location=path,
            body=body.strip(),
            table=str(metadata.get("table", "")),
            text_fields=text_fields,
            field_descriptions={str(key): str(value) for key, value in field_descriptions.items()},
            business_metrics=_parse_business_metrics(metadata.get("business_metrics", {})),
            metadata=metadata,
        )

    def _domain_paths(self) -> list[Path]:
        if not self.root.exists():
            return []
        paths: list[Path] = []
        for domain_dir in sorted(self.root.iterdir()):
            if not domain_dir.is_dir():
                continue
            domain_path = domain_dir / "DOMAIN.md"
            if domain_path.exists():
                paths.append(domain_path)
        return paths


def build_prompt_context(manifest: AgentManifest) -> dict[str, str]:
    return {
        "domains": Text2SQLDomainRegistry.from_agent(manifest).format_domains_for_prompt(),
    }


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
        phrases = payload.get("phrases", [])
        if isinstance(phrases, str):
            parsed_phrases = [phrases]
        elif isinstance(phrases, list):
            parsed_phrases = [str(item) for item in phrases if str(item).strip()]
        else:
            parsed_phrases = []
        filters = payload.get("filters", {})
        if not isinstance(filters, dict):
            filters = {}
        metrics.append(
            BusinessMetric(
                name=name,
                description=str(payload.get("description") or ""),
                phrases=parsed_phrases,
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
