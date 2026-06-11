"""Worker subagent result envelopes and normalization helpers."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.runtime_utils import json_dumps


class SubagentResult(BaseModel):
    answer: str = ""
    subagent: str = ""
    trace: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def populate_extras(cls, data: Any) -> Any:
        if isinstance(data, dict):
            known_fields = {"answer", "subagent", "trace", "error", "artifacts", "extras"}
            extras = data.get("extras") or {}
            if not isinstance(extras, dict):
                extras = {}
            else:
                extras = dict(extras)
            for k, v in list(data.items()):
                if k not in known_fields:
                    extras[k] = data.pop(k)
            data["extras"] = extras
        return data


class SubagentToolInput(BaseModel):
    task: str = Field(
        description=(
            "Self-contained task for the worker agent, including resolved time "
            "ranges, business intent, and the user's original filter terms. "
            "Do not add inferred schema fields or enum values as facts."
        )
    )


def coerce_subagent_result(value: Any, subagent_name: str) -> SubagentResult:
    if isinstance(value, SubagentResult):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            payload = json.loads(extract_json_text(text))
        except json.JSONDecodeError:
            return SubagentResult(answer=text, subagent=subagent_name)
        if isinstance(payload, dict):
            return validate_subagent_payload(payload, subagent_name, raw_output=text)
    if isinstance(value, dict):
        return validate_subagent_payload(
            value,
            subagent_name,
            raw_output=json_dumps(value),
        )
    return SubagentResult(answer=str(value), subagent=subagent_name)


def validate_subagent_payload(
    payload: dict[str, Any],
    subagent_name: str,
    *,
    raw_output: str,
) -> SubagentResult:
    payload = normalize_subagent_payload(payload)
    try:
        return SubagentResult.model_validate(payload)
    except ValidationError as exc:
        return SubagentResult(
            subagent=subagent_name,
            trace=[
                {
                    "stage": "invalid_subagent_output",
                    "error": str(exc),
                    "raw_output": raw_output[:2000],
                }
            ],
            error=f"invalid_subagent_output: {exc.errors()}",
        )


def normalize_subagent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    trace = payload.get("trace")
    if not isinstance(trace, list):
        return payload
    normalized_trace = [
        item if isinstance(item, dict) else {"stage": "note", "message": str(item)}
        for item in trace
    ]
    return {**payload, "trace": normalized_trace}


def run_payloads(context: RuntimeContext) -> list[dict[str, Any]]:
    return [
        event.get("payload", {})
        for event in context.events
        if event.get("run_id") == context.run_id
    ]


def parse_subagent_tool_input(input_json: str) -> SubagentToolInput:
    try:
        payload = json.loads(input_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON input for subagent tool: {exc}") from exc
    if isinstance(payload, dict) and "task" not in payload and "input" in payload:
        payload = {**payload, "task": payload["input"]}
    return SubagentToolInput.model_validate(payload)


def build_subagent_input(options: dict[str, Any]) -> str:
    params = options.get("params", {})
    if isinstance(params, SubagentToolInput):
        return params.task
    if isinstance(params, dict):
        return str(params.get("task") or params.get("input") or "")
    return str(params)


async def extract_worker_agent_tool_output(value: Any) -> str:
    final_output = getattr(value, "final_output", value)
    result = coerce_subagent_result(final_output, "")
    return json_dumps(subagent_tool_payload(result))


def subagent_tool_payload(result: SubagentResult) -> dict[str, Any]:
    return {
        "answer": result.answer,
        "error": result.error,
        "subagent": result.subagent,
        "artifacts": result.artifacts,
        "extras": result.extras,
    }


def extract_json_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def model_dump(value: BaseModel) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


_coerce_subagent_result = coerce_subagent_result
_subagent_tool_payload = subagent_tool_payload
