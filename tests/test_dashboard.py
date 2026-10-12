"""Tests for the study dashboard (Phase 3 learning loop)."""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app as app_module
from studygenie import db, dashboard




def seed(path, uid, exam_days=10):
    """Course with topics and a spread of sessions; returns ids."""
    exam = (date.today() + timedelta(days=exam_days)).isoformat()
    cid = db.add_course(path, uid, "Physics", exam)
    t1 = db.add_topic(path, cid, uid, "Hard Topic", 5)
    t2 = db.add_topic(path, cid, uid, "Easy Topic", 1)
    today = date.today()
    # overdue incomplete, today incomplete, future incomplete
    db.add_session(path, cid, uid, t1, (today - timedelta(days=2)).isoformat(), 30)
    db.add_session(path, cid, uid, t2, today.isoformat(), 20)
    db.add_session(path, cid, uid, t1, (today + timedelta(days=3)).isoformat(), 30)
    # completed recent
    s4 = db.add_session(path, cid, uid, t2, (today - timedelta(days=1)).isoformat(), 20)
    db.toggle_session(path, s4, uid)
    # second session for t1 -> t1's later session is a review
    return cid, t1, t2


def test_empty_dashboard_no_crash(uid):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    try:
        assert dashboard.up_next(path, uid) == []
        assert dashboard.overdue(path, uid) == []
        assert dashboard.recently_completed(path, uid) == []
        assert dashboard.upcoming_exams(path, uid) == []
    finally:
        os.unlink(path)


def test_up_next_overdue_first(client, uid):
    path = app_module.DB_PATH
    seed(path, uid)
    items = dashboard.up_next(path, uid)
    assert items, "should have upcoming sessions"
    assert items[0]["overdue"] is True  # overdue sorts first
    assert "overdue" in items[0]["reason"]


def test_up_next_explains_why(client, uid):
    path = app_module.DB_PATH
    seed(path, uid)
    items = dashboard.up_next(path, uid)
    reasons = " ".join(i["reason"] for i in items)
    assert "Exam in 10d" in reasons or "overdue" in reasons
    # no invented scores
    assert "%" not in reasons and "confidence" not in reasons.lower()


def test_review_flag_marks_later_sessions(client, uid):
    path = app_module.DB_PATH
    seed(path, uid)
    # earliest session per topic is the first pass; all later are reviews
    with db.connect(path) as conn:
        rows = conn.execute(
            "SELECT topic_id, MIN(planned_date) AS first FROM sessions"
            " GROUP BY topic_id").fetchall()
    firsts = {r["topic_id"]: r["first"] for r in rows}
    for r in dashboard._sessions_with_context(path, uid):
        if r["topic_id"] is None:
            continue
        expected = r["planned_date"] > firsts[r["topic_id"]]
        assert bool(r["is_review"]) == expected, r


def test_overdue_only_incomplete_past(client, uid):
    path = app_module.DB_PATH
    seed(path, uid)
    items = dashboard.overdue(path, uid)
    assert len(items) == 1  # the one overdue incomplete session
    assert items[0]["topic_title"] == "Hard Topic"


def test_recently_completed(client, uid):
    path = app_module.DB_PATH
    seed(path, uid)
    items = dashboard.recently_completed(path, uid)
    assert len(items) == 1
    assert items[0]["topic_title"] == "Easy Topic"


def test_upcoming_exams_sorted_and_filtered(client, uid):
    path = app_module.DB_PATH
    seed(path, uid, exam_days=10)
    db.add_course(path, uid, "Soon", (date.today() + timedelta(days=2)).isoformat())
    db.add_course(path, uid, "Past", (date.today() - timedelta(days=5)).isoformat())
    exams = dashboard.upcoming_exams(path, uid)
    names = [e["name"] for e in exams]
    assert names == ["Soon", "Physics"]
    assert exams[0]["days_left"] == 2


def test_dashboard_page_renders_sections(client, uid):
    path = app_module.DB_PATH
    seed(path, uid)
    html = client.get("/").get_data(as_text=True)
    assert "Up next" in html
    assert "Upcoming exams" in html
    assert "Recently completed" in html
    assert "Needs attention" in html
    assert "Review" in html  # review tag shown


def test_dashboard_empty_state(client):
    html = client.get("/").get_data(as_text=True)
    assert "Nothing scheduled" in html
    assert "No upcoming exams." in html


def test_complete_from_dashboard_reflects_in_progress(client, uid):
    path = app_module.DB_PATH
    seed(path, uid)
    items = dashboard.up_next(path, uid)
    sid = items[0]["id"]
    # toggle via the dashboard's check button (referrer -> back to /)
    client.post(f"/sessions/{sid}/done", headers={"Referer": "/"})
    assert sid not in [i["id"] for i in dashboard.up_next(path, uid)]
    assert len(dashboard.recently_completed(path, uid)) == 2


def test_up_next_respects_limit(client, uid):
    path = app_module.DB_PATH
    cid, t1, t2 = seed(path, uid)
    today = date.today()
    for i in range(10):
        db.add_session(path, cid, uid, t2,
                       (today + timedelta(days=i + 5)).isoformat(), 15)
    assert len(dashboard.up_next(path, uid)) == 5
