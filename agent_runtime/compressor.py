from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime.memory_manager import MemoryManager
from agent_runtime.model_profiles import ModelProfile
from agent_runtime.prompts import COMPACTION_PROMPT
from agent_runtime.runtime_utils import make_async_client
from agent_runtime.token_counter import (
    TokenCounter,
    build_token_counter,
    estimate_tokens as _heuristic_estimate_tokens,
)


SOFT_THRESHOLD = 0.70
HARD_THRESHOLD = 0.90
MAX_MESSAGE_CONTENT_CHARS = 4000
MESSAGE_PREVIEW_CHARS = 1200
DEFAULT_SAFETY_MARGIN_TOKENS = 512


@dataclass(frozen=True)
class CompressionDecision:
    mode: str
    estimated_tokens: int
    context_window: int
    reserved_output_tokens: int
    safety_margin_tokens: int
    input_budget: int
    ratio: float
    counter: str
    fallback: bool

    @property
    def max_tokens(self) -> int:
        """Backward-compatible alias for old tests/callers."""
        return self.input_budget


class ContextCompressor:
    def __init__(
        self,
        context_window: int | None = None,
        *,
        max_tokens: int | None = None,
        reserved_output_tokens: int = 0,
        safety_margin_tokens: int = DEFAULT_SAFETY_MARGIN_TOKENS,
        model_name: str = "",
        token_counter: TokenCounter | None = None,
    ) -> None:
        if context_window is None and max_tokens is not None:
            safety_margin_tokens = 0
        resolved_context_window = context_window or max_tokens or 4096
        self.context_window = resolved_context_window
        self.reserved_output_tokens = max(0, reserved_output_tokens)
        self.safety_margin_tokens = max(0, safety_margin_tokens)
        self.input_budget = max(
            1,
            resolved_context_window - self.reserved_output_tokens - self.safety_margin_tokens,
        )
        self.token_counter = token_counter or build_token_counter(model_name)

    async def compress(
        self,
        messages: list[dict[str, Any]],
        session_id: str,
        memory_manager: MemoryManager,
        model_profile: ModelProfile,
    ) -> list[dict[str, Any]]:
        compacted = micro_compact(messages)
        decision = self.decide(compacted)
        if decision.mode == "none":
            return compacted
        if decision.mode == "hard":
            return self.emergency_trim(compacted)
        try:
            summary = await self._llm_summarize(compacted, model_profile)
        except Exception:
            return compacted
        if not summary:
            return compacted
        memory_manager.write_session_summary(session_id, summary)
        return self._replace_middle_with_summary(compacted, summary)

    def decide(self, messages: list[dict[str, Any]]) -> CompressionDecision:
        count_result = self.token_counter.count_messages(messages)
        estimated_tokens = count_result.tokens
        ratio = estimated_tokens / self.input_budget if self.input_budget else 0
        if ratio >= HARD_THRESHOLD:
            mode = "hard"
        elif ratio >= SOFT_THRESHOLD:
            mode = "soft"
        else:
            mode = "none"
        return CompressionDecision(
            mode=mode,
            estimated_tokens=estimated_tokens,
            context_window=self.context_window,
            reserved_output_tokens=self.reserved_output_tokens,
            safety_margin_tokens=self.safety_margin_tokens,
            input_budget=self.input_budget,
            ratio=ratio,
            counter=count_result.counter,
            fallback=count_result.fallback,
        )

    def emergency_trim(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return emergency_trim(messages)

    async def _llm_summarize(
        self,
        messages: list[dict[str, Any]],
        profile: ModelProfile,
    ) -> str:
        client = make_async_client(profile)
        response = await client.chat.completions.create(
            model=profile.model_name,
            messages=[
                {"role": "system", "content": COMPACTION_PROMPT},
                {"role": "user", "content": _format_for_summary(messages)},
            ],
            max_tokens=min(1024, profile.max_tokens),
        )
        content = response.choices[0].message.content or ""
        # Extract <summary> content if present; otherwise use full output
        return _extract_summary_tags(content.strip())

    def _replace_middle_with_summary(
        self,
        messages: list[dict[str, Any]],
        summary: str,
    ) -> list[dict[str, Any]]:
        if len(messages) <= 8:
            return list(messages)
        marker = {
            "role": "assistant",
            "content": f"[上下文摘要]\n{summary}",
        }
        return safe_stitch_with_marker(messages[:2], messages[-6:], marker)


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return _heuristic_estimate_tokens(messages)


def micro_compact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content", "")
        if not isinstance(content, str) or len(content) <= MAX_MESSAGE_CONTENT_CHARS:
            compacted.append(dict(message))
            continue
        preview = content[:MESSAGE_PREVIEW_CHARS].rstrip()
        compacted.append(
            {
                **message,
                "content": (
                    f"{preview}\n\n"
                    f"[内容已微压缩：原始长度 {len(content)} 字符，仅保留预览。]"
                ),
            }
        )
    return compacted


def emergency_trim(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(messages) <= 8:
        return list(messages)
    keep_head = _head_with_system_messages(messages, keep_count=2)
    keep_tail = _tail_with_tool_pairs(messages, keep_count=6)
    marker = {
        "role": "assistant",
        "content": "[上下文已紧急截断]",
    }
    return safe_stitch_with_marker(keep_head, keep_tail, marker)


def safe_stitch_with_marker(
    head: list[dict[str, Any]],
    tail: list[dict[str, Any]],
    marker: dict[str, Any],
) -> list[dict[str, Any]]:
    # Deep copy lists so we don't mutate original session messages
    head = [dict(m) for m in head]
    tail = [dict(m) for m in tail]
    marker = dict(marker)

    # Check head boundary
    if head and head[-1].get("role") == marker.get("role"):
        merged_content = f"{head[-1].get('content', '')}\n\n{marker.get('content', '')}"
        if tail and tail[0].get("role") == head[-1].get("role"):
            tail[0]["content"] = f"{merged_content}\n\n{tail[0].get('content', '')}"
            return head[:-1] + tail
        head[-1]["content"] = merged_content
        return head + tail

    # Check tail boundary
    if tail and tail[0].get("role") == marker.get("role"):
        tail[0]["content"] = f"{marker.get('content', '')}\n\n{tail[0].get('content', '')}"
        return head + tail

    return head + [marker] + tail


def _head_with_system_messages(
    messages: list[dict[str, Any]],
    *,
    keep_count: int,
) -> list[dict[str, Any]]:
    keep_indices: list[int] = []
    for idx, message in enumerate(messages):
        if message.get("role") == "system":
            keep_indices.append(idx)
    keep_indices.extend(range(min(keep_count, len(messages))))
    unique_indices = sorted(set(keep_indices))
    return [dict(messages[idx]) for idx in unique_indices]


def _tail_with_tool_pairs(
    messages: list[dict[str, Any]],
    *,
    keep_count: int,
) -> list[dict[str, Any]]:
    start = max(0, len(messages) - keep_count)
    if start < len(messages) and messages[start].get("role") == "tool":
        for idx in range(start - 1, -1, -1):
            if messages[idx].get("role") == "assistant" and messages[idx].get("tool_calls"):
                start = idx
                break
            if messages[idx].get("role") != "tool":
                break
    return [dict(message) for message in messages[start:]]


def _format_for_summary(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "unknown"))
        content = str(message.get("content", ""))
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


def _extract_summary_tags(text: str) -> str:
    """Extract content from <summary>...</summary> tags if present."""
    start = text.find("<summary>")
    end = text.find("</summary>")
    if start != -1 and end != -1 and end > start:
        return text[start + len("<summary>") : end].strip()
    return text
