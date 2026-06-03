from __future__ import annotations

import json
import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime.registry.skill_registry import AgentManifest, AgentRegistry


@dataclass(frozen=True)
class PresetQuestionGroup:
    domain_name: str
    title: str
    questions: list[str]


@dataclass(frozen=True)
class PresetQuestionResult:
    groups: list[PresetQuestionGroup]
    source: str
    error: str = ""
    raw_output: str = ""
    domains: list[dict[str, str]] | None = None


def generate_preset_question_groups(
    *,
    skills_root: Path | None = None,
    subagents_root: Path | None = None,
    base_url: str,
    model_name: str,
    api_key: str,
    questions_per_domain: int = 2,
    subagent_names: list[str] | None = None,
    welcome_mode: str = "providers",
    welcome_provider_module: str = "",
    preset_question_groups: list[dict[str, Any]] | None = None,
) -> list[PresetQuestionGroup]:
    return generate_preset_question_result(
        skills_root=skills_root,
        subagents_root=subagents_root,
        base_url=base_url,
        model_name=model_name,
        api_key=api_key,
        questions_per_domain=questions_per_domain,
        subagent_names=subagent_names,
        welcome_mode=welcome_mode,
        welcome_provider_module=welcome_provider_module,
        preset_question_groups=preset_question_groups,
    ).groups


def generate_preset_question_result(
    *,
    skills_root: Path | None = None,
    subagents_root: Path | None = None,
    base_url: str,
    model_name: str,
    api_key: str,
    questions_per_domain: int = 2,
    subagent_names: list[str] | None = None,
    welcome_mode: str = "providers",
    welcome_provider_module: str = "",
    preset_question_groups: list[dict[str, Any]] | None = None,
) -> PresetQuestionResult:
    """Generate homepage preset questions from bot welcome config."""
    mode = (welcome_mode or "providers").strip().lower()
    configured_groups = _coerce_question_groups(preset_question_groups or [])
    root = skills_root or Path("skills")
    agent_root = subagents_root or root.parent / "subagents"
    try:
        manifests = AgentRegistry(subagents_root=agent_root).discover()
        if subagent_names is not None:
            allowed = set(subagent_names)
            manifests = [manifest for manifest in manifests if manifest.name in allowed]
    except Exception as exc:
        return PresetQuestionResult(
            groups=[],
            source="fallback",
            error=f"{type(exc).__name__}: {exc}",
            domains=[],
        )

    groups: list[PresetQuestionGroup] = []
    capabilities: list[dict[str, str]] = []
    errors: list[str] = []
    sources: list[str] = []
    raw_outputs: list[str] = []

    if configured_groups:
        groups.extend(configured_groups)
        sources.append("config")

    if mode == "static":
        capabilities = [_capability_summary(manifest) for manifest in manifests]
        return PresetQuestionResult(
            groups=groups,
            source="config" if groups else _merge_sources([], groups, capabilities),
            domains=capabilities,
        )

    provider_module = (welcome_provider_module or "").strip()
    if provider_module:
        try:
            result = _run_provider(
                provider_module=provider_module,
                manifests=manifests,
                base_url=base_url,
                model_name=model_name,
                api_key=api_key,
                questions_per_domain=questions_per_domain,
            )
        except Exception as exc:
            errors.append(f"{provider_module}: {type(exc).__name__}: {exc}")
            capabilities.extend(_capability_summary(manifest) for manifest in manifests)
        else:
            groups.extend(result.groups)
            capabilities.extend(
                result.domains or [_capability_summary(manifest) for manifest in manifests]
            )
            if result.error:
                errors.append(result.error)
            if result.source:
                sources.append(result.source)
            if result.raw_output:
                raw_outputs.append(result.raw_output)
    else:
        capabilities.extend(_capability_summary(manifest) for manifest in manifests)

    source = _merge_sources(sources, groups, capabilities)
    return PresetQuestionResult(
        groups=groups,
        source=source,
        error="; ".join(errors),
        raw_output="\n\n".join(raw_outputs),
        domains=capabilities,
    )


def format_welcome_message(
    groups: list[PresetQuestionGroup],
    domains: list[dict[str, str]] | None = None,
) -> str:
    if not groups:
        lines = ["你好，我可以回答已接入能力范围内的问题。"]
        if domains:
            lines.append("\n当前已接入的数据域或能力：")
            for domain in domains:
                description = domain.get("description") or domain.get("name") or ""
                name = domain.get("name") or ""
                label = f"`{name}`" if name else ""
                if description and description != name:
                    label = f"{label}：{description}" if label else description
                lines.append(f"- {label}")
        return "\n".join(lines)

    lines = ["你好，我可以回答已接入能力范围内的问题。\n", "试试问我："]
    for group in groups:
        title = _short_title(group.title)
        lines.append(f"\n**{title}**")
        for question in group.questions:
            lines.append(f"- {question}")
    return "\n".join(lines)


