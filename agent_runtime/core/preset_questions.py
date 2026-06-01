from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from agent_runtime.registry.skill_registry import AgentRegistry
from subagents.text2sql.domain_registry import Text2SQLDomainRegistry


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
) -> list[PresetQuestionGroup]:
    return generate_preset_question_result(
        skills_root=skills_root,
        subagents_root=subagents_root,
        base_url=base_url,
        model_name=model_name,
        api_key=api_key,
        questions_per_domain=questions_per_domain,
    ).groups


def generate_preset_question_result(
    *,
    skills_root: Path | None = None,
    subagents_root: Path | None = None,
    base_url: str,
    model_name: str,
    api_key: str,
    questions_per_domain: int = 2,
) -> PresetQuestionResult:
    """Generate homepage preset questions from Text2SQL domain metadata."""
    domains = _load_text2sql_domains(
        skills_root=skills_root,
        subagents_root=subagents_root,
    )
    if not domains:
        return PresetQuestionResult(groups=[], source="empty", domains=[])
    domain_summaries = _domain_summaries(domains)

    raw_content = ""
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=8.0)
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是数据问答产品的示例问题生成器。"
                        "根据每个 Text2SQL domain 的 YAML 元数据和说明，为每个 domain 生成简短、真实、"
                        "用户会直接输入的中文问数问题。只输出 JSON。"
                        "不要输出思考过程，不要输出 <think> 标签。"
                    ),
                },
                {
                    "role": "user",
                    "content": _build_generation_prompt(domains, questions_per_domain),
                },
            ],
            temperature=0.2,
            max_tokens=1024,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw_content = response.choices[0].message.content or ""
        generated = _parse_question_groups(raw_content)
        if generated:
            return PresetQuestionResult(
                groups=generated,
                source="model",
                raw_output=raw_content,
                domains=domain_summaries,
            )
        return PresetQuestionResult(
            groups=[],
            source="fallback",
            error="模型返回内容未解析出有效问题。",
            raw_output=raw_content,
            domains=domain_summaries,
        )
    except Exception as exc:
        return PresetQuestionResult(
            groups=[],
            source="fallback",
            error=f"{type(exc).__name__}: {exc}",
            raw_output=raw_content,
            domains=domain_summaries,
        )


def format_welcome_message(
    groups: list[PresetQuestionGroup],
    domains: list[dict[str, str]] | None = None,
) -> str:
    if not groups:
        lines = ["你好，我可以回答已接入数据领域的问数问题。"]
        if domains:
            lines.append("\n当前已接入的数据域：")
            for domain in domains:
                description = domain.get("description") or domain.get("name") or ""
                name = domain.get("name") or ""
                label = f"`{name}`" if name else ""
                if description and description != name:
                    label = f"{label}：{description}" if label else description
                lines.append(f"- {label}")
        return "\n".join(lines)

    lines = ["你好，我可以回答已接入数据领域的问数问题。\n", "试试问我："]
    for group in groups:
        title = _short_title(group.title)
        lines.append(f"\n**{title}**")
        for question in group.questions:
            lines.append(f"- {question}")
    return "\n".join(lines)


def _load_text2sql_domains(
    *,
    skills_root: Path | None,
    subagents_root: Path | None = None,
) -> list:
    root = skills_root or Path("skills")
    agent_root = subagents_root or root.parent / "subagents"
    manifest = AgentRegistry(subagents_root=agent_root).get("text2sql")
    return Text2SQLDomainRegistry.from_agent(manifest).list_domains()


def _domain_summaries(domains: list) -> list[dict[str, str]]:
    return [
        {
            "name": str(domain.name),
            "description": str(domain.description or ""),
        }
        for domain in domains
    ]


def _build_generation_prompt(domains: list, questions_per_domain: int) -> str:
    payload = []
    for domain in domains:
        payload.append(
            {
                "name": domain.name,
                "description": domain.description,
                "table": domain.table,
                "text_fields": domain.text_fields,
                "field_descriptions": domain.field_descriptions,
                "workflow_excerpt": domain.body[:1200],
            }
        )
    return (
        f"请为每个 Text2SQL domain 生成 {questions_per_domain} 个预设问题。\n"
        "要求：\n"
        "- 问题必须适合直接作为用户输入。\n"
        "- 优先覆盖计数、排行、状态、时间/数值聚合等典型问数。\n"
        "- 不要编造过细的字段值，除非字段描述里明确给了示例值。\n"
        "- 输出严格 JSON，不要 Markdown，不要解释。\n"
        "JSON 格式："
        '{"domains":[{"domain_name":"...","title":"...","questions":["..."]}]}\n\n'
        f"domains:\n{json.dumps(payload, ensure_ascii=False)}"
    )


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
