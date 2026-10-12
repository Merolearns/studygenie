"""Shared fixtures for StudyGenie tests (multi-user auth)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app as app_module
from studygenie import db
from werkzeug.security import generate_password_hash

TEST_EMAIL = "test@example.com"
TEST_PASSWORD = "password123"


def make_test_user(db_path, email=TEST_EMAIL):
    """Create a user directly; returns the user id."""
    return db.add_user(db_path, email, generate_password_hash(TEST_PASSWORD))


@pytest.fixture
def client():
    """Test client logged in as a fresh user; CSRF disabled for tests."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(path)
    db.migrate(path)
    old = app_module.DB_PATH
    app_module.DB_PATH = path
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    app_module.SESSIONS.clear()
    with app_module.app.test_client() as c:
        c.post("/register", data={"email": TEST_EMAIL,
                                  "password": TEST_PASSWORD})
        yield c
    app_module.DB_PATH = old
    app_module.app.config["WTF_CSRF_ENABLED"] = True
    app_module.SESSIONS.clear()
    os.unlink(path)


@pytest.fixture
def uid(client):
    """The logged-in test user's id."""
    row = db.get_user_by_email(app_module.DB_PATH, TEST_EMAIL)
    return row["id"]
