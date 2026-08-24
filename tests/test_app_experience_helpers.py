import app


def test_chat_entry_id_is_stable_for_same_question_and_answer():
    first = app.make_chat_entry("问题", "回答", [])
    second = app.make_chat_entry("问题", "回答", [])

    assert first["id"] == second["id"]
    assert first["question"] == "问题"


def test_render_active_view_renders_only_selected_view(monkeypatch):
    called = []
    monkeypatch.setattr(app, "render_qa_section", lambda: called.append("qa"))
    monkeypatch.setattr(app, "render_experience_section", lambda: called.append("experience"))

    app.render_active_view(app.VIEW_QA)

    assert called == ["qa"]


def test_select_experience_sources_returns_only_user_checked_sources():
    sources = [
        {"source": "one.md", "excerpt": "第一段"},
        {"source": "two.md", "excerpt": "第二段"},
    ]

    selected = app.select_experience_sources(sources, [False, True])

    assert selected == [{"source": "two.md", "excerpt": "第二段"}]
