"""Tests for the interactive Study Session Coach (AI Coach + Practice Mode)."""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app as app_module
from studygenie import ai, db


@pytest.fixture
def client():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    old = app_module.DB_PATH
    app_module.DB_PATH = path
    app_module.SESSIONS.clear()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client
    app_module.DB_PATH = old
    app_module.SESSIONS.clear()
    os.unlink(path)


def make_course(client, name="Databases", topics=("Joins", "Indexing")):
    exam = (date.today() + timedelta(days=10)).isoformat()
    resp = client.post("/courses/new", data={"name": name, "exam_date": exam})
    cid = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    tids = []
    for title in topics:
        with db.connect(app_module.DB_PATH) as conn:
            cur = conn.execute(
                "INSERT INTO topics (course_id, title, difficulty)"
                " VALUES (?, ?, 3)", (cid, title))
            tids.append(cur.lastrowid)
    return cid, tids


def start_practice(client, cid, tids, length=5):
    resp = client.post(f"/courses/{cid}/session/start", data={
        "topic_ids": [str(t) for t in tids],
        "length": str(length), "mode": "practice"})
    assert resp.status_code == 302
    return resp.headers["Location"].rsplit("/", 1)[-1]


# --- route and setup ---

def test_setup_page_200(client):
    cid, _ = make_course(client)
    resp = client.get(f"/courses/{cid}/session")
    assert resp.status_code == 200
    assert "Study session" in resp.get_data(as_text=True)


def test_setup_missing_course_404(client):
    assert client.get("/courses/99999/session").status_code == 404
    assert client.post("/courses/99999/session/start").status_code == 404


def test_course_page_exposes_session_action(client):
    cid, _ = make_course(client)
    html = client.get(f"/courses/{cid}").get_data(as_text=True)
    assert f"/courses/{cid}/session" in html


def test_empty_course_shows_empty_state(client):
    cid, _ = make_course(client, topics=())
    html = client.get(f"/courses/{cid}/session").get_data(as_text=True)
    assert resp_ok(html)
    assert "no topics" in html.lower()


def resp_ok(html):
    return "Study session" in html


def test_length_restricted_to_valid_choices(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "generate_coach_question",
                        lambda *a, **k: {"enabled": True, "question": "Q?"})
    cid, tids = make_course(client)
    token = start_practice(client, cid, tids, length=99)
    assert app_module.SESSIONS[token]["length"] == 5  # invalid -> default


