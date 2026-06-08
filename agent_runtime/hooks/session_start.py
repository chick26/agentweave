"""SessionStart hook for rendering the startup welcome message."""

from __future__ import annotations

from dataclasses import dataclass, field

from openai import OpenAI

from agent_runtime.core.hooks import HookHandler, HookResult
from agent_runtime.registry.bot_registry import DEFAULT_WELCOME_MESSAGE
from agent_runtime.registry.skill_registry import AgentManifest, Skill


@dataclass(frozen=True)
class SessionStartContext:
    welcome_message: str = DEFAULT_WELCOME_MESSAGE
    welcome_preset: bool = False
    welcome_prompt: str = ""
    welcome_model_base_url: str = ""
    welcome_model_name: str = ""
    welcome_model_api_key: str = ""
    memory_context: str = ""
    subagents: list[AgentManifest] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)


class SessionStartHook:
    event_name = "SessionStart"

    def run(self, context: SessionStartContext) -> HookResult:
        resources = [
            *[_resource_summary("subagent", item) for item in context.subagents],
            *[_resource_summary("skill", item) for item in context.skills],
        ]
        source = "descriptions" if resources else "default"
        error = ""
        if context.welcome_preset:
            try:
                message = generate_welcome_message(context, resources)
            except Exception as exc:
                message = format_welcome_message(context.welcome_message, resources)
                source = "descriptions"
                error = f"{type(exc).__name__}: {exc}"
            else:
                source = "preset"
        else:
            message = format_welcome_message(context.welcome_message, resources)
        if context.memory_context:
            message = f"{message}\n\n我会参考已保存的项目记忆和会话摘要。"
        startup_payload = build_startup_payload(
            welcome_message=message,
            resources=resources,
            memory_enabled=bool(context.memory_context),
            source=source,
        )
        return HookResult(
            message=message,
            payload=startup_payload,
            error=error,
        )


def build_default_session_start_hooks() -> list[HookHandler]:
    return [SessionStartHook()]


def generate_welcome_message(
    context: SessionStartContext,
    resources: list[dict[str, object]],
) -> str:
    prompt = (context.welcome_prompt or "").strip()
    if not prompt:
        raise ValueError("welcome.prompt is required when welcome.preset is true")
    if not context.welcome_model_base_url or not context.welcome_model_name:
        raise ValueError("welcome model profile is not configured")

    client = OpenAI(
        base_url=context.welcome_model_base_url,
        api_key=context.welcome_model_api_key or "not-needed",
    )
    response = client.chat.completions.create(
        model=context.welcome_model_name,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": _welcome_prompt_input(context, resources)},
        ],
        temperature=0,
        max_tokens=700,
    )
    message = (response.choices[0].message.content or "").strip()
    if not message:
        raise ValueError("welcome model returned empty message")
    return message


def format_welcome_message(
    welcome_message: str,
    resources: list[dict[str, object]],
) -> str:
    message = (welcome_message or DEFAULT_WELCOME_MESSAGE).strip() or DEFAULT_WELCOME_MESSAGE
    if not resources:
        return message

    lines = [message, "", "当前已接入能力："]
    for item in resources:
        name = str(item["name"])
        description = str(item["description"] or name)
        lines.append(f"- `{name}`：{description}")
    return "\n".join(lines)


def build_startup_payload(
    *,
    welcome_message: str,
    resources: list[dict[str, object]],
    memory_enabled: bool,
    source: str,
) -> dict[str, object]:
    capabilities = [
        {
            "kind": item["kind"],
            "name": item["name"],
            "description": item["description"],
        }
        for item in resources
    ]
    preset_questions = [
        question
        for item in resources
        if item.get("kind") == "subagent"
        for question in _preset_questions_for_resource(item)
    ]
    return {
        "source": source,
        "welcome_message": welcome_message,
        "preset_questions": preset_questions,
        "capabilities": capabilities,
        "memory_hint": "我会参考已保存的项目记忆和会话摘要。" if memory_enabled else "",
        "suggested_next_actions": [
            item["text"]
            for item in preset_questions[:3]
        ],
        "resources": resources,
    }


def _preset_questions_for_resource(item: dict[str, object]) -> list[dict[str, str]]:
    name = str(item.get("name", ""))
    questions = item.get("suggested_questions")
    if not isinstance(questions, list):
        return []
    payloads: list[dict[str, str]] = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        text = str(question.get("text") or "").strip()
        if not text:
            continue
        payloads.append(
            {
                "text": text,
                "target": name,
                "reason": str(question.get("reason") or "").strip(),
            }
        )
    return payloads


def _resource_summary(kind: str, item: AgentManifest | Skill) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": kind,
        "name": item.name,
        "description": item.description,
    }
    if kind == "subagent":
        payload["suggested_questions"] = [
            {"text": question.text, "reason": question.reason}
            for question in getattr(item, "suggested_questions", [])
        ]
    return payload


def _welcome_prompt_input(
    context: SessionStartContext,
    resources: list[dict[str, object]],
) -> str:
    lines = [
        "基础欢迎文案：",
        context.welcome_message,
        "",
        "已接入能力：",
    ]
    if resources:
        for item in resources:
            lines.append(f"- {item['kind']} `{item['name']}`：{item['description']}")
    else:
        lines.append("- 无")
    return "\n".join(lines)
