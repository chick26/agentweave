from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents import AsyncOpenAI, ModelSettings, OpenAIChatCompletionsModel
from agents.models.chatcmpl_converter import Converter
from agents.models.openai_chatcompletions import _to_dump_compatible

from agent_runtime.common import to_jsonable, utc_now_iso


class LoggingOpenAIChatCompletionsModel(OpenAIChatCompletionsModel):
    """OpenAI Chat Completions model wrapper that records payloads."""

    def __init__(
        self,
        *,
        model: str,
        openai_client: AsyncOpenAI,
        log_callback: Callable[[dict[str, Any]], None],
        title: str,
        kind: str,
    ) -> None:
        super().__init__(model=model, openai_client=openai_client)
        self._log_callback = log_callback
        self._title = title
        self._kind = kind

    async def _fetch_response(
        self,
        system_instructions: str | None,
        input: str | list[Any],
        model_settings: ModelSettings,
        tools: list[Any],
        output_schema: Any | None,
        handoffs: list[Any],
        span: Any,
        tracing: Any,
        stream: bool = False,
        prompt: Any | None = None,
    ):
        log_entry: dict[str, Any] = {
            "kind": self._kind,
            "title": self._title,
            "model": str(self.model),
            "created_at": utc_now_iso(),
            "request": {
                "messages": _build_chat_messages_for_log(
                    model=str(self.model),
                    base_url=str(self._client.base_url),
                    system_instructions=system_instructions,
                    input=input,
                    should_replay_reasoning_content=self.should_replay_reasoning_content,
                ),
                "tools": _build_tools_for_log(tools, handoffs),
                "model_settings": model_settings.to_json_dict(),
                "stream": stream,
                "prompt": prompt,
            },
        }
        try:
            response = await super()._fetch_response(
                system_instructions=system_instructions,
                input=input,
                model_settings=model_settings,
                tools=tools,
                output_schema=output_schema,
                handoffs=handoffs,
                span=span,
                tracing=tracing,
                stream=stream,
                prompt=prompt,
            )
            raw_response = response[0] if isinstance(response, tuple) else response
            log_entry["response"] = to_jsonable(raw_response)
            log_entry["usage"] = to_jsonable(getattr(raw_response, "usage", None))
            return response
        except Exception as exc:
            log_entry["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            log_entry["completed_at"] = utc_now_iso()
            self._log_callback(log_entry)


async def call_chat_model(
    *,
    client: AsyncOpenAI,
    model_name: str,
    max_tokens: int,
    messages: list[dict[str, str]],
    log_callback: Callable[[dict[str, Any]], None] | None = None,
    title: str = "模型调用",
    kind: str = "model_call",
) -> str:
    log_entry: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "model": model_name,
        "created_at": utc_now_iso(),
        "request": {
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        },
    }
    try:
        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0,
            max_tokens=max_tokens,
        )
        raw_output = response.choices[0].message.content or ""
        log_entry["response"] = to_jsonable(response)
        log_entry["usage"] = to_jsonable(response.usage)
        return raw_output
    except Exception as exc:
        log_entry["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        log_entry["completed_at"] = utc_now_iso()
        if log_callback is not None:
            log_callback(log_entry)


def build_model(*, profile, log_callback, title: str, kind: str):
    return LoggingOpenAIChatCompletionsModel(
        model=profile.model_name,
        openai_client=make_async_client(profile),
        log_callback=log_callback,
        title=title,
        kind=kind,
    )


def make_async_client(profile) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=profile.base_url,
        api_key=profile.api_key,
        timeout=float(os.getenv("OPENAI_CLIENT_TIMEOUT", "60")),
        max_retries=int(os.getenv("OPENAI_CLIENT_MAX_RETRIES", "2")),
    )


def get_current_time_payload(
    timezone_name: str = "Asia/Hong_Kong",
    now: datetime | None = None,
) -> dict[str, str]:
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unsupported timezone: {timezone_name}") from exc

    source_now = now or datetime.now(timezone.utc)
    if source_now.tzinfo is None:
        source_now = source_now.replace(tzinfo=timezone.utc)
    local_now = source_now.astimezone(tz)
    return {
        "timezone": timezone_name,
        "iso": local_now.isoformat(timespec="seconds"),
        "date": local_now.date().isoformat(),
        "time": local_now.strftime("%H:%M:%S"),
        "weekday": local_now.strftime("%A"),
    }


def extract_sql(content: str) -> str:
    stripped = content.strip()
    fenced_sql = _extract_fenced_sql(stripped)
    if fenced_sql:
        return _normalize_sql_statement(fenced_sql)
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```sql").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
        return _normalize_sql_statement(stripped)
    match = re.search(r"\b(select|with)\b.+", stripped, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return stripped
    sql = match.group(0).strip()
    return _normalize_sql_statement(sql)


def _build_chat_messages_for_log(
    *,
    model: str,
    base_url: str,
    system_instructions: str | None,
    input: str | list[Any],
    should_replay_reasoning_content: Any,
) -> list[dict[str, Any]]:
    converted_messages = Converter.items_to_messages(
        input,
        model=model,
        base_url=base_url,
        should_replay_reasoning_content=should_replay_reasoning_content,
    )
    if system_instructions:
        converted_messages.insert(0, {"content": system_instructions, "role": "system"})
    return to_jsonable(_to_dump_compatible(converted_messages))


def _build_tools_for_log(tools: list[Any], handoffs: list[Any]) -> list[dict[str, Any]]:
    converted_tools = [Converter.tool_to_openai(tool) for tool in tools] if tools else []
    for handoff in handoffs:
        converted_tools.append(Converter.convert_handoff_tool(handoff))
    return to_jsonable(_to_dump_compatible(converted_tools))


def _extract_fenced_sql(content: str) -> str:
    matches = re.findall(
        r"```(?:sql|sqlite)?\s*(.*?)```",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for candidate in reversed(matches):
        if re.search(r"\b(select|with)\b", candidate, flags=re.IGNORECASE):
            return candidate.strip()
    return ""


def _normalize_sql_statement(sql: str) -> str:
    stripped = sql.strip()
    if ";" in stripped:
        stripped = stripped.split(";", 1)[0].strip()
    kept: list[str] = []
    for line in stripped.splitlines():
        clean = line.strip()
        if not clean:
            continue
        kept.append(clean)
    return " ".join(kept)


def json_dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False)