def test_no_key_practice_available_ai_disabled(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cid, _ = make_course(client)
    html = client.get(f"/courses/{cid}/session").get_data(as_text=True)
    assert "Practice Mode" in html
    assert "needs GEMINI_API_KEY" in html


def test_no_key_never_invokes_gemini(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ai.generate_coach_question("C", []) == {"enabled": False}
    assert ai.evaluate_coach_answer("Q", "A", "C", []) == {"enabled": False}


def test_ai_mode_rejected_without_key(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cid, tids = make_course(client)
    resp = client.post(f"/courses/{cid}/session/start", data={
        "topic_ids": [str(t) for t in tids], "mode": "ai"})
    assert resp.status_code == 400
    assert "Practice Mode" in resp.get_data(as_text=True)


# --- session behavior (practice mode) ---

def test_start_initializes_clean_state(client):
    cid, tids = make_course(client)
    token = start_practice(client, cid, tids)
    sess = app_module.SESSIONS[token]
    assert sess["answers"] == [] and sess["feedbacks"] == []
    assert sess["current"] == 0 and not sess["finished"]
    assert sess["mode"] == "practice"


def test_one_question_at_a_time(client):
    cid, tids = make_course(client)
    token = start_practice(client, cid, tids)
    html = client.get(f"/session/{token}").get_data(as_text=True)
    assert html.count('class="question-card"') == 1
    assert "Question 1 of" in html


def test_empty_answer_rejected(client):
    cid, tids = make_course(client)
    token = start_practice(client, cid, tids)
    resp = client.post(f"/session/{token}/answer", data={"answer": "   "},
                       follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "Please write an answer" in html
    assert app_module.SESSIONS[token]["current"] == 0  # not advanced


def test_oversized_answer_rejected_with_input_preserved(client):
    cid, tids = make_course(client)
    token = start_practice(client, cid, tids)
    big = "x" * (ai.MAX_ANSWER_CHARS + 100)
    resp = client.post(f"/session/{token}/answer", data={"answer": big},
                       follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "too long" in html
    assert "x" * 50 in html  # truncated input preserved for editing
    assert app_module.SESSIONS[token]["current"] == 0


def test_practice_answer_advances_and_reviews(client):
    cid, tids = make_course(client)
    token = start_practice(client, cid, tids)
    client.post(f"/session/{token}/answer", data={"answer": "my answer"})
    sess = app_module.SESSIONS[token]
    assert sess["current"] == 1
    assert sess["answers"] == ["my answer"]
    # finish early -> review shows the interaction
    client.post(f"/session/{token}/finish")
    html = client.get(f"/session/{token}").get_data(as_text=True)
    assert "Session review" in html
    assert "my answer" in html
    assert "1 question answered" in html


def test_new_session_does_not_reuse_answers(client):
    cid, tids = make_course(client)
    t1 = start_practice(client, cid, tids)
    client.post(f"/session/{t1}/answer", data={"answer": "old"})
    t2 = start_practice(client, cid, tids)
    assert app_module.SESSIONS[t2]["answers"] == []
    assert t1 != t2


def test_invalid_session_token_404(client):
    assert client.get("/session/nope").status_code == 404
    assert client.post("/session/nope/answer").status_code == 404
    assert client.post("/session/nope/finish").status_code == 404


def test_refresh_after_answer_does_not_resubmit(client):
    cid, tids = make_course(client)
    token = start_practice(client, cid, tids)
    # POST then GET (PRG): the GET must not create a second answer
    client.post(f"/session/{token}/answer", data={"answer": "once"})
    client.get(f"/session/{token}")
    client.get(f"/session/{token}")
    assert app_module.SESSIONS[token]["answers"] == ["once"]


# --- practice mode content ---

def test_practice_questions_come_from_topics(client):
    cid, tids = make_course(client, topics=("Normalization",))
    token = start_practice(client, cid, tids)
    sess = app_module.SESSIONS[token]
    assert all("Normalization" in q["question"] for q in sess["questions"])


def test_practice_insufficient_material_is_truthful(client):
    cid, tids = make_course(client, topics=("Solo",))
    token = start_practice(client, cid, tids, length=15)
    sess = app_module.SESSIONS[token]
    assert len(sess["questions"]) < 15  # not padded with duplicates
    assert len({q["question"] for q in sess["questions"]}) == len(
        sess["questions"])  # no duplicates
    html = client.get(f"/session/{token}").get_data(as_text=True)
    assert "Only" in html and "rather than repeat" in html


def test_practice_makes_no_ai_claims(client):
    cid, tids = make_course(client)
    token = start_practice(client, cid, tids)
    client.post(f"/session/{token}/answer", data={"answer": "a"})
    client.post(f"/session/{token}/finish")
    html = client.get(f"/session/{token}").get_data(as_text=True)
    assert "doesn't grade" in html
    assert "AI" not in html.replace("Practice Mode", "").replace(
        "AI Coach", "") or "AI evaluation" not in html


# --- AI coach behavior (mocked) ---

def _mock_coach(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "generate_coach_question",
                        lambda name, topics, history=None: {
                            "enabled": True, "question": "Explain joins?"})
    monkeypatch.setattr(ai, "evaluate_coach_answer",
                        lambda q, a, name, topics: {
                            "enabled": True, "covered": "defined inner join",
                            "missed": "did not mention outer joins",
                            "suggestion": "contrast inner vs outer",
                            "followup": "When would you use a left join?"})


def _start_ai(client, cid, tids, length=5):
    resp = client.post(f"/courses/{cid}/session/start", data={
        "topic_ids": [str(t) for t in tids],
        "length": str(length), "mode": "ai"})
    assert resp.status_code == 302
    return resp.headers["Location"].rsplit("/", 1)[-1]


def test_ai_question_generation_uses_topics(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    model = None

    class FakeModel:
        def generate_content(self, prompt):
            assert "Joins" in prompt
            return type("Resp", (), {"text": "What is a join?"})()

    monkeypatch.setattr(ai, "_model", lambda: FakeModel())
    result = ai.generate_coach_question("Databases", [{"title": "Joins"}])
    assert result["question"] == "What is a join?"


def test_ai_followup_uses_history(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    seen = []

    class FakeModel:
        def generate_content(self, prompt):
            seen.append(prompt)
            return type("Resp", (), {"text": "Follow-up?"})()

    monkeypatch.setattr(ai, "_model", lambda: FakeModel())
    history = [{"question": "Q1", "answer": "A1", "feedback": "F1"}]
    ai.generate_coach_question("DB", [{"title": "T"}], history)
    assert "A1" in seen[0] and "F1" in seen[0]


def test_ai_evaluate_returns_structured_feedback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "_model", lambda: FakeModel2())
    result = ai.evaluate_coach_answer("Q?", "my answer", "DB",
                                      [{"title": "T"}])
    assert result["covered"] and result["followup"]
    assert result["enabled"] is True


class FakeModel2:
    def generate_content(self, prompt):
        import json
        return type("Resp", (), {"text": json.dumps({
            "covered": "c", "missed": "m",
            "suggestion": "s", "followup": "f?"})})()


def test_ai_evaluate_rejects_incomplete_feedback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    class Incomplete:
        def generate_content(self, prompt):
            return type("Resp", (), {"text": '{"covered": "c"}'})()

    monkeypatch.setattr(ai, "_model", lambda: Incomplete())
    result = ai.evaluate_coach_answer("Q?", "A", "DB", [{"title": "T"}])
    assert "incomplete" in result["error"]


def test_ai_answer_flow_advances_on_success(client, monkeypatch):
    _mock_coach(monkeypatch)
    cid, tids = make_course(client)
    token = _start_ai(client, cid, tids)
    client.post(f"/session/{token}/answer", data={"answer": "joins combine"})
    sess = app_module.SESSIONS[token]
    assert sess["current"] == 1
    assert sess["answers"] == ["joins combine"]
    assert sess["feedbacks"][0]["covered"] == "defined inner join"
    # follow-up became the next question
    assert sess["questions"][1]["question"] == "When would you use a left join?"


def test_ai_failure_preserves_answer_and_allows_retry(client, monkeypatch):
    _mock_coach(monkeypatch)
    cid, tids = make_course(client)
    token = _start_ai(client, cid, tids)

    calls = {"n": 0}

    def flaky(q, a, name, topics):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"enabled": True, "error": "quota exceeded"}
        return {"enabled": True, "covered": "c", "missed": "",
                "suggestion": "s", "followup": "next?"}

    monkeypatch.setattr(ai, "evaluate_coach_answer", flaky)
    resp = client.post(f"/session/{token}/answer",
                       data={"answer": "my kept answer"},
                       follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "quota exceeded" in html
    assert "my kept answer" in html  # preserved
    sess = app_module.SESSIONS[token]
    assert sess["current"] == 0  # not advanced
    assert sess["answers"] == []

    # retry succeeds without duplication
    client.post(f"/session/{token}/answer",
                data={"answer": "my kept answer"})
    sess = app_module.SESSIONS[token]
    assert sess["current"] == 1
    assert sess["answers"] == ["my kept answer"]


def test_ai_session_ends_after_configured_length(client, monkeypatch):
    _mock_coach(monkeypatch)
    cid, tids = make_course(client)
    token = _start_ai(client, cid, tids, length=5)
    for i in range(5):
        client.post(f"/session/{token}/answer", data={"answer": f"a{i}"})
    sess = app_module.SESSIONS[token]
    assert sess["finished"] is True
    assert len(sess["answers"]) == 5
    html = client.get(f"/session/{token}").get_data(as_text=True)
    assert "Session review" in html
    assert "5 questions answered" in html


def test_ai_review_shows_feedback_not_fabricated_metrics(client, monkeypatch):
    _mock_coach(monkeypatch)
    cid, tids = make_course(client)
    token = _start_ai(client, cid, tids)
    client.post(f"/session/{token}/answer", data={"answer": "a"})
    client.post(f"/session/{token}/finish")
    html = client.get(f"/session/{token}").get_data(as_text=True)
    assert "defined inner join" in html
    assert "master" not in html.lower() or "mastery" not in html.lower()
    assert "%" not in html


# --- security ---

def test_foreign_topic_ids_rejected(client, monkeypatch):
    _mock_coach(monkeypatch)
    cid, tids = make_course(client)
    other_cid, other_tids = make_course(client, name="Math")
    resp = client.post(f"/courses/{cid}/session/start", data={
        "topic_ids": [str(other_tids[0])], "mode": "practice"})
    assert resp.status_code == 400  # no valid topics for this course


def test_user_input_rendered_safely(client):
    cid, tids = make_course(client)
    token = start_practice(client, cid, tids)
    client.post(f"/session/{token}/answer",
                data={"answer": "<script>alert(1)</script>"})
    client.post(f"/session/{token}/finish")
    html = client.get(f"/session/{token}").get_data(as_text=True)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_no_credentials_in_responses(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    cid, tids = make_course(client)
    for path in [f"/courses/{cid}/session",
                 f"/courses/{cid}/guide",
                 "/notes", "/flashcards"]:
        html = client.get(path).get_data(as_text=True)
        assert "fake-key" not in html


def test_practice_question_helpers():
    import app as app_module
    topics = [{"title": "A"}, {"title": "B"}]
    qs = app_module._practice_questions(topics, 5)
    assert len(qs) == 5
    assert len({q["question"] for q in qs}) == 5  # all distinct
    assert all(q["topic"] in ("A", "B") for q in qs)
