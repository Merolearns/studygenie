"""Day-4 tests: streak, weekly completion, minutes studied, progress page."""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app as app_module
from studygenie import db, progress


def fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    return path


def seed(path, course_name="Databases", exam_in_days=10):
    return db.add_course(path, course_name,
                         (date.today() + timedelta(days=exam_in_days)).isoformat())


def complete(path, course_id, topic_id, day_offset, minutes=30):
    """Add a session `day_offset` days from today and mark it completed."""
    day = (date.today() + timedelta(days=day_offset)).isoformat()
    sid = db.add_session(path, course_id, topic_id, day, minutes)
    db.toggle_session(path, sid)
    return sid


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


def test_streak_counts_consecutive_days():
    path = fresh_db()
    cid = seed(path)
    tid = db.add_topic(path, cid, "Joins", 3)
    complete(path, cid, tid, 0)
    complete(path, cid, tid, -1)
    complete(path, cid, tid, -2)
    assert progress.streak(path) == 3
    os.unlink(path)


def test_streak_breaks_on_gap():
    path = fresh_db()
    cid = seed(path)
    tid = db.add_topic(path, cid, "Joins", 3)
    complete(path, cid, tid, 0)
    complete(path, cid, tid, -2)  # yesterday missing
    assert progress.streak(path) == 1
    os.unlink(path)


def test_streak_zero_when_nothing_done_today():
    path = fresh_db()
    cid = seed(path)
    tid = db.add_topic(path, cid, "Joins", 3)
    complete(path, cid, tid, -1)
    assert progress.streak(path) == 0
    os.unlink(path)


def test_weekly_completion_math():
    path = fresh_db()
    cid = seed(path)
    tid = db.add_topic(path, cid, "Joins", 3)
    for offset in (0, -1, -2, -3):
        db.add_session(path, cid, tid,
                       (date.today() + timedelta(days=offset)).isoformat(), 30)
    complete(path, cid, tid, 0)
    complete(path, cid, tid, -1)
    pct, done, total = progress.weekly_completion(path)
    assert (pct, done, total) == (33, 2, 6)
    os.unlink(path)


def test_weekly_completion_empty():
    path = fresh_db()
    assert progress.weekly_completion(path) == (0, 0, 0)
    os.unlink(path)


def test_total_minutes_counts_completed_only():
    path = fresh_db()
    cid = seed(path)
    tid = db.add_topic(path, cid, "Joins", 3)
    complete(path, cid, tid, 0, minutes=45)
    db.add_session(path, cid, tid, date.today().isoformat(), 30)  # incomplete
    assert progress.total_minutes(path) == 45
    os.unlink(path)


def test_minutes_by_day_series():
    path = fresh_db()
    cid = seed(path)
    tid = db.add_topic(path, cid, "Joins", 3)
    complete(path, cid, tid, 0, minutes=45)
    complete(path, cid, tid, -2, minutes=20)
    series = progress.minutes_by_day(path)
    assert len(series) == 7
    by_date = {s["date"]: s["minutes"] for s in series}
    assert by_date[date.today().isoformat()] == 45
    assert by_date[(date.today() - timedelta(days=2)).isoformat()] == 20
    assert by_date[(date.today() - timedelta(days=1)).isoformat()] == 0
    # oldest first
    assert series[0]["date"] == (date.today() - timedelta(days=6)).isoformat()
    os.unlink(path)


def test_course_progress_percentages():
    path = fresh_db()
    cid = seed(path, "Databases")
    other = seed(path, "Algorithms")
    tid = db.add_topic(path, cid, "Joins", 3)
    db.add_session(path, cid, tid, date.today().isoformat(), 30)
    complete(path, cid, tid, -1)
    rows = {r["id"]: r for r in progress.course_progress(path)}
    assert rows[cid]["pct"] == 50
    assert rows[cid]["done"] == 1
    assert rows[cid]["total"] == 2
    assert rows[other]["pct"] is None  # no sessions planned yet
    os.unlink(path)


def test_progress_page_renders(client):
    exam = (date.today() + timedelta(days=10)).isoformat()
    resp = client.post("/courses/new", data={"name": "DB", "exam_date": exam})
    cid = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/courses/{cid}/topics", data={"title": "Joins", "difficulty": 5})
    client.post(f"/courses/{cid}/plan", data={"minutes_per_day": 60})
    html = client.get("/progress").get_data(as_text=True)
    assert client.get("/progress").status_code == 200
    assert "streak" in html.lower()
    assert "chart.js" in html.lower()
    assert "minutes" in html.lower()


def test_index_shows_course_progress_bar(client):
    exam = (date.today() + timedelta(days=10)).isoformat()
    resp = client.post("/courses/new", data={"name": "DB", "exam_date": exam})
    cid = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/courses/{cid}/topics", data={"title": "Joins", "difficulty": 5})
    client.post(f"/courses/{cid}/plan", data={"minutes_per_day": 60})
    html = client.get("/").get_data(as_text=True)
    assert "progress-bar" in html
