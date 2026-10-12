"""Day-1 tests: database layer for courses and topics."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from studygenie import db
from werkzeug.security import generate_password_hash


def _user(path, email="u@example.com"):
    return db.add_user(path, email, generate_password_hash("password123"))


def fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    uid = _user(path)
    return path, uid


def test_add_and_list_courses():
    path, uid = fresh_db()
    db.add_course(path, uid, "CPSC 483", "2026-12-10")
    db.add_course(path, uid, "CPSC 481", "2026-11-20")
    courses = db.list_courses(path, uid)
    assert len(courses) == 2
    # ordered by exam date
    assert courses[0]["name"] == "CPSC 481"
    assert courses[1]["exam_date"] == "2026-12-10"
    os.unlink(path)


def test_get_course_missing():
    path, uid = fresh_db()
    assert db.get_course(path, 999, uid) is None
    os.unlink(path)


def test_topics_ordered_by_difficulty():
    path, uid = fresh_db()
    cid = db.add_course(path, uid, "CPSC 483", "2026-12-10")
    db.add_topic(path, cid, uid, "KNN basics", difficulty=1)
    db.add_topic(path, cid, uid, "Backprop", difficulty=5)
    db.add_topic(path, cid, uid, "Decision trees", difficulty=3)
    topics = db.list_topics(path, cid, uid)
    assert [t["title"] for t in topics] == ["Backprop", "Decision trees", "KNN basics"]
    os.unlink(path)


def test_difficulty_clamped():
    path, uid = fresh_db()
    cid = db.add_course(path, uid, "CPSC 483", "2026-12-10")
    db.add_topic(path, cid, uid, "Too hard", difficulty=99)
    db.add_topic(path, cid, uid, "Too easy", difficulty=0)
    topics = db.list_topics(path, cid, uid)
    diffs = sorted(t["difficulty"] for t in topics)
    assert diffs == [1, 5]
    os.unlink(path)


# --- availability ---

def test_availability_defaults_and_roundtrip(tmp_path):
    from studygenie import db as dbm
    path = str(tmp_path / "a.db")
    dbm.init_db(path)
    uid = _user(path)
    assert dbm.get_availability(path, uid) == {}
    assert dbm.minutes_for(path, uid, "2026-10-12") == 60  # Monday default
    dbm.set_availability(path, uid, {0: 90, 6: 0})
    assert dbm.get_availability(path, uid) == {0: 90, 6: 0}
    assert dbm.minutes_for(path, uid, "2026-10-12") == 90  # Monday
    assert dbm.minutes_for(path, uid, "2026-10-18") == 0   # Sunday
    assert dbm.minutes_for(path, uid, "2026-10-13") == 60  # Tuesday default


def test_availability_clamps_and_ignores_bad_weekdays(tmp_path):
    from studygenie import db as dbm
    path = str(tmp_path / "a.db")
    dbm.init_db(path)
    uid = _user(path)
    dbm.set_availability(path, uid, {1: -5, 9: 30})
    assert dbm.get_availability(path, uid) == {1: 0}


# --- course/topic update & delete ---

def test_update_and_delete_course(tmp_path):
    from studygenie import db as dbm
    path = str(tmp_path / "m.db")
    dbm.init_db(path)
    uid = _user(path)
    cid = dbm.add_course(path, uid, "Old", "2026-12-20")
    assert dbm.update_course(path, cid, uid, "New", "2026-12-25") == 1
    course = dbm.get_course(path, cid, uid)
    assert course["name"] == "New" and course["exam_date"] == "2026-12-25"
    assert dbm.update_course(path, 99999, uid, "X", "2026-12-20") == 0
    assert dbm.delete_course(path, cid, uid) == 1
    assert dbm.get_course(path, cid, uid) is None
    assert dbm.delete_course(path, cid, uid) == 0


def test_delete_course_cascades_topics_and_sessions(tmp_path):
    from studygenie import db as dbm
    path = str(tmp_path / "m.db")
    dbm.init_db(path)
    uid = _user(path)
    cid = dbm.add_course(path, uid, "C", "2026-12-20")
    tid = dbm.add_topic(path, cid, uid, "T", 3)
    dbm.add_session(path, cid, uid, tid, "2026-12-01", 30)
    preview = dbm.course_delete_preview(path, cid, uid)
    assert preview == {"topics": 1, "sessions": 1, "completed": 0}
    dbm.toggle_session(path, 1, uid)
    assert dbm.course_delete_preview(path, cid, uid)["completed"] == 1
    dbm.delete_course(path, cid, uid)
    assert dbm.list_topics(path, cid, uid) == []
    assert dbm.list_sessions(path, cid, uid) == []


def test_update_and_delete_topic(tmp_path):
    from studygenie import db as dbm
    path = str(tmp_path / "m.db")
    dbm.init_db(path)
    uid = _user(path)
    cid = dbm.add_course(path, uid, "C", "2026-12-20")
    tid = dbm.add_topic(path, cid, uid, "Old", 2)
    assert dbm.update_topic(path, tid, uid, "New", 5) == 1
    topic = dbm.get_topic(path, tid, uid)
    assert topic["title"] == "New" and topic["difficulty"] == 5
    assert dbm.update_topic(path, tid, uid, "Clamped", 99) == 1
    assert dbm.get_topic(path, tid, uid)["difficulty"] == 5  # clamped
    assert dbm.update_topic(path, 99999, uid, "X", 3) == 0
    # sessions survive topic deletion, detached
    dbm.add_session(path, cid, uid, tid, "2026-12-01", 30)
    assert dbm.count_topic_sessions(path, tid, uid) == 1
    assert dbm.delete_topic(path, tid, uid) == 1
    assert dbm.get_topic(path, tid, uid) is None
    sessions = dbm.list_sessions(path, cid, uid)
    assert len(sessions) == 1 and sessions[0]["topic_id"] is None
