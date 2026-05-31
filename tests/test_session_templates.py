import pytest

from agent_runtime.storage.session_templates import SessionTemplateStore


def test_session_template_store_saves_lists_and_deletes(tmp_path):
    store = SessionTemplateStore(tmp_path / "templates.sqlite")

    template_id = store.save_template(
        name="IDC 巡检",
        messages=[
            {"role": "assistant", "content": "你好"},
            {"role": "user", "content": "查空闲机柜"},
            {"role": "", "content": "ignored"},
        ],
    )
    templates = store.list_templates()

    assert template_id == "tpl_idc_巡检"
    assert [template.name for template in templates] == ["IDC 巡检"]
    assert templates[0].messages == [
        {"role": "assistant", "content": "你好"},
        {"role": "user", "content": "查空闲机柜"},
    ]

    loaded = store.get_template(template_id)
    assert loaded.name == "IDC 巡检"

    store.delete_template(template_id)
    assert store.list_templates() == []


def test_session_template_store_rejects_empty_values(tmp_path):
    store = SessionTemplateStore(tmp_path / "templates.sqlite")

    with pytest.raises(ValueError):
        store.save_template(name="", messages=[{"role": "user", "content": "hello"}])

    with pytest.raises(ValueError):
        store.save_template(name="empty", messages=[])
