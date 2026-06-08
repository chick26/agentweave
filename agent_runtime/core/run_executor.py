"""Runner execution helpers."""

from __future__ import annotations

from typing import Any, Callable

from agents import Runner


async def run_orchestrator_agent(
    *,
    agent: Any,
    user_input: str,
    context: Any,
    session: Any,
    max_turns: int,
    model_delta_callback: Callable[[dict[str, Any]], None] | None,
    model_name: str,
) -> Any:
    if model_delta_callback is None:
        return await Runner.run(
            agent,
            user_input,
            context=context,
            session=session,
            max_turns=max_turns,
        )
    result = Runner.run_streamed(
        agent,
        user_input,
        context=context,
        session=session,
        max_turns=max_turns,
    )
    async for stream_event in result.stream_events():
        if delta := model_text_delta(stream_event):
            model_delta_callback(
                {
                    "kind": "orchestration_model",
                    "stage": "model_delta",
                    "title": "编排模型调用",
                    "model": model_name,
                    "delta": delta,
                }
            )
    return result


def model_text_delta(stream_event: Any) -> str:
    if getattr(stream_event, "type", "") != "raw_response_event":
        return ""
    data = getattr(stream_event, "data", None)
    if getattr(data, "type", "") != "response.output_text.delta":
        return ""
    delta = getattr(data, "delta", "")
    return delta if isinstance(delta, str) else ""
