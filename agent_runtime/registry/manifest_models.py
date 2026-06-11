"""Manifest data models shared by skill and subagent registries."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ManifestExecution:
    mode: str = "inline"
    max_turns: int | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class ManifestMemory:
    namespaces: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ManifestDomains:
    root: str = ""
    file: str = ""


@dataclass(frozen=True)
class ManifestModel:
    llm: str = ""
    llm_base_url: str = ""
    embedding: str = ""
    embedding_base_url: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManifestExtension:
    module: str = ""


@dataclass(frozen=True)
class ManifestOutputContract:
    format: str = ""
    required_fields: list[str] = field(default_factory=list)
    artifact_types: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SuggestedQuestion:
    text: str
    reason: str = ""


@dataclass(frozen=True)
class ManifestBase:
    name: str
    description: str
    location: Path
    kind: str
    body: str = ""
    execution: ManifestExecution = field(default_factory=ManifestExecution)
    tools: list[str] = field(default_factory=list)
    memory: ManifestMemory = field(default_factory=ManifestMemory)
    domains: ManifestDomains = field(default_factory=ManifestDomains)
    model: ManifestModel = field(default_factory=ManifestModel)
    extension: ManifestExtension = field(default_factory=ManifestExtension)
    capabilities: list[str] = field(default_factory=list)
    policies: dict[str, Any] = field(default_factory=dict)
    output_contract: ManifestOutputContract = field(default_factory=ManifestOutputContract)
    routing_hints: list[str] = field(default_factory=list)
    suggested_questions: list[SuggestedQuestion] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Skill(ManifestBase):
    """A loadable skill document under skills/*/SKILL.md."""


@dataclass(frozen=True)
class AgentManifest(ManifestBase):
    """A delegated subagent manifest under subagents/*/AGENT.yaml."""


SkillExecution = ManifestExecution
SkillMemory = ManifestMemory
SkillDomains = ManifestDomains
SkillModel = ManifestModel
SkillExtension = ManifestExtension
SkillOutputContract = ManifestOutputContract


__all__ = [
    "AgentManifest",
    "ManifestBase",
    "ManifestDomains",
    "ManifestExecution",
    "ManifestExtension",
    "ManifestMemory",
    "ManifestModel",
    "ManifestOutputContract",
    "Skill",
    "SkillDomains",
    "SkillExecution",
    "SkillExtension",
    "SkillMemory",
    "SkillModel",
    "SkillOutputContract",
    "SuggestedQuestion",
]
