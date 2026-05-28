import asyncio

from agent_runtime.compressor import ContextCompressor, emergency_trim, estimate_tokens, micro_compact
from agent_runtime.memory_manager import MemoryManager
from agent_runtime.memory_store import MemoryStore
from agent_runtime.model_profiles import ModelProfile
from agent_runtime.token_counter import TokenCountResult


class FixedCounter:
    name = "fixed"

    def __init__(self, tokens):
        self.tokens = tokens

    def count_messages(self, messages):
        return TokenCountResult(tokens=self.tokens, counter=self.name, fallback=False)


def test_context_compressor_thresholds():
    messages = [{"role": "user", "content": "x" * 150}]
    compressor = ContextCompressor(max_tokens=100)

    decision = compressor.decide(messages)

    assert estimate_tokens(messages) == 54
    assert decision.mode == "none"
    assert decision.input_budget == 100
    assert decision.counter == "heuristic"


def test_context_compressor_emergency_trim_keeps_head_and_tail():
    messages = [
        {"role": "user", "content": f"message {idx}"}
        for idx in range(12)
    ]

    trimmed = emergency_trim(messages)

    assert trimmed[:2] == messages[:2]
    assert trimmed[-6:] == messages[-6:]
    assert trimmed[2]["content"] == "[上下文已紧急截断]"


def test_context_compressor_emergency_trim_keeps_tool_pair_boundary():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "tool result"},
        {"role": "assistant", "content": "final"},
        {"role": "user", "content": "again"},
        {"role": "assistant", "content": "again answer"},
        {"role": "tool", "tool_call_id": "call_2", "content": "tail starts with tool"},
    ]

    trimmed = emergency_trim(messages)

    assert trimmed[0]["role"] == "system"
    assert {"id": "call_1"} in trimmed[-6]["tool_calls"]
    assert trimmed[-5]["role"] == "tool"


def test_context_compressor_soft_summary(tmp_path, monkeypatch):
    class FakeMessage:
        content = "摘要：用户关注可用机柜数量。"

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        async def create(self, **kwargs):
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(
        "agent_runtime.compressor.make_async_client",
        lambda profile: FakeClient(),
    )
    store = MemoryStore(tmp_path / "agent_memory.sqlite")
    manager = MemoryManager(store)
    profile = ModelProfile(
        role="sql_worker",
        base_url="http://example.test/v1",
        model_name="sql",
        api_key="not-needed",
        max_tokens=2048,
        context_window=32768,
    )
    messages = [
        {"role": "user", "content": f"message {idx} " + "x" * 150}
        for idx in range(12)
    ]

    compressed = asyncio.run(
        ContextCompressor(
            context_window=1000,
            reserved_output_tokens=0,
            safety_margin_tokens=0,
            token_counter=FixedCounter(750),
        ).compress(
            messages,
            session_id="abc",
            memory_manager=manager,
            model_profile=profile,
        )
    )

    assert compressed[:2] == messages[:2]
    assert compressed[-6:] == messages[-6:]
    assert "摘要：用户关注可用机柜数量。" in compressed[2]["content"]
    records = store.load_namespace("session:abc")
    assert records[0].key == "conversation_summary"
    assert records[0].content == "摘要：用户关注可用机柜数量。"


def test_context_compressor_micro_compacts_long_content():
    messages = [{"role": "tool", "content": "x" * 5000}]

    compacted = micro_compact(messages)

    assert len(compacted[0]["content"]) < 2000
    assert "内容已微压缩" in compacted[0]["content"]
