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


# --- course/topic edit & delete routes ---

def _make_course_with_topic(client):
    from datetime import date, timedelta
    exam = (date.today() + timedelta(days=10)).isoformat()
    resp = client.post("/courses/new", data={"name": "Bio", "exam_date": exam})
    cid = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    client.post(f"/courses/{cid}/topics", data={"title": "Cells", "difficulty": 3})
    return cid


def test_edit_course_roundtrip(client):
    cid = _make_course_with_topic(client)
    resp = client.get(f"/courses/{cid}/edit")
    assert resp.status_code == 200
    assert "Bio" in resp.get_data(as_text=True)
    resp = client.post(f"/courses/{cid}/edit",
                       data={"name": "Biology", "exam_date": "2026-12-25"})
    assert resp.status_code == 302
    html = client.get(f"/courses/{cid}").get_data(as_text=True)
    assert "Biology" in html and "2026-12-25" in html


def test_edit_course_invalid_date_keeps_old(client):
    cid = _make_course_with_topic(client)
    resp = client.post(f"/courses/{cid}/edit",
                       data={"name": "Bio", "exam_date": "not-a-date"})
    assert resp.status_code == 200
    assert "valid exam date" in resp.get_data(as_text=True)
    # stored data unchanged
    from studygenie import db as dbm
    import app as app_module
    assert dbm.get_course(app_module.DB_PATH, cid)["name"] == "Bio"


def test_edit_course_missing_404(client):
    assert client.get("/courses/99999/edit").status_code == 404
    assert client.post("/courses/99999/edit",
                       data={"name": "X", "exam_date": "2026-12-20"}).status_code == 404


def test_delete_course_confirmation_and_cancel(client):
    cid = _make_course_with_topic(client)
    # GET confirmation does not delete
    resp = client.get(f"/courses/{cid}/delete")
    assert resp.status_code == 200
    assert "permanent" in resp.get_data(as_text=True).lower()
    from studygenie import db as dbm
    import app as app_module
    assert dbm.get_course(app_module.DB_PATH, cid) is not None
    # cancel (POST without confirm) does not delete
    resp = client.post(f"/courses/{cid}/delete", data={})
    assert resp.status_code == 302
    assert dbm.get_course(app_module.DB_PATH, cid) is not None


def test_delete_course_confirmed_removes_all(client):
    cid = _make_course_with_topic(client)
    resp = client.post(f"/courses/{cid}/delete", data={"confirm": "yes"},
                       follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Course deleted." in html
    assert "Bio" not in html
    assert client.get(f"/courses/{cid}").status_code == 404


def test_delete_course_missing_404(client):
    assert client.get("/courses/99999/delete").status_code == 404
    assert client.post("/courses/99999/delete",
                       data={"confirm": "yes"}).status_code == 404


def _topic_id(client, cid):
    from studygenie import db as dbm
    import app as app_module
    with dbm.connect(app_module.DB_PATH) as conn:
        return conn.execute(
            "SELECT id FROM topics WHERE course_id = ?", (cid,)
        ).fetchone()["id"]


def test_edit_topic_roundtrip(client):
    cid = _make_course_with_topic(client)
    tid = _topic_id(client, cid)
    resp = client.get(f"/courses/{cid}/topics/{tid}/edit")
    assert resp.status_code == 200
    resp = client.post(f"/courses/{cid}/topics/{tid}/edit",
                       data={"title": "Cell Biology", "difficulty": 5},
                       follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Cell Biology" in html and "Topic updated." in html


def test_edit_topic_invalid_keeps_old(client):
    cid = _make_course_with_topic(client)
    tid = _topic_id(client, cid)
    resp = client.post(f"/courses/{cid}/topics/{tid}/edit",
                       data={"title": "", "difficulty": 3})
    assert resp.status_code == 200
    assert "topic title" in resp.get_data(as_text=True).lower()


def test_edit_topic_wrong_course_404(client):
    cid = _make_course_with_topic(client)
    other = _make_course_with_topic(client)
    tid = _topic_id(client, cid)
    assert client.get(f"/courses/{other}/topics/{tid}/edit").status_code == 404
    assert client.post(f"/courses/{other}/topics/{tid}/edit",
                       data={"title": "Hijack", "difficulty": 1}).status_code == 404


def test_delete_topic_keeps_sessions_detached(client):
    from studygenie import db as dbm
    import app as app_module
    cid = _make_course_with_topic(client)
    tid = _topic_id(client, cid)
    dbm.add_session(app_module.DB_PATH, cid, tid, "2026-12-01", 30)
    resp = client.get(f"/courses/{cid}/topics/{tid}/delete")
    assert resp.status_code == 200
    assert "detached" in resp.get_data(as_text=True)
    resp = client.post(f"/courses/{cid}/topics/{tid}/delete",
                       data={"confirm": "yes"}, follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Topic deleted." in html
    sessions = dbm.list_sessions(app_module.DB_PATH, cid)
    assert len(sessions) == 1 and sessions[0]["topic_id"] is None


def test_delete_topic_cancel_preserves(client):
    from studygenie import db as dbm
    import app as app_module
    cid = _make_course_with_topic(client)
    tid = _topic_id(client, cid)
    client.post(f"/courses/{cid}/topics/{tid}/delete", data={})
    assert dbm.get_topic(app_module.DB_PATH, tid) is not None


def test_course_page_shows_edit_delete_actions(client):
    cid = _make_course_with_topic(client)
    html = client.get(f"/courses/{cid}").get_data(as_text=True)
    assert f"/courses/{cid}/edit" in html
    assert f"/courses/{cid}/delete" in html
    assert "notice" not in html  # no notice param -> no banner
