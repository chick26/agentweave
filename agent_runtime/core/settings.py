from __future__ import annotations

from agent_runtime.core.model_profiles import ModelProfile, load_model_profiles


def build_model_profiles(
    *,
    base_url: str,
    model_name: str,
    api_key: str,
    max_tokens: int,
    sql_base_url: str,
    sql_model_name: str,
    sql_max_tokens: int,
) -> dict[str, ModelProfile]:
    return load_model_profiles(
        orchestrator_base_url=base_url,
        orchestrator_model=model_name,
        orchestrator_max_tokens=max_tokens,
        sql_base_url=sql_base_url,
        sql_model=sql_model_name,
        sql_max_tokens=sql_max_tokens,
        api_key=api_key,
    )
