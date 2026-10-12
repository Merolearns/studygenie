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
    monkeypatch.setattr(ai, "generate_quiz", lambda title: {
        "enabled": True, "topic": title,
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
