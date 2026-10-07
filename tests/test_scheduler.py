"""Day-2 tests: the scheduling engine."""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from studygenie import db, scheduler

TODAY = date.today()


def fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    return path


def seed(path, exam_offset_days, topics):
    """topics: list of (title, difficulty)."""
    exam = (TODAY + timedelta(days=exam_offset_days)).isoformat()
    cid = db.add_course(path, "CPSC 483", exam)
    for title, difficulty in topics:
        db.add_topic(path, cid, title, difficulty)
    return cid


def test_urgency_overdue_is_maximum():
    overdue = scheduler.urgency_score(TODAY - timedelta(days=3), TODAY)
    soon = scheduler.urgency_score(TODAY + timedelta(days=2), TODAY)
    far = scheduler.urgency_score(TODAY + timedelta(days=10), TODAY)
    assert overdue == 1.0
    assert soon == 0.5
    assert far == 0.1
    assert overdue > soon > far


def test_overdue_course_plans_everything_today():
    path = fresh_db()
    cid = seed(path, -2, [("KNN", 2), ("Backprop", 5), ("Trees", 3)])
    plan = scheduler.generate_plan(path, cid)
    # overdue exam: only today is left, and reviews past the exam drop out
    assert {s["date"] for s in plan} == {TODAY.isoformat()}
    os.unlink(path)


def test_harder_topic_gets_more_minutes():
    path = fresh_db()
    cid = seed(path, 14, [("KNN basics", 1), ("Backprop", 5)])
    plan = scheduler.generate_plan(path, cid, minutes_per_day=60)
    by_title = {s["topic_title"]: s for s in plan if s["date"] == TODAY.isoformat()}
    assert by_title["Backprop"]["minutes"] > by_title["KNN basics"]["minutes"]
    # harder also goes first
    dates = {s["topic_title"]: s["date"] for s in plan}
    assert dates["Backprop"] <= dates["KNN basics"]
    os.unlink(path)


def test_plan_covers_all_topics():
    path = fresh_db()
    cid = seed(path, 14, [("A", 2), ("B", 4), ("C", 1), ("D", 5)])
    plan = scheduler.generate_plan(path, cid)
    firsts = {s["topic_id"] for s in plan}
    assert len(firsts) == 4
    os.unlink(path)


def test_daily_budget_respected():
    path = fresh_db()
    cid = seed(path, 10, [(f"Topic {i}", (i % 5) + 1) for i in range(8)])
    for budget in (60, 45):
        plan = scheduler.generate_plan(path, cid, minutes_per_day=budget)
        per_day = {}
        for s in plan:
            per_day.setdefault(s["date"], 0)
            per_day[s["date"]] += s["minutes"]
        assert all(total <= budget for total in per_day.values()), per_day
    os.unlink(path)


def test_spaced_repetition_offsets():
    path = fresh_db()
    cid = seed(path, 20, [("KNN", 3)])
    plan = scheduler.generate_plan(path, cid)
    dates = sorted(s["date"] for s in plan)
    expected = [TODAY + timedelta(days=d) for d in (0, 1, 3, 7)]
    assert dates == [d.isoformat() for d in expected]
    os.unlink(path)


def test_reviews_stop_at_exam():
    path = fresh_db()
    cid = seed(path, 2, [("KNN", 3)])
    plan = scheduler.generate_plan(path, cid)
    dates = sorted(s["date"] for s in plan)
    # exam is in 2 days: only the +1-day review still counts
    assert dates == [TODAY.isoformat(), (TODAY + timedelta(days=1)).isoformat()]
    os.unlink(path)


def test_save_plan_replaces_pending():
    path = fresh_db()
    cid = seed(path, 14, [("KNN", 3), ("Trees", 2)])
    plan = scheduler.generate_plan(path, cid)
    scheduler.save_plan(path, cid, plan)
    first_count = len(db.list_sessions(path, cid))
    assert first_count == len(plan)
    # regenerating replaces, not duplicates
    scheduler.save_plan(path, cid, plan)
    assert len(db.list_sessions(path, cid)) == first_count
    os.unlink(path)
