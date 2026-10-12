"""Phase 1 reliability regression tests: input validation, upload limits,
session bounds. Uses isolated temp databases; never touches real data."""
import io
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


def _course_count():
    with db.connect(app_module.DB_PATH) as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM courses").fetchone()["n"]


def _topic_count():
    with db.connect(app_module.DB_PATH) as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM topics").fetchone()["n"]


def make_course(client, name="Physics", exam="2026-12-20"):
    resp = client.post("/courses/new", data={"name": name, "exam_date": exam})
    assert resp.status_code == 302
    return int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])


# --- 1. exam date validation ---

def test_valid_exam_date_creates_course(client):
    cid = make_course(client, exam="2026-12-20")
    assert db.get_course(app_module.DB_PATH, cid)["exam_date"] == "2026-12-20"


def test_malformed_exam_date_rejected_no_record(client):
    before = _course_count()
    resp = client.post("/courses/new",
                       data={"name": "Bad", "exam_date": "not-a-date"})
    assert resp.status_code == 200  # re-rendered form, not a redirect
    assert "valid exam date" in resp.get_data(as_text=True)
    assert _course_count() == before


def test_impossible_exam_date_rejected(client):
    before = _course_count()
    resp = client.post("/courses/new",
                       data={"name": "Bad", "exam_date": "2026-02-30"})
    assert resp.status_code == 200
    assert _course_count() == before


def test_missing_exam_date_rejected(client):
    before = _course_count()
    resp = client.post("/courses/new", data={"name": "NoDate", "exam_date": ""})
    assert resp.status_code == 200
    assert _course_count() == before


def test_missing_name_rejected(client):
    before = _course_count()
    resp = client.post("/courses/new",
                       data={"name": "", "exam_date": "2026-12-20"})
    assert resp.status_code == 200
    assert _course_count() == before


def test_legacy_bad_date_plan_returns_400_not_500(client):
    # a database predating validation may hold a bad date; the plan
    # route must fail gracefully instead of crashing
    cid = db.add_course(app_module.DB_PATH, "Legacy", "garbage")
    resp = client.post(f"/courses/{cid}/plan", data={"minutes_per_day": 60})
    assert resp.status_code == 400
    assert "invalid exam date" in resp.get_data(as_text=True)


# --- 2. topic creation validates course ---

def test_add_topic_valid_course(client):
    cid = make_course(client)
    resp = client.post(f"/courses/{cid}/topics",
                       data={"title": "Waves", "difficulty": 4})
    assert resp.status_code == 302
    assert _topic_count() == 1


def test_add_topic_missing_course_404_no_record(client):
    before = _topic_count()
    resp = client.post("/courses/99999/topics",
                       data={"title": "Ghost", "difficulty": 3})
    assert resp.status_code == 404
    assert _topic_count() == before


# --- 3. quiz topic ownership ---

