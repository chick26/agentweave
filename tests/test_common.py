import os

from agent_runtime.common import (
    coerce_bool,
    columns_from_rows,
    load_runtime_env_files,
    quote_identifier,
    split_frontmatter,
    utc_now_iso,
    xml_escape,
)
import pytest


def test_common_helpers_cover_shared_formatting_and_validation():
    assert utc_now_iso().endswith("Z")
    assert "+00:00" not in utc_now_iso()
    assert xml_escape("<tag a='1' b=\"2\">&</tag>") == (
        "&lt;tag a=&apos;1&apos; b=&quot;2&quot;&gt;&amp;&lt;/tag&gt;"
    )
    metadata, body = split_frontmatter("---\nname: demo\n---\nBody")
    assert metadata == {"name": "demo"}
    assert body == "\nBody"
    assert coerce_bool("YES") is True
    assert coerce_bool("0") is False
    assert columns_from_rows([{"a": 1, "b": 2}, {"b": 3, "c": 4}]) == ["a", "b", "c"]
    assert quote_identifier("safe_name_1") == '"safe_name_1"'


def test_quote_identifier_rejects_unsafe_names():
    try:
        quote_identifier("bad; DROP TABLE x")
    except ValueError as exc:
        assert "Unsafe SQL identifier" in str(exc)
    else:
        raise AssertionError("Expected unsafe identifier to fail")


def test_split_frontmatter_rejects_invalid_yaml():
    with pytest.raises(ValueError, match="Invalid YAML frontmatter"):
        split_frontmatter("---\nname: [broken\n---\nBody")


def test_load_runtime_env_files_uses_standard_local_files(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTWEAVE_TEST_VALUE", raising=False)
    (tmp_path / ".env").write_text("AGENTWEAVE_TEST_VALUE=from-dot-env\n", encoding="utf-8")
    env_dir = tmp_path / ".agentweave"
    env_dir.mkdir()
    (env_dir / "text2sql.env").write_text(
        "TEXT2SQL_BACKEND=sqlite\n"
        "TEXT2SQL_DATABASE_URL=sqlite:////tmp/text2sql.sqlite\n",
        encoding="utf-8",
    )

    loaded = load_runtime_env_files(tmp_path)

    assert [path.name for path in loaded] == [".env", "text2sql.env"]
    assert os.environ["AGENTWEAVE_TEST_VALUE"] == "from-dot-env"
    assert os.environ["TEXT2SQL_BACKEND"] == "sqlite"
