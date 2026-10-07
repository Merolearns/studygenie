"""Day-1 tests: database layer for courses and topics."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from studygenie import db


def fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    return path


def test_add_and_list_courses():
    path = fresh_db()
    db.add_course(path, "CPSC 483", "2026-12-10")
    db.add_course(path, "CPSC 481", "2026-11-20")
    courses = db.list_courses(path)
    assert len(courses) == 2
    # ordered by exam date
    assert courses[0]["name"] == "CPSC 481"
    assert courses[1]["exam_date"] == "2026-12-10"
    os.unlink(path)


def test_get_course_missing():
    path = fresh_db()
    assert db.get_course(path, 999) is None
    os.unlink(path)


def test_topics_ordered_by_difficulty():
    path = fresh_db()
    cid = db.add_course(path, "CPSC 483", "2026-12-10")
    db.add_topic(path, cid, "KNN basics", difficulty=1)
    db.add_topic(path, cid, "Backprop", difficulty=5)
    db.add_topic(path, cid, "Decision trees", difficulty=3)
    topics = db.list_topics(path, cid)
    assert [t["title"] for t in topics] == ["Backprop", "Decision trees", "KNN basics"]
    os.unlink(path)


def test_difficulty_clamped():
    path = fresh_db()
    cid = db.add_course(path, "CPSC 483", "2026-12-10")
    db.add_topic(path, cid, "Too hard", difficulty=99)
    db.add_topic(path, cid, "Too easy", difficulty=0)
    topics = db.list_topics(path, cid)
    diffs = sorted(t["difficulty"] for t in topics)
    assert diffs == [1, 5]
    os.unlink(path)