def _run_provider(
    *,
    provider_module: str,
    manifests: list[AgentManifest],
    base_url: str,
    model_name: str,
    api_key: str,
    questions_per_domain: int,
) -> PresetQuestionResult:
    module = importlib.import_module(provider_module)
    generator = getattr(module, "generate_preset_question_result", None)
    if generator is None:
        raise ValueError(
            f"Welcome provider {provider_module} is missing generate_preset_question_result"
        )
    result = generator(
        manifests=manifests,
        manifest=manifests[0] if len(manifests) == 1 else None,
        base_url=base_url,
        model_name=model_name,
        api_key=api_key,
        questions_per_domain=questions_per_domain,
    )
    if isinstance(result, PresetQuestionResult):
        return result
    if isinstance(result, dict):
        return PresetQuestionResult(
            groups=_coerce_question_groups(result.get("groups", [])),
            source=str(result.get("source") or "provider"),
            error=str(result.get("error") or ""),
            raw_output=str(result.get("raw_output") or ""),
            domains=_coerce_dict_list(result.get("domains")),
        )
    raise TypeError(f"Welcome provider {provider_module} returned {type(result).__name__}")


def _capability_summary(manifest: AgentManifest) -> dict[str, str]:
    return {"name": manifest.name, "description": manifest.description}


def _merge_sources(
    sources: list[str],
    groups: list[PresetQuestionGroup],
    capabilities: list[dict[str, str]],
) -> str:
    if any(source == "model" for source in sources):
        return "model"
    if any(source == "config" for source in sources):
        return "config"
    if groups:
        return "provider"
    if capabilities:
        return "capabilities"
    return "empty"


def _domain_summaries(domains: list[Any]) -> list[dict[str, str]]:
    return [
        {
            "name": str(domain.name),
            "description": str(domain.description or ""),
        }
        for domain in domains
    ]


def _parse_question_groups(content: str) -> list[PresetQuestionGroup]:
    text = _extract_json_text(content)
    payload = json.loads(text)
    raw_groups = payload.get("domains", []) if isinstance(payload, dict) else []
    if not raw_groups and isinstance(payload, dict):
        raw_groups = payload.get("skills", [])
    groups: list[PresetQuestionGroup] = []
    for item in raw_groups:
        if not isinstance(item, dict):
            continue
        domain_name = str(item.get("domain_name") or item.get("skill_name") or "").strip()
        title = str(item.get("title", domain_name)).strip()
        questions = [
            _clean_question(question)
            for question in item.get("questions", [])
            if isinstance(question, str) and _clean_question(question)
        ]
        if domain_name and questions:
            groups.append(
                PresetQuestionGroup(
                    domain_name=domain_name,
                    title=title or domain_name,
                    questions=questions,
                )
            )
    return groups


def _coerce_question_groups(value: Any) -> list[PresetQuestionGroup]:
    if not isinstance(value, list):
        return []
    groups: list[PresetQuestionGroup] = []
    for item in value:
        if isinstance(item, PresetQuestionGroup):
            groups.append(item)
        elif isinstance(item, dict):
            questions = [
                _clean_question(question)
                for question in item.get("questions", [])
                if isinstance(question, str) and _clean_question(question)
            ]
            domain_name = str(item.get("domain_name") or item.get("name") or "").strip()
            if domain_name and questions:
                groups.append(
                    PresetQuestionGroup(
                        domain_name=domain_name,
                        title=str(item.get("title") or domain_name),
                        questions=questions,
                    )
                )
    return groups


def _coerce_dict_list(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, list):
        return None
    items: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "name": str(item.get("name") or ""),
                "description": str(item.get("description") or ""),
            }
        )
    return items


def _extract_json_text(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    return match.group(0) if match else text


def _clean_question(question: str) -> str:
    return question.strip().lstrip("-•0123456789.、)） ").strip()


def _short_title(description: str) -> str:
    title = description.strip()
    title = title.removeprefix("回答关于").split("的数据问题", 1)[0]
    title = title.split("，", 1)[0].strip("。 ")
    return title or description.strip()
