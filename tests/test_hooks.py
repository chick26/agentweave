"""Tests for hook execution and SessionStart welcome rendering."""

from pathlib import Path

from agent_runtime.core.hooks import HookResult, HookRunner
from agent_runtime.hooks.session_start import (
    SessionStartContext,
    build_default_session_start_hooks,
)
from agent_runtime.registry.manifest_models import AgentManifest, Skill, SuggestedQuestion


def _subagent(
    name: str,
    description: str,
    *,
    suggested_questions: list[SuggestedQuestion] | None = None,
) -> AgentManifest:
    return AgentManifest(
        name=name,
        description=description,
        location=Path("subagents") / name / "AGENT.yaml",
        kind="subagent",
        suggested_questions=suggested_questions or [],
    )


def _skill(name: str, description: str) -> Skill:
    return Skill(
        name=name,
        description=description,
        location=Path("skills") / name / "SKILL.md",
        kind="skill",
    )


def test_session_start_hook_returns_default_welcome_without_resources():
    result = HookRunner(handlers=build_default_session_start_hooks()).run(
        "SessionStart",
        SessionStartContext(),
    )

    assert result.error == ""
    assert result.message == "你好，我可以回答已接入能力范围内的问题。"
    assert result.payload["source"] == "default"
    assert result.payload["welcome_message"] == "你好，我可以回答已接入能力范围内的问题。"
    assert result.payload["preset_questions"] == []
    assert result.payload["capabilities"] == []
    assert result.payload["resources"] == []


def test_session_start_hook_renders_mounted_resource_descriptions():
    result = HookRunner(handlers=build_default_session_start_hooks()).run(
        "SessionStart",
        SessionStartContext(
            welcome_message="你好，当前机器人已加载以下能力。",
            memory_context="[project]\n- rule: 保留 SQL 口径",
            subagents=[
                _subagent(
                    "text2sql",
                    "使用自然语言查询结构化数据，生成并执行只读 SQL。",
                    suggested_questions=[
                        SuggestedQuestion(
                            text="查询最近一周结构化数据中的关键指标",
                            reason="结构化数据查询",
                        )
                    ],
                ),
                _subagent(
                    "rag",
                    "基于 Markdown 知识库回答问题，返回带来源的检索片段。",
                    suggested_questions=[
                        SuggestedQuestion(
                            text="总结知识库中与当前问题相关的资料",
                            reason="知识库检索",
                        )
                    ],
                ),
            ],
            skills=[
                _skill("data_analysis", "面向表格结果的数据分析方法卡。"),
            ],
        ),
    )

    assert result.error == ""
    assert "你好，当前机器人已加载以下能力。" in result.message
    assert "当前已接入能力：" in result.message
    assert "`text2sql`：使用自然语言查询结构化数据，生成并执行只读 SQL。" in result.message
    assert "`rag`：基于 Markdown 知识库回答问题，返回带来源的检索片段。" in result.message
    assert "`data_analysis`：面向表格结果的数据分析方法卡。" in result.message
    assert "项目记忆" in result.message
    assert result.payload["source"] == "descriptions"
    assert result.payload["memory_hint"]
    assert [item["target"] for item in result.payload["preset_questions"]] == [
        "text2sql",
        "rag",
    ]
    assert [item["name"] for item in result.payload["resources"]] == [
        "text2sql",
        "rag",
        "data_analysis",
    ]


def test_session_start_hook_uses_prompt_when_preset_enabled(monkeypatch):
    captured = {}

    def fake_generate_welcome_message(context, resources):
        captured["prompt"] = context.welcome_prompt
        captured["resources"] = resources
        return "欢迎使用数据分析机器人。\n- 403机房有多少可用机柜？"

    monkeypatch.setattr(
        "agent_runtime.hooks.session_start.generate_welcome_message",
        fake_generate_welcome_message,
    )

    result = HookRunner(handlers=build_default_session_start_hooks()).run(
        "SessionStart",
        SessionStartContext(
            welcome_preset=True,
            welcome_prompt="根据能力描述生成示例问题",
            welcome_model_base_url="http://example.test/v1",
            welcome_model_name="chat",
            welcome_model_api_key="not-needed",
            subagents=[
                _subagent(
                    "text2sql",
                    "使用自然语言查询结构化数据。",
                    suggested_questions=[
                        SuggestedQuestion(
                            text="查询最近一周结构化数据中的关键指标",
                            reason="结构化数据查询",
                        )
                    ],
                ),
            ],
        ),
    )

    assert result.error == ""
    assert result.message == "欢迎使用数据分析机器人。\n- 403机房有多少可用机柜？"
    assert result.payload["source"] == "preset"
    assert result.payload["preset_questions"][0]["target"] == "text2sql"
    assert captured["prompt"] == "根据能力描述生成示例问题"
    assert captured["resources"] == [
        {
            "kind": "subagent",
            "name": "text2sql",
            "description": "使用自然语言查询结构化数据。",
            "suggested_questions": [
                {
                    "text": "查询最近一周结构化数据中的关键指标",
                    "reason": "结构化数据查询",
                }
            ],
        }
    ]


def test_session_start_hook_does_not_generate_fallback_presets_for_unknown_subagent():
    result = HookRunner(handlers=build_default_session_start_hooks()).run(
        "SessionStart",
        SessionStartContext(
            subagents=[
                _subagent("code_executor", "执行代码任务。"),
            ],
        ),
    )

    assert result.payload["preset_questions"] == []
    assert result.payload["suggested_next_actions"] == []


def test_hook_runner_returns_unsupported_event_error() -> None:
    result = HookRunner().run("UnknownEvent", object())

    assert result.message == ""
    assert result.error == "Unsupported hook event: UnknownEvent"


def test_hook_runner_known_event_without_handlers_returns_empty_result() -> None:
    result = HookRunner().run("SessionStart", object())

    assert result == HookResult()


def test_hook_runner_accepts_injected_handler() -> None:
    class FakeHook:
        event_name = "SessionStart"

        def run(self, context):
            return HookResult(
                message=f"fake:{context.welcome_message}",
                payload={"source": "fake"},
            )

    result = HookRunner(handlers=[FakeHook()]).run(
        "SessionStart",
        SessionStartContext(welcome_message="hello"),
    )

    assert result.message == "fake:hello"
    assert result.payload == {"source": "fake"}
    assert result.error == ""


def test_hook_runner_aggregates_multiple_handlers() -> None:
    class FirstHook:
        event_name = "SessionStart"

        def run(self, context):
            return HookResult(message="first", payload={"first": True})

    class SecondHook:
        event_name = "SessionStart"

        def run(self, context):
            return HookResult(message="second", payload={"second": True})

    result = HookRunner(handlers=[FirstHook(), SecondHook()]).run(
        "SessionStart",
        SessionStartContext(),
    )

    assert result.message == "first\n\nsecond"
    assert result.payload["first"] is True
    assert result.payload["second"] is True
    assert [item["message"] for item in result.payload["handler_results"]] == [
        "first",
        "second",
    ]


def test_hook_runner_no_longer_has_builtin_tool_interception_events() -> None:
    result = HookRunner().run("PreToolUse", {"tool_name": "load_skill"})

    assert result.error == "Unsupported hook event: PreToolUse"
