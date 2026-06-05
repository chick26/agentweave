"""Tests for hook execution and preset question aggregation."""

from pathlib import Path

from agent_runtime.core.hooks import HookResult, HookRunner, SessionStartContext
from agent_runtime.core.preset_questions import PresetQuestionGroup, PresetQuestionResult
from agent_runtime.core.preset_questions import generate_preset_question_result


def _write_subagent(root: Path, name: str, description: str) -> None:
    subagent_dir = root / "subagents" / name
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "AGENT.yaml").write_text(
        f"name: {name}\n"
        f"description: {description}\n"
        "execution:\n"
        "  mode: worker\n",
        encoding="utf-8",
    )
    (subagent_dir / "prompt.md").write_text(f"{name} prompt.\n", encoding="utf-8")


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

    assert "你好，我可以回答已接入能力范围内的问题。" == result.message
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


def test_hook_runner_runs_multiple_handlers_until_blocking_result() -> None:
    calls = []

    class FirstHook:
        event_name = "PreToolUse"

        def run(self, payload):
            calls.append(("first", payload["tool_name"]))
            return HookResult()

    class BlockingHook:
        event_name = "PreToolUse"

        def run(self, payload):
            calls.append(("blocking", payload["tool_name"]))
            return HookResult(exit_code=1, message="blocked")

    class NeverCalledHook:
        event_name = "PreToolUse"

        def run(self, payload):
            calls.append(("never", payload["tool_name"]))
            return HookResult()

    result = HookRunner(
        handlers=[FirstHook(), BlockingHook(), NeverCalledHook()]
    ).run("PreToolUse", {"tool_name": "load_skill"})

    assert result.exit_code == 1
    assert result.message == "blocked"
    assert calls == [("first", "load_skill"), ("blocking", "load_skill")]


def test_hook_runner_supports_injected_pre_tool_result() -> None:
    class InjectHook:
        event_name = "PreToolUse"

        def run(self, payload):
            return HookResult(exit_code=2, message=f"inject:{payload['tool_name']}")

    result = HookRunner(handlers={"PreToolUse": [InjectHook()]}).run(
        "PreToolUse",
        {"tool_name": "get_current_time"},
    )

    assert result.exit_code == 2
    assert result.message == "inject:get_current_time"


def test_preset_questions_fallback_to_generic_subagent_capabilities(tmp_path):
    _write_subagent(tmp_path, "api_call", "调用外部 API 查询业务状态。")

    result = generate_preset_question_result(
        subagents_root=tmp_path / "subagents",
        base_url="http://example.test/v1",
        model_name="unused",
        api_key="not-needed",
    )

    assert result.source == "capabilities"
    assert result.groups == []
    assert result.domains == [
        {"name": "api_call", "description": "调用外部 API 查询业务状态。"}
    ]


def test_preset_questions_loads_bot_welcome_provider(tmp_path, monkeypatch):
    provider_module = tmp_path / "fake_welcome.py"
    provider_module.write_text(
        "from agent_runtime.core.preset_questions import PresetQuestionGroup, PresetQuestionResult\n\n"
        "def generate_preset_question_result(**kwargs):\n"
        "    manifest = kwargs['manifest']\n"
        "    return PresetQuestionResult(\n"
        "        groups=[PresetQuestionGroup(domain_name=manifest.name, title='API', questions=['查一下接口状态'])],\n"
        "        source='provider',\n"
        "        domains=[{'name': manifest.name, 'description': manifest.description}],\n"
        "    )\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_subagent(tmp_path, "api_call", "调用外部 API 查询业务状态。")

    result = generate_preset_question_result(
        subagents_root=tmp_path / "subagents",
        base_url="http://example.test/v1",
        model_name="unused",
        api_key="not-needed",
        welcome_provider_module="fake_welcome",
    )

    assert result.source == "provider"
    assert result.groups[0].questions == ["查一下接口状态"]
    assert result.domains == [{"name": "api_call", "description": "调用外部 API 查询业务状态。"}]


def test_preset_questions_static_mode_uses_configured_yaml_groups(tmp_path):
    _write_subagent(tmp_path, "api_call", "调用外部 API 查询业务状态。")

    result = generate_preset_question_result(
        subagents_root=tmp_path / "subagents",
        base_url="http://example.test/v1",
        model_name="unused",
        api_key="not-needed",
        welcome_mode="static",
        preset_question_groups=[
            {
                "domain_name": "api_call",
                "title": "API 查询",
                "questions": ["查一下接口状态"],
            }
        ],
    )

    assert result.source == "config"
    assert result.error == ""
    assert result.groups == [
        PresetQuestionGroup(
            domain_name="api_call",
            title="API 查询",
            questions=["查一下接口状态"],
        )
    ]
    assert result.domains == [{"name": "api_call", "description": "调用外部 API 查询业务状态。"}]
