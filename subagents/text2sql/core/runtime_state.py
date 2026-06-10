"""Run-scoped typed state helpers for the Text2SQL subagent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agent_runtime.shared.database import DatabaseBackend
from agent_runtime.subagent_api import SubagentContext
from subagents.text2sql.core.domain_catalog import DomainConfig


TEXT2SQL_STATE_NAMESPACE = "text2sql"


@dataclass
class Text2SQLState:
    backend: DatabaseBackend | None = None
    active_domain: str = ""
    active_table: str = ""
    active_text_fields: list[str] = field(default_factory=list)
    active_field_descriptions: dict[str, str] = field(default_factory=dict)
    active_columns: list[str] = field(default_factory=list)
    schema_text: str = ""


@dataclass(frozen=True)
class Text2SQLActiveDomain:
    domain: str
    table: str
    schema_text: str
    columns: list[str]
    text_fields: list[str]
    field_descriptions: dict[str, str]


class Text2SQLRunStateManager:
    def __init__(
        self,
        *,
        run_ctx: SubagentContext,
        backend_factory: Callable[[], DatabaseBackend],
    ) -> None:
        self.run_ctx = run_ctx
        self.backend_factory = backend_factory
        self.state = run_ctx.typed_state(TEXT2SQL_STATE_NAMESPACE, Text2SQLState)

    @classmethod
    def from_context(
        cls,
        run_ctx: SubagentContext,
        *,
        backend_factory: Callable[[], DatabaseBackend],
    ) -> "Text2SQLRunStateManager":
        return cls(run_ctx=run_ctx, backend_factory=backend_factory)

    @property
    def backend(self) -> DatabaseBackend:
        if self.state.backend is None:
            self.state.backend = self.backend_factory()
        return self.state.backend

    @property
    def dialect(self) -> str:
        return str(getattr(self.backend, "dialect", "SQL"))

    def activate_domain(self, domain: DomainConfig) -> Text2SQLActiveDomain:
        columns = self.backend.get_columns(domain.table)
        schema_text = self.backend.get_schema_for_prompt(
            domain.table,
            domain.field_descriptions,
        )
        self.state.active_domain = domain.name
        self.state.active_table = domain.table
        self.state.active_text_fields = list(domain.text_fields)
        self.state.active_field_descriptions = dict(domain.field_descriptions)
        self.state.active_columns = list(columns)
        self.state.schema_text = schema_text
        self.run_ctx.trace(
            stage="activation",
            title=f"Domain: {domain.name}",
            input={"domain_name": domain.name},
            output={
                "name": domain.name,
                "description": domain.description,
                "table": domain.table,
            },
        )
        return self.active_domain()

    def ensure_domain(self, domain: DomainConfig) -> Text2SQLActiveDomain:
        if self.state.active_domain != domain.name or self.state.active_table != domain.table:
            return self.activate_domain(domain)
        return self.active_domain()

    def active_domain(self) -> Text2SQLActiveDomain:
        if not self.state.active_domain or not self.state.active_table:
            raise RuntimeError("Text2SQL domain is not activated.")
        return Text2SQLActiveDomain(
            domain=self.state.active_domain,
            table=self.state.active_table,
            schema_text=self.state.schema_text,
            columns=list(self.state.active_columns),
            text_fields=list(self.state.active_text_fields),
            field_descriptions=dict(self.state.active_field_descriptions),
        )

    def search_fields(self, field_list: list[str] | None = None) -> list[str]:
        active = self.active_domain()
        selected_columns = set(active.columns)
        return [
            field
            for field in list(field_list or active.text_fields)
            if field in selected_columns
        ]
