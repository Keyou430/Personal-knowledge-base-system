import json

from core.experience_store import ExperienceDraft, ExperienceStore


def make_draft(**overrides):
    values = {
        "title": "排查部署失败",
        "scenario": "发布后服务不可用",
        "conclusion": "先检查最近一次配置变更",
        "steps": ["查看日志"],
        "tags": ["排障"],
        "sources": [{"source": "runbook.md", "excerpt": "检查日志"}],
        "question": "服务为什么不可用？",
        "answer_excerpt": "先检查配置变更。",
    }
    values.update(overrides)
    return ExperienceDraft(**values)


def test_create_persists_card_source_and_version(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    saved = store.create(make_draft(title="排查部署失败"))

    assert saved.title == "排查部署失败"
    assert saved.status == "active"
    assert store.get_sources(saved.id)[0]["source"] == "runbook.md"
    assert store.get_versions(saved.id)[0]["change_type"] == "created"


def test_exact_duplicate_ignores_whitespace_and_tag_order(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    first = store.create(make_draft(tags=["a", "b"]))

    duplicate = store.find_exact_duplicate(
        make_draft(title="  排查部署失败 ", tags=["b", "a"])
    )

    assert duplicate.id == first.id


def test_update_archives_and_restores_with_history(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    card = store.create(make_draft())

    store.update(card.id, make_draft(title="更新后"), change_type="edited")
    store.archive(card.id)
    assert store.list() == []

    store.restore(card.id)
    assert store.get(card.id).title == "更新后"
    assert [version["change_type"] for version in store.get_versions(card.id)] == [
        "created",
        "edited",
        "archived",
        "restored",
    ]
