from pathlib import Path

from agent_runtime.core.hooks import HookResult, HookRunner, SessionStartContext
from agent_runtime.core.preset_questions import PresetQuestionGroup, PresetQuestionResult


def test_session_start_hook_generates_welcome(monkeypatch):
    def fake_generate_preset_question_result(**kwargs):
        return PresetQuestionResult(
            groups=[
                PresetQuestionGroup(
                    domain_name="idc_resources",
                    title="IDC 资源",
                    questions=["403机房有多少可用机柜？"],
                )
            ],
            source="model",
            domains=[{"name": "idc_resources", "description": "IDC 资源"}],
        )

    monkeypatch.setattr(
        "agent_runtime.core.hooks.generate_preset_question_result",
        fake_generate_preset_question_result,
    )

    result = HookRunner().run(
        "SessionStart",
        SessionStartContext(
            skills_root=Path("skills"),
            base_url="http://example.test/v1",
            model_name="sql",
            api_key="not-needed",
            questions_per_domain=1,
            memory_context="[project]\n- rule: 保留 SQL 口径",
        ),
    )

    assert result.error == ""
    assert "403机房有多少可用机柜？" in result.message
    assert "项目记忆" in result.message
    assert result.payload["source"] == "model"
    assert result.payload["domains"] == [{"name": "idc_resources", "description": "IDC 资源"}]


def test_session_start_hook_fallback_on_error(monkeypatch):
    def fake_generate_preset_question_result(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "agent_runtime.core.hooks.generate_preset_question_result",
        fake_generate_preset_question_result,
    )

    result = HookRunner().run(
        "SessionStart",
        SessionStartContext(
            skills_root=Path("skills"),
            base_url="http://example.test/v1",
            model_name="sql",
            api_key="not-needed",
        ),
    )

    assert "你好，我可以回答已接入数据领域的问数问题。" == result.message
    assert "RuntimeError: boom" == result.error
    assert result.payload["source"] == "fallback"


def test_hook_runner_returns_unsupported_event_error() -> None:
    result = HookRunner().run("UnknownEvent", object())

    assert result.message == ""
    assert result.error == "Unsupported hook event: UnknownEvent"


def test_hook_runner_accepts_injected_handler() -> None:
    class FakeHook:
        event_name = "SessionStart"

        def run(self, context):
            return HookResult(
                message=f"fake:{context.model_name}",
                payload={"source": "fake"},
            )

    result = HookRunner(handlers=[FakeHook()]).run(
        "SessionStart",
        SessionStartContext(
            skills_root=Path("skills"),
            base_url="http://example.test/v1",
            model_name="sql",
            api_key="not-needed",
        ),
    )

    assert result.message == "fake:sql"
    assert result.payload == {"source": "fake"}
    assert result.error == ""
