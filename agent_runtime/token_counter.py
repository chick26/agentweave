from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from agent_runtime.common import json_size


@dataclass(frozen=True)
class TokenCountResult:
    tokens: int
    counter: str
    fallback: bool = False


class TokenCounter(Protocol):
    name: str

    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCountResult:
        ...


class HeuristicTokenCounter:
    name = "heuristic"

    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCountResult:
        return TokenCountResult(
            tokens=sum(_estimate_message_tokens(message) for message in messages),
            counter=self.name,
            fallback=True,
        )


class TiktokenTokenCounter:
    def __init__(self, model_name: str) -> None:
        import tiktoken  # type: ignore

        try:
            self._encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            self._encoding = tiktoken.get_encoding("cl100k_base")
        self.name = "tiktoken"

    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCountResult:
        text = _format_messages_for_counting(messages)
        return TokenCountResult(
            tokens=len(self._encoding.encode(text)),
            counter=self.name,
            fallback=False,
        )


class QwenTokenCounter:
    def __init__(self, model_name: str) -> None:
        from transformers import AutoTokenizer  # type: ignore

        self._tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            trust_remote_code=True,
            local_files_only=True,
        )
        self.name = "qwen_tokenizer"

    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCountResult:
        chat_messages = [
            {"role": str(message.get("role") or "user"), "content": str(message.get("content") or "")}
            for message in messages
            if message.get("content") is not None
        ]
        if hasattr(self._tokenizer, "apply_chat_template"):
            rendered = self._tokenizer.apply_chat_template(
                chat_messages,
                tokenize=False,
                add_generation_prompt=False,
            )
            token_ids = self._tokenizer(rendered, add_special_tokens=False).input_ids
        else:
            token_ids = self._tokenizer(_format_messages_for_counting(messages)).input_ids
        return TokenCountResult(
            tokens=len(token_ids),
            counter=self.name,
            fallback=False,
        )


def build_token_counter(model_name: str) -> TokenCounter:
    if _looks_like_qwen_hf_model(model_name):
        try:
            return QwenTokenCounter(model_name)
        except Exception:
            return HeuristicTokenCounter()
    if _looks_like_openai_model(model_name):
        try:
            return TiktokenTokenCounter(model_name)
        except Exception:
            return HeuristicTokenCounter()
    return HeuristicTokenCounter()


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return HeuristicTokenCounter().count_messages(messages).tokens


def _looks_like_qwen_hf_model(model_name: str) -> bool:
    lowered = model_name.lower()
    return lowered.startswith("qwen/") or lowered.startswith("qwenlm/")


def _looks_like_openai_model(model_name: str) -> bool:
    return bool(re.match(r"^(gpt-|o[0-9]+-|chatgpt-)", model_name.lower()))


def _estimate_message_tokens(message: dict[str, Any]) -> int:
    content = str(message.get("content", ""))
    non_ascii_count = sum(1 for c in content if ord(c) > 127)
    ascii_len = len(content) - non_ascii_count
    tokens = int(non_ascii_count * 1.2) + max(1, ascii_len // 3)
    for key in ("tool_calls", "function_call", "arguments"):
        if message.get(key):
            tokens += max(8, json_size(message.get(key)) // 3)
    tokens += 4
    return tokens


def _format_messages_for_counting(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "unknown"))
        content = str(message.get("content", ""))
        lines.append(f"{role}: {content}")
        if message.get("tool_calls"):
            lines.append(f"tool_calls: {message['tool_calls']}")
    return "\n\n".join(lines)
