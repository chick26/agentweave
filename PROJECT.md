# Project Rules

This repository is being refactored from a Text2SQL-only app into a general-purpose agent runtime with delegated subagents.

Core boundaries:
- `subagents/` contains delegated worker agents with `AGENT.md`.
- `skills/` contains loadable method cards with `SKILL.md`; skills are not worker agents.
- The orchestrator chooses top-level subagents and summarizes results.
- Subagent-local tools must not be exposed to the orchestrator.
- Worker state must be per-run and must not be stored on shared runtime instances.
- SQL execution must remain read-only.
