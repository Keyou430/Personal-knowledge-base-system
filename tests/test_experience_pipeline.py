from core.experience_pipeline import merge_experience, prepare_save
from core.experience_store import ExperienceStore
from tests.test_experience_store import make_draft


def test_prepare_save_returns_exact_duplicate_without_writing(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    existing = store.create(make_draft(title="重复"))

    result = prepare_save(store, make_draft(title="重复"), semantic_matches=[])

    assert result.kind == "exact_duplicate"
    assert result.matches[0].id == existing.id
    assert len(store.list()) == 1


def test_prepare_save_returns_semantic_review_candidates(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    existing = store.create(make_draft(title="相近经验"))

    result = prepare_save(store, make_draft(title="不同标题"), semantic_matches=[existing])

    assert result.kind == "needs_review"
    assert result.matches == [existing]


def test_merge_uses_new_content_unions_tags_and_versions(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    existing = store.create(make_draft(tags=["旧标签"]))

    merged = merge_experience(
        store,
        existing.id,
        make_draft(title="新标题", tags=["新标签"]),
    )

    assert merged.title == "新标题"
    assert set(merged.tags) == {"旧标签", "新标签"}
    assert store.get_versions(existing.id)[-1]["change_type"] == "merged"
