import sys
from types import SimpleNamespace

from agent_runtime.compressor import ContextCompressor
from agent_runtime.token_counter import TokenCountResult, build_token_counter


class FixedCounter:
    name = "fixed"

    def __init__(self, tokens):
        self.tokens = tokens

    def count_messages(self, messages):
        return TokenCountResult(tokens=self.tokens, counter=self.name, fallback=False)


def test_context_compressor_uses_input_budget_not_output_max_tokens():
    messages = [{"role": "user", "content": "hello"}]

    none = ContextCompressor(
        context_window=1000,
        reserved_output_tokens=200,
        safety_margin_tokens=100,
        token_counter=FixedCounter(489),
    ).decide(messages)
    soft = ContextCompressor(
        context_window=1000,
        reserved_output_tokens=200,
        safety_margin_tokens=100,
        token_counter=FixedCounter(490),
    ).decide(messages)
    hard = ContextCompressor(
        context_window=1000,
        reserved_output_tokens=200,
        safety_margin_tokens=100,
        token_counter=FixedCounter(630),
    ).decide(messages)

    assert none.input_budget == 700
    assert none.mode == "none"
    assert soft.mode == "soft"
    assert hard.mode == "hard"
    assert soft.counter == "fixed"
    assert soft.fallback is False


def test_qwen_token_counter_is_used_when_local_tokenizer_is_available(monkeypatch):
    class FakeTokenizer:
        def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False):
            return " ".join(message["content"] for message in messages)

        def __call__(self, text, add_special_tokens=False):
            return SimpleNamespace(input_ids=text.split())

    fake_transformers = SimpleNamespace(
        AutoTokenizer=SimpleNamespace(
            from_pretrained=lambda *args, **kwargs: FakeTokenizer(),
        )
    )
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    counter = build_token_counter("Qwen/example-chat-model")
    result = counter.count_messages([{"role": "user", "content": "one two three"}])

    assert result.counter == "qwen_tokenizer"
    assert result.tokens == 3
    assert result.fallback is False


def test_openai_token_counter_uses_tiktoken_when_available(monkeypatch):
    class FakeEncoding:
        def encode(self, text):
            return text.split()

    fake_tiktoken = SimpleNamespace(
        encoding_for_model=lambda model: FakeEncoding(),
    )
    monkeypatch.setitem(sys.modules, "tiktoken", fake_tiktoken)

    counter = build_token_counter("gpt-4o")
    result = counter.count_messages([{"role": "user", "content": "one two"}])

    assert result.counter == "tiktoken"
    assert result.tokens >= 2
    assert result.fallback is False


def test_unknown_model_uses_heuristic_fallback():
    counter = build_token_counter("private-model")
    result = counter.count_messages([{"role": "user", "content": "hello"}])

    assert result.counter == "heuristic"
    assert result.fallback is True
