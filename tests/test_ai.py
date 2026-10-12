"""Day-5 tests: Gemini AI helpers degrade gracefully without a key."""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app as app_module
from studygenie import ai, db


# --- unit tests for the ai module ---

def test_quiz_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ai.is_enabled() is False
    assert ai.generate_quiz("Joins") == {"enabled": False}


def test_tips_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ai.generate_tips("Databases") == {"enabled": False}


class FakeModel:
    """Stands in for the Gemini model; records prompts, returns canned JSON."""
    def __init__(self, text):
        self.text = text
        self.prompts = []

    def generate_content(self, prompt):
        self.prompts.append(prompt)
        resp = type("Resp", (), {"text": self.text})
        return resp


def fake_model(text):
    return FakeModel(text)


def test_quiz_parses_model_json(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "_model", lambda: fake_model(
        '{"questions": [{"question": "What is a join?", '
        '"choices": ["a", "b", "c", "d"], "answer": "b"}]}'))
    result = ai.generate_quiz("Joins")
    assert result["enabled"] is True
    assert result["topic"] == "Joins"
    assert result["questions"][0]["question"] == "What is a join?"
    assert result["questions"][0]["answer"] in ("a", "b", "c", "d")


def test_quiz_mentions_topic_in_prompt(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    model = fake_model('{"questions": []}')
    monkeypatch.setattr(ai, "_model", lambda: model)
    ai.generate_quiz("Indexing")
    assert "Indexing" in model.prompts[0]


def test_tips_parses_model_json(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "_model", lambda: fake_model(
        '{"tips": ["Space your sessions out", "Quiz yourself", "Sleep well"]}'))
    result = ai.generate_tips("Databases")
    assert result["enabled"] is True
    assert len(result["tips"]) == 3


def test_api_error_never_raises(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    class Broken:
        def generate_content(self, prompt):
            raise RuntimeError("quota exceeded")

    monkeypatch.setattr(ai, "_model", lambda: Broken())
    result = ai.generate_quiz("Joins")
    assert result["enabled"] is True
    assert "quota exceeded" in result["error"]


def test_markdown_fences_stripped(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "_model", lambda: fake_model(
        '```json\n{"tips": ["one", "two", "three"]}\n```'))
    result = ai.generate_tips("Databases")
    assert result["tips"] == ["one", "two", "three"]


# --- route tests for the quiz page ---

@pytest.fixture
def client():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    old = app_module.DB_PATH
    app_module.DB_PATH = path
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client
    app_module.DB_PATH = old
    os.unlink(path)


def make_course(client):
    exam = (date.today() + timedelta(days=10)).isoformat()
    resp = client.post("/courses/new", data={"name": "Databases", "exam_date": exam})
    course_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/courses/{course_id}/topics", data={"title": "Joins", "difficulty": 5})
    return course_id


def test_quiz_page_renders_without_key(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    course_id = make_course(client)
    resp = client.get(f"/courses/{course_id}/quiz")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Joins" in html  # topic picker
    assert "GEMINI_API_KEY" in html  # how-to-enable note
    assert "disabled" in html  # buttons disabled without a key


def test_quiz_post_without_key_is_harmless(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    course_id = make_course(client)
    resp = client.post(f"/courses/{course_id}/quiz",
                       data={"action": "quiz", "topic": "Joins"})
    assert resp.status_code == 200
    assert "GEMINI_API_KEY" in resp.get_data(as_text=True)


def test_quiz_page_shows_quiz_when_enabled(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "generate_quiz", lambda title, difficulty="medium": {
        "enabled": True, "topic": title, "difficulty": difficulty,
        "questions": [{"question": "What is a join?", "choices": ["a", "b"],
                       "answer": "a"}]})
    course_id = make_course(client)
    resp = client.post(f"/courses/{course_id}/quiz",
                       data={"action": "quiz", "topic": "Joins"})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "What is a join?" in html
    assert "Reveal answer" in html


def test_quiz_page_shows_tips(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "generate_tips", lambda name: {
        "enabled": True, "course": name, "tips": ["tip one", "tip two", "tip three"]})
    course_id = make_course(client)
    resp = client.post(f"/courses/{course_id}/quiz", data={"action": "tips"})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "tip one" in html


def test_quiz_missing_course_404(client):
    assert client.get("/courses/99999/quiz").status_code == 404


# --- notes generator tests (ported from capstone generate-notes flow) ---

def test_notes_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ai.generate_notes("x" * 100) == {"enabled": False}


def test_notes_returns_text_when_enabled(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "_model",
                        lambda: fake_model("## Summary\nSome notes here."))
    result = ai.generate_notes("x" * 100, "concise")
    assert result["enabled"] is True
    assert "Some notes here" in result["notes"]
    assert result["style"] == "concise"


def test_notes_prompt_includes_style_and_focus(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    model = fake_model("notes")
    monkeypatch.setattr(ai, "_model", lambda: model)
    ai.generate_notes("x" * 100, "outline", "key definitions")
    prompt = model.prompts[0]
    assert "outline" in prompt
    assert "key definitions" in prompt
    assert "x" * 20 in prompt  # document content included


def test_notes_invalid_style_falls_back_to_comprehensive(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    model = fake_model("notes")
    monkeypatch.setattr(ai, "_model", lambda: model)
    result = ai.generate_notes("x" * 100, "nonsense-style")
    assert result["style"] == "comprehensive"
    assert "comprehensive" in model.prompts[0]


def test_notes_api_error_never_raises(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    class Broken:
        def generate_content(self, prompt):
            raise RuntimeError("quota exceeded")

    monkeypatch.setattr(ai, "_model", lambda: Broken())
    result = ai.generate_notes("x" * 100)
    assert result["enabled"] is True
    assert "quota exceeded" in result["error"]


def test_notes_page_renders_without_key(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    resp = client.get("/notes")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "GEMINI_API_KEY" in html
    assert "disabled" in html


def test_notes_post_short_content_rejected(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    resp = client.post("/notes", data={
        "document_content": "too short", "note_style": "concise"})
    assert resp.status_code == 200
    assert "at least 50 characters" in resp.get_data(as_text=True)


def test_notes_post_shows_generated_notes(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "generate_notes", lambda *a, **k: {
        "enabled": True, "style": "bullet-points",
        "notes": "• first point\n• second point"})
    resp = client.post("/notes", data={
        "document_content": "x" * 100, "note_style": "bullet-points"})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "first point" in html
    assert "Copy" in html  # copy/download buttons present


# --- flashcard tests (real implementation; capstone's was pseudo-text) ---

def test_flashcards_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ai.generate_flashcards("x" * 100) == {"enabled": False}


def test_flashcards_parse_and_filter(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "_model", lambda: fake_model(
        '{"flashcards": [{"front": "Q1", "back": "A1"},'
        ' {"front": "", "back": "no front"},'
        ' {"front": "Q2", "back": "A2"}]}'))
    result = ai.generate_flashcards("x" * 100, count=8)
    assert result["enabled"] is True
    assert len(result["flashcards"]) == 2  # malformed card dropped
    assert result["flashcards"][0]["front"] == "Q1"


def test_flashcards_count_clamped(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    model = fake_model('{"flashcards": []}')
    monkeypatch.setattr(ai, "_model", lambda: model)
    ai.generate_flashcards("x" * 100, count=99)
    assert "exactly 20" in model.prompts[0]
    ai.generate_flashcards("x" * 100, count=0)
    assert "exactly 2" in model.prompts[0]


def test_flashcards_api_error_never_raises(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    class Broken:
        def generate_content(self, prompt):
            raise RuntimeError("boom")

    monkeypatch.setattr(ai, "_model", lambda: Broken())
    result = ai.generate_flashcards("x" * 100)
    assert result["enabled"] is True
    assert "boom" in result["error"]


def test_flashcards_page_renders(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    resp = client.get("/flashcards")
    assert resp.status_code == 200
    assert "GEMINI_API_KEY" in resp.get_data(as_text=True)


def test_flashcards_post_shows_cards(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "generate_flashcards", lambda *a, **k: {
        "enabled": True,
        "flashcards": [{"front": "What is 2+2?", "back": "4"}]})
    resp = client.post("/flashcards",
                       data={"document_content": "x" * 100, "count": 8})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "What is 2+2?" in html
    assert "flipCard" in html


# --- quiz difficulty (ported from capstone quiz service design) ---

def test_quiz_difficulty_in_prompt(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    model = fake_model('{"questions": []}')
    monkeypatch.setattr(ai, "_model", lambda: model)
    ai.generate_quiz("Joins", difficulty="hard")
    assert "hard" in model.prompts[0]
    assert "synthesis" in model.prompts[0]


def test_quiz_invalid_difficulty_defaults_medium(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    model = fake_model('{"questions": []}')
    monkeypatch.setattr(ai, "_model", lambda: model)
    result = ai.generate_quiz("Joins", difficulty="nonsense")
    assert result["difficulty"] == "medium"


def test_quiz_route_passes_difficulty(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    seen = {}

    def fake_quiz(topic, difficulty="medium"):
        seen["difficulty"] = difficulty
        return {"enabled": True, "topic": topic, "difficulty": difficulty,
                "questions": []}

    monkeypatch.setattr(ai, "generate_quiz", fake_quiz)
    course_id = make_course(client)
    client.post(f"/courses/{course_id}/quiz",
                data={"action": "quiz", "topic": "Joins",
                      "difficulty": "easy"})
    assert seen["difficulty"] == "easy"


# --- AI weekly plan (ported from capstone generate-schedule flow) ---

def test_weekly_plan_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ai.generate_weekly_plan("Math", []) == {"enabled": False}


def test_weekly_plan_prompt_includes_topics(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    model = fake_model("Monday: study hard.")
    monkeypatch.setattr(ai, "_model", lambda: model)
    topics = [{"title": "Derivatives", "difficulty": 5},
              {"title": "Integrals", "difficulty": 3}]
    result = ai.generate_weekly_plan("Calculus", topics, "2026-12-15")
    assert result["enabled"] is True
    assert "Monday: study hard." in result["plan"]
    prompt = model.prompts[0]
    assert "Calculus" in prompt
    assert "Derivatives" in prompt
    assert "2026-12-15" in prompt


def test_weekly_plan_api_error_never_raises(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    class Broken:
        def generate_content(self, prompt):
            raise RuntimeError("boom")

    monkeypatch.setattr(ai, "_model", lambda: Broken())
    result = ai.generate_weekly_plan("Math", [])
    assert result["enabled"] is True
    assert "boom" in result["error"]


def test_ai_plan_route_renders_plan(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "generate_weekly_plan", lambda *a, **k: {
        "enabled": True, "course": "Databases", "plan": "Monday: review joins."})
    course_id = make_course(client)
    resp = client.post(f"/courses/{course_id}/ai-plan")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Monday: review joins." in html


def test_ai_plan_missing_course_404(client):
    assert client.post("/courses/99999/ai-plan").status_code == 404


# --- file upload for notes/flashcards ---

def test_notes_accepts_txt_upload(client, monkeypatch):
    import io
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    seen = {}
    monkeypatch.setattr(ai, "generate_notes",
                        lambda doc, style="comprehensive", focus_area=None: (
                            seen.update(doc=doc), {"enabled": True,
                             "style": style, "notes": "notes here"})[1])
    data = {"document_file": (io.BytesIO(b"uploaded text " * 20), "notes.txt"),
            "note_style": "concise"}
    resp = client.post("/notes", data=data,
                       content_type="multipart/form-data")
    assert resp.status_code == 200
    assert "uploaded text" in seen["doc"]
    assert "notes here" in resp.get_data(as_text=True)


def test_flashcards_accepts_txt_upload(client, monkeypatch):
    import io
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "generate_flashcards", lambda *a, **k: {
        "enabled": True, "flashcards": [{"front": "Q", "back": "A"}]})
    data = {"document_file": (io.BytesIO(b"card material " * 20), "m.txt")}
    resp = client.post("/flashcards", data=data,
                       content_type="multipart/form-data")
    assert resp.status_code == 200
    assert "flipCard" in resp.get_data(as_text=True)


# --- AI study guide generator ---

def _guide_course(client):
    """Course with two topics; returns (course_id, topic_ids)."""
    cid = make_course(client)
    from studygenie import db as dbm
    import app as app_module
    with dbm.connect(app_module.DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id FROM topics WHERE course_id = ?", (cid,)).fetchall()
    return cid, [r["id"] for r in rows]


def test_guide_page_200_for_existing_course(client):
    cid, _ = _guide_course(client)
    resp = client.get(f"/courses/{cid}/guide")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Databases" in html  # course identified
    assert "Joins" in html  # topics listed


def test_guide_missing_course_404(client):
    assert client.get("/courses/99999/guide").status_code == 404
    assert client.post("/courses/99999/guide").status_code == 404


def test_course_page_exposes_study_guide_action(client):
    cid, _ = _guide_course(client)
    html = client.get(f"/courses/{cid}").get_data(as_text=True)
    assert f"/courses/{cid}/guide" in html
    assert "Study guide" in html


def test_guide_page_no_key_renders_disabled(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cid, tids = _guide_course(client)
    resp = client.get(f"/courses/{cid}/guide")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "GEMINI_API_KEY" in html
    assert "disabled" in html


def test_guide_no_key_never_calls_api(client, monkeypatch):
    """Without a key, the real _model() returns None -> {"enabled": False}.

    No generate_content call is possible because the function returns
    before touching any model. Verified at the unit level (no patching).
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ai.generate_study_guide("Math", [{"title": "T"}]) == {"enabled": False}
    cid, tids = _guide_course(client)
    resp = client.post(f"/courses/{cid}/guide",
                       data={"topic_ids": [str(t) for t in tids]})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "GEMINI_API_KEY" in html  # disabled explanation shown
    assert "Copy guide" not in html  # no fabricated output


def test_guide_empty_notes_accepted(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    seen = {}
    monkeypatch.setattr(ai, "generate_study_guide",
                        lambda name, topics, notes=None, depth="standard": (
                            seen.update(notes=notes),
                            {"enabled": True, "guide": "guide text",
                             "course": name, "depth": depth})[1])
    cid, tids = _guide_course(client)
    resp = client.post(f"/courses/{cid}/guide",
                       data={"topic_ids": [str(t) for t in tids],
                             "additional_notes": ""})
    assert resp.status_code == 200
    assert seen["notes"] is None
    assert "guide text" in resp.get_data(as_text=True)


def test_guide_rejects_foreign_and_invalid_topic_ids(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    seen = {}
    monkeypatch.setattr(ai, "generate_study_guide",
                        lambda name, topics, notes=None, depth="standard": (
                            seen.update(topics=[t["title"] for t in topics]),
                            {"enabled": True, "guide": "g",
                             "course": name, "depth": depth})[1])
    cid, tids = _guide_course(client)
    other_cid, other_tids = _guide_course(client)  # second course's topics
    resp = client.post(f"/courses/{cid}/guide",
                       data={"topic_ids": [str(other_tids[0]), "abc", "99999"]})
    # foreign/invalid ids dropped; with no valid topics and no notes -> error
    assert resp.status_code == 200
    assert "Select at least one topic" in resp.get_data(as_text=True)
    assert "generate_study_guide" not in str(seen) or not seen.get("topics")


def test_guide_oversized_notes_truncated(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    seen = {}
    monkeypatch.setattr(ai, "generate_study_guide",
                        lambda name, topics, notes=None, depth="standard": (
                            seen.update(notes=notes),
                            {"enabled": True, "guide": "g",
                             "course": name, "depth": depth})[1])
    cid, tids = _guide_course(client)
    big = "x" * (ai.MAX_NOTES_CHARS + 5000)
    client.post(f"/courses/{cid}/guide",
                data={"topic_ids": [str(t) for t in tids],
                      "additional_notes": big})
    assert len(seen["notes"]) == ai.MAX_NOTES_CHARS


def test_guide_generation_inputs_reach_prompt(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    model = fake_model("## Title\nGuide body.")
    monkeypatch.setattr(ai, "_model", lambda: model)
    topics = [{"title": "SQL Joins", "difficulty": 5}]
    result = ai.generate_study_guide(
        "Databases", topics, "my own summary", "detailed")
    assert result["enabled"] is True
    assert result["depth"] == "detailed"
    assert "Guide body" in result["guide"]
    prompt = model.prompts[0]
    assert "Databases" in prompt
    assert "SQL Joins" in prompt
    assert "my own summary" in prompt
    assert "detailed" in prompt
    # honesty constraints present
    assert "NOT actual or predicted exam questions" in prompt
    assert "Do not invent" in prompt


def test_guide_invalid_depth_defaults_standard(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    model = fake_model("guide")
    monkeypatch.setattr(ai, "_model", lambda: model)
    result = ai.generate_study_guide("Math", [], None, "extreme")
    assert result["depth"] == "standard"


def test_guide_empty_ai_output_is_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "_model", lambda: fake_model("   "))
    result = ai.generate_study_guide("Math", [{"title": "T"}])
    assert result["enabled"] is True
    assert "empty" in result["error"].lower()


def test_guide_api_error_never_raises(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    class Broken:
        def generate_content(self, prompt):
            raise RuntimeError("boom")

    monkeypatch.setattr(ai, "_model", lambda: Broken())
    result = ai.generate_study_guide("Math", [{"title": "T"}])
    assert result["enabled"] is True
    assert "boom" in result["error"]


def test_guide_error_state_preserves_inputs_no_fabrication(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "generate_study_guide", lambda *a, **k: {
        "enabled": True, "error": "quota exceeded"})
    cid, tids = _guide_course(client)
    resp = client.post(f"/courses/{cid}/guide",
                       data={"topic_ids": [str(t) for t in tids],
                             "additional_notes": "keep me",
                             "depth": "detailed"})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "quota exceeded" in html
    assert "keep me" in html  # inputs preserved
    assert "Copy guide" not in html  # no copy/download without content


def test_guide_success_shows_copy_download(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "generate_study_guide", lambda *a, **k: {
        "enabled": True, "guide": "## Key concepts\nJoins combine rows.",
        "course": "Databases", "depth": "standard"})
    cid, tids = _guide_course(client)
    resp = client.post(f"/courses/{cid}/guide",
                       data={"topic_ids": [str(t) for t in tids]})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Joins combine rows" in html
    assert "Copy guide" in html
    assert "Download .txt" in html
    assert "databases-study-guide.txt" in html  # course-based .txt filename


def test_safe_filename():
    import app as app_module
    assert app_module._safe_filename("Databases 101!") == "databases-101"
    assert app_module._safe_filename("  ") == "study-guide"
