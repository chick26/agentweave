from agent_runtime.memory.memory_store import MemoryStore


def test_memory_store_isolates_namespaces_and_overwrites(tmp_path):
    store = MemoryStore(tmp_path / "agent_memory.sqlite")

    store.write("project", "currency", "Use HKD", tags=["money"])
    store.write("user", "currency", "Use USD", tags=["money"])
    store.write("project", "currency", "Use CNY", tags=["money"])

    project_records = store.load_namespace("project")
    user_records = store.load_namespace("user")

    assert len(project_records) == 1
    assert project_records[0].content == "Use CNY"
    assert user_records[0].content == "Use USD"
    assert store.search("CNY", ["project"])[0].namespace == "project"


def test_memory_store_expires_records(tmp_path):
    store = MemoryStore(tmp_path / "agent_memory.sqlite")

    store.write("session:abc", "summary", "temporary", expires_in_seconds=-1)

    assert store.load_namespace("session:abc") == []


def test_memory_store_clear_removes_records_and_vectors(tmp_path):
    store = MemoryStore(tmp_path / "agent_memory.sqlite")

    memory_id = store.write("project", "metric_rule", "Use cabinet count.")
    store.upsert_vector(
        memory_id=memory_id,
        namespace="project",
        embedding_model="fake",
        content_hash="hash",
        vector=[1.0, 0.0],
    )

    store.clear()

    assert store.load_namespace("project") == []
    assert store.load_vectors(embedding_model="fake", namespaces=["project"]) == []
    assert store.search("cabinet", ["project"]) == []
