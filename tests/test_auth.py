"""Phase 5: auth, per-user isolation, CSRF, migration, export/delete."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from studygenie import db


@pytest.fixture
def raw_client():
    """Unauthenticated client with CSRF disabled (for auth flow tests)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    db.migrate(path)
    old = app_module.DB_PATH
    app_module.DB_PATH = path
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    with app_module.app.test_client() as c:
        yield c, path
    app_module.DB_PATH = old
    app_module.app.config["WTF_CSRF_ENABLED"] = True
    os.unlink(path)


def register(client, email, password="password123"):
    return client.post("/register",
                       data={"email": email, "password": password},
                       follow_redirects=True)


def test_register_and_login_flow(raw_client):
    client, path = raw_client
    # register logs in automatically
    resp = register(client, "a@x.com")
    assert resp.status_code == 200
    assert "Study dashboard" in resp.get_data(as_text=True)
    # logout then login
    client.post("/logout")
    assert client.get("/").status_code == 302  # redirected to login
    resp = client.post("/login", data={"email": "a@x.com",
                                       "password": "password123"},
                       follow_redirects=True)
    assert "Study dashboard" in resp.get_data(as_text=True)


def test_register_rejects_bad_input(raw_client):
    client, _ = raw_client
    assert b"valid email" in register(client, "not-an-email").data
    assert b"8 characters" in register(client, "b@x.com", "short").data
    register(client, "c@x.com")
    client.post("/logout")
    assert b"already exists" in register(client, "c@x.com").data


def test_login_rejects_wrong_password(raw_client):
    client, _ = raw_client
    register(client, "d@x.com")
    client.post("/logout")
    resp = client.post("/login", data={"email": "d@x.com",
                                       "password": "wrongpass"})
    assert b"Invalid email or password" in resp.data


def test_passwords_are_hashed(raw_client):
    client, path = raw_client
    register(client, "e@x.com", "secretpassword")
    row = db.get_user_by_email(path, "e@x.com")
    assert row["password_hash"] != "secretpassword"
    assert "pbkdf2" in row["password_hash"] or "$" in row["password_hash"]


def test_users_cannot_see_each_others_courses(raw_client):
    client, path = raw_client
    register(client, "user1@x.com")
    uid1 = db.get_user_by_email(path, "user1@x.com")["id"]
    cid = db.add_course(path, uid1, "User1 Course", "2026-12-20")
    client.post("/logout")

    register(client, "user2@x.com")
    # user2's dashboard shows no courses
    assert "User1 Course" not in client.get("/").get_data(as_text=True)
    # direct URL access -> 404, not data
    assert client.get(f"/courses/{cid}").status_code == 404
    assert client.get(f"/courses/{cid}/edit").status_code == 404
    assert client.post(f"/courses/{cid}/delete",
                       data={"confirm": "yes"}).status_code == 404
    # user2 cannot toggle user1's session
    tid = db.add_topic(path, uid1, cid, "T", 3)
    sid = db.add_session(path, cid, uid1, tid, "2026-12-01", 30)
    assert db.toggle_session(path, sid,
                             db.get_user_by_email(
                                 path, "user2@x.com")["id"]) == 0


def test_users_cannot_see_each_others_progress(raw_client):
    client, path = raw_client
    register(client, "p1@x.com")
    uid1 = db.get_user_by_email(path, "p1@x.com")["id"]
    cid = db.add_course(path, uid1, "P1 Course", "2026-12-20")
    sid = db.add_session(path, cid, uid1, None, "2026-12-01", 30)
    db.toggle_session(path, sid, uid1)
    client.post("/logout")
    register(client, "p2@x.com")
    html = client.get("/progress").get_data(as_text=True)
    assert "P1 Course" not in html
    from studygenie import progress
    uid2 = db.get_user_by_email(path, "p2@x.com")["id"]
    assert progress.total_minutes(path, uid2) == 0
    assert progress.streak(path, uid2) == 0


def test_csrf_blocks_post_without_token():
    """With CSRF enabled, a POST without token is rejected."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    old = app_module.DB_PATH
    app_module.DB_PATH = path
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = True
    try:
        with app_module.app.test_client() as c:
            # register with CSRF on: need token from the form
            html = c.get("/register").get_data(as_text=True)
            import re
            token = re.search(r'name="csrf_token" value="([^"]+)"',
                              html).group(1)
            resp = c.post("/register",
                          data={"email": "csrf@x.com", "password": "password123",
                                "csrf_token": token},
                          follow_redirects=True)
            assert resp.status_code == 200
            # now POST without token -> 400
            resp = c.post("/courses/new",
                          data={"name": "X", "exam_date": "2026-12-20"})
            assert resp.status_code == 400
    finally:
        app_module.DB_PATH = old
        app_module.app.config["WTF_CSRF_ENABLED"] = False
        os.unlink(path)


def test_orphan_data_adopted_by_first_user(raw_client):
    client, path = raw_client
    # simulate pre-account data: create orphan course directly
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO courses (user_id, name, exam_date)"
            " VALUES (NULL, 'Orphan Course', '2026-12-20')")
    register(client, "first@x.com")
    uid = db.get_user_by_email(path, "first@x.com")["id"]
    courses = db.list_courses(path, uid)
    assert len(courses) == 1 and courses[0]["name"] == "Orphan Course"
    assert "Orphan Course" in client.get("/").get_data(as_text=True)


def test_migrate_upgrades_old_database():
    """An old single-user DB gains users/user_id without losing data."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # build the old schema manually
        import sqlite3
        conn = sqlite3.connect(path)
        conn.executescript("""
        CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT, exam_date TEXT);
        CREATE TABLE topics (id INTEGER PRIMARY KEY, course_id INTEGER, title TEXT);
        CREATE TABLE availability (weekday INTEGER PRIMARY KEY, minutes INTEGER);
        INSERT INTO courses (name, exam_date) VALUES ('Old', '2026-12-20');
        INSERT INTO availability (weekday, minutes) VALUES (0, 90);
        """)
        conn.commit()
        conn.close()
        db.migrate(path)
        assert db.orphan_course_count(path) == 1
        # adopt and verify availability preserved
        uid = db.add_user(path, "m@x.com",
                          generate_password_hash("password123"))
        db.adopt_orphans(path, uid)
        assert db.get_availability(path, uid).get(0) == 90
        assert db.list_courses(path, uid)[0]["name"] == "Old"
    finally:
        os.unlink(path)


def test_export_and_delete_account(raw_client):
    client, path = raw_client
    register(client, "exp@x.com")
    uid = db.get_user_by_email(path, "exp@x.com")["id"]
    cid = db.add_course(path, uid, "Export Me", "2026-12-20")
    db.add_topic(path, cid, uid, "T1", 3)

    resp = client.get("/account/export")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["account"]["email"] == "exp@x.com"
    assert data["courses"][0]["name"] == "Export Me"
    assert data["courses"][0]["topics"][0]["title"] == "T1"

    # delete with confirmation
    resp = client.post("/account/delete", data={"confirm": "yes"},
                       follow_redirects=True)
    assert b"deleted" in resp.data.lower()
    assert db.get_user_by_email(path, "exp@x.com") is None
    assert db.list_courses(path, uid) == []
    # logged out now
    assert client.get("/").status_code == 302


def test_delete_account_requires_confirmation(raw_client):
    client, path = raw_client
    register(client, "keep@x.com")
    client.post("/account/delete", data={})  # no confirm
    assert db.get_user_by_email(path, "keep@x.com") is not None