def test_quiz_valid_topic_calls_helper(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    called = {}
    monkeypatch.setattr(ai, "generate_quiz",
                        lambda topic, difficulty="medium": (
                            called.update(topic=topic),
                            {"enabled": True, "topic": topic,
                             "questions": []})[1])
    cid = make_course(client)
    client.post(f"/courses/{cid}/topics",
                data={"title": "Thermo", "difficulty": 3})
    resp = client.post(f"/courses/{cid}/quiz",
                       data={"action": "quiz", "topic": "Thermo"})
    assert resp.status_code == 200
    assert called.get("topic") == "Thermo"


def test_quiz_invalid_topic_rejected_helper_not_called(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    called = []
    monkeypatch.setattr(ai, "generate_quiz",
                        lambda *a, **k: called.append(True))
    cid = make_course(client)
    client.post(f"/courses/{cid}/topics",
                data={"title": "Thermo", "difficulty": 3})
    resp = client.post(f"/courses/{cid}/quiz",
                       data={"action": "quiz", "topic": "Nonexistent Topic"})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "does not belong to this course" in html
    assert called == []


def test_quiz_cross_course_topic_rejected(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    called = []
    monkeypatch.setattr(ai, "generate_quiz",
                        lambda *a, **k: called.append(True))
    cid = make_course(client, name="Physics")
    other = make_course(client, name="Math")
    client.post(f"/courses/{other}/topics",
                data={"title": "Algebra", "difficulty": 2})
    resp = client.post(f"/courses/{cid}/quiz",
                       data={"action": "quiz", "topic": "Algebra"})
    assert "does not belong to this course" in resp.get_data(as_text=True)
    assert called == []


def test_quiz_validation_applies_without_key(client, monkeypatch):
    # invalid input must not bypass validation just because AI is off
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cid = make_course(client)
    resp = client.post(f"/courses/{cid}/quiz",
                       data={"action": "quiz", "topic": "Bogus"})
    assert "does not belong to this course" in resp.get_data(as_text=True)


# --- 4. upload limits ---

def test_valid_txt_upload_still_works(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "generate_notes",
                        lambda *a, **k: {"enabled": True, "style": "concise",
                                         "notes": "n"})
    data = {"document_file": (io.BytesIO(b"hello " * 500), "a.txt")}
    resp = client.post("/notes", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200


def test_empty_file_upload_handled(client):
    data = {"document_file": (io.BytesIO(b""), "empty.txt"),
            "document_content": "x" * 100}
    resp = client.post("/notes", data=data, content_type="multipart/form-data")
    # empty file falls back to pasted content; short content rejected cleanly
    assert resp.status_code == 200


def test_oversized_request_rejected_with_413(client):
    big = b"x" * (app_module.app.config["MAX_CONTENT_LENGTH"] + 1024)
    data = {"document_file": (io.BytesIO(big), "big.txt")}
    resp = client.post("/notes", data=data, content_type="multipart/form-data")
    assert resp.status_code == 413
    assert "too large" in resp.get_data(as_text=True)


def test_pasted_text_truncated_to_char_limit(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    seen = {}
    monkeypatch.setattr(ai, "generate_notes",
                        lambda doc, style="comprehensive", focus_area=None: (
                            seen.update(doc=doc),
                            {"enabled": True, "style": style, "notes": "n"})[1])
    resp = client.post("/notes", data={
        "document_content": "y" * (ai.MAX_NOTES_CHARS if hasattr(ai, "MAX_NOTES_CHARS") else 20000 + 5000)})
    assert resp.status_code == 200
    assert len(seen["doc"]) <= 20000


# --- 5. session memory bounds ---

def _seed_session(token="tok", idle_seconds=0):
    now = app_module._now()
    app_module.SESSIONS[token] = {
        "course_id": 1, "course_name": "C", "topics": [], "mode": "practice",
        "length": 5, "questions": [], "available": 0, "answers": [],
        "feedbacks": [], "current": 0, "finished": False, "error": None,
        "pending_answer": "", "last_active": now - idle_seconds,
    }


def test_idle_session_expires():
    app_module.SESSIONS.clear()
    _seed_session("old", idle_seconds=app_module.SESSION_TTL_SECONDS + 1)
    _seed_session("fresh", idle_seconds=10)
    app_module._prune_sessions()
    assert "old" not in app_module.SESSIONS
    assert "fresh" in app_module.SESSIONS


def test_hard_cap_evicts_oldest_idle():
    app_module.SESSIONS.clear()
    base = app_module._now()
    for i in range(app_module.MAX_SESSIONS + 5):
        _seed_session(f"t{i}", idle_seconds=0)
        app_module.SESSIONS[f"t{i}"]["last_active"] = base - i
    app_module._prune_sessions(now=base)
    assert len(app_module.SESSIONS) == app_module.MAX_SESSIONS
    # newest survive, oldest evicted
    assert "t0" in app_module.SESSIONS
    assert f"t{app_module.MAX_SESSIONS + 4}" not in app_module.SESSIONS


def test_active_session_not_expired_by_use(client):
    cid = make_course(client)
    with db.connect(app_module.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO topics (course_id, title, difficulty) VALUES (?, ?, 3)",
            (cid, "T"))
        tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    resp = client.post(f"/courses/{cid}/session/start", data={
        "topic_ids": [str(tid)], "length": "5", "mode": "practice"})
    token = resp.headers["Location"].rsplit("/", 1)[-1]
    before = app_module.SESSIONS[token]["last_active"]
    # simulate time passing but within TTL; a request keeps it alive
    app_module.SESSIONS[token]["last_active"] = before - 60
    assert client.get(f"/session/{token}").status_code == 200
    assert token in app_module.SESSIONS


def test_expired_session_token_404s_gracefully(client):
    _seed_session("stale", idle_seconds=app_module.SESSION_TTL_SECONDS + 60)
    assert client.get("/session/stale").status_code == 404
    assert "stale" not in app_module.SESSIONS  # pruned on access
