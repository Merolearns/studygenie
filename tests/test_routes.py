"""Day-3 tests: week timetable, session check-off, and the full flow."""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app as app_module
from studygenie import db


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


def make_course_with_plan(client, exam_in_days=10):
    exam = (date.today() + timedelta(days=exam_in_days)).isoformat()
    resp = client.post("/courses/new", data={"name": "Databases", "exam_date": exam})
    course_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/courses/{course_id}/topics", data={"title": "Joins", "difficulty": 5})
    client.post(f"/courses/{course_id}/topics", data={"title": "Views", "difficulty": 2})
    client.post(f"/courses/{course_id}/plan", data={"minutes_per_day": 60})
    return course_id


def test_plan_shows_this_week(client):
    course_id = make_course_with_plan(client)
    sessions = db.list_sessions(app_module.DB_PATH, course_id)
    assert sessions, "plan generator should have saved sessions"
    resp = client.get("/plan")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Joins" in html
    assert "Today" in html  # today's column is highlighted


def test_toggle_session_marks_completed(client):
    course_id = make_course_with_plan(client)
    sessions = db.list_sessions(app_module.DB_PATH, course_id)
    session_id = sessions[0]["id"]
    assert sessions[0]["completed"] == 0

    resp = client.post(f"/sessions/{session_id}/done")
    assert resp.status_code == 302
    after = db.list_sessions(app_module.DB_PATH, course_id)
    assert [s for s in after if s["id"] == session_id][0]["completed"] == 1

    # toggling again undoes it
    client.post(f"/sessions/{session_id}/done")
    again = db.list_sessions(app_module.DB_PATH, course_id)
    assert [s for s in again if s["id"] == session_id][0]["completed"] == 0


def test_toggle_missing_session_is_harmless(client):
    resp = client.post("/sessions/99999/done")
    assert resp.status_code == 302


def test_completed_session_rendered_done(client):
    course_id = make_course_with_plan(client)
    sessions = db.list_sessions(app_module.DB_PATH, course_id)
    client.post(f"/sessions/{sessions[0]['id']}/done")
    html = client.get("/plan").get_data(as_text=True)
    assert 'class="done"' in html
