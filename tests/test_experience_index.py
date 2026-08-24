from core.experience_index import ExperienceIndex
from core.experience_store import ExperienceStore
from core.experience_pipeline import save_new_experience
from tests.test_experience_store import make_draft


class FailingIndex:
    def add(self, card):
        raise RuntimeError("index unavailable")


def test_index_failure_marks_saved_card_pending_without_losing_it(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")

    saved = save_new_experience(store, make_draft(), index=FailingIndex())

    assert store.get(saved.id) is not None
    assert store.get(saved.id).index_pending is True


def test_in_memory_index_can_add_search_delete_and_rebuild(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    index = ExperienceIndex(store, backend="memory")
    card = store.create(make_draft(title="排查服务故障"))

    index.add(card)
    assert index.search("服务故障")[0].id == card.id
    index.delete(card.id)
    assert index.search("服务故障") == []
    index.rebuild()
    assert index.search("服务故障")[0].id == card.id


def test_chroma_index_replaces_existing_card_before_adding(tmp_path):
    class RecordingVectorStore:
        def __init__(self):
            self.operations = []

        def delete(self, *, ids):
            self.operations.append(("delete", ids))

        def add_documents(self, documents, *, ids):
            self.operations.append(("add", ids))

    store = ExperienceStore(tmp_path / "experiences.db")
    card = store.create(make_draft())
    index = ExperienceIndex.__new__(ExperienceIndex)
    index.backend = "chroma"
    index._vectorstore = RecordingVectorStore()

    index.add(card)

    assert index._vectorstore.operations == [
        ("delete", [card.id]),
        ("add", [card.id]),
    ]
