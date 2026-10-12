"""Phase 6: production configuration, health checks, error handling."""
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _reload_app(env):
    old = dict(os.environ)
    os.environ.update(env)
    os.environ.pop("SECRET_KEY", None)
    for k, v in env.items():
        os.environ[k] = v
    try:
        import app as app_module
        return importlib.reload(app_module)
    finally:
        os.environ.clear()
        os.environ.update(old)


def test_production_requires_secret_key():
    try:
        _reload_app({"PRODUCTION": "1"})
    except RuntimeError as e:
        assert "SECRET_KEY" in str(e)
    else:
        raise AssertionError("production boot without SECRET_KEY should fail")


def test_production_config_with_key(tmp_path):
    mod = _reload_app({"PRODUCTION": "1",
                       "SECRET_KEY": "x" * 64,
                       "STUDYGENIE_DB": str(tmp_path / "p.db")})
    assert mod.app.debug is False
    assert mod.app.config["SESSION_COOKIE_SECURE"] is True
    assert mod.app.config["SESSION_COOKIE_HTTPONLY"] is True


def test_healthz_ok(tmp_path):
    mod = _reload_app({"SECRET_KEY": "x" * 64,
                       "STUDYGENIE_DB": str(tmp_path / "h.db")})
    mod.app.config["TESTING"] = True
    client = mod.app.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_404_does_not_leak_internals(tmp_path):
    mod = _reload_app({"SECRET_KEY": "x" * 64,
                       "STUDYGENIE_DB": str(tmp_path / "e.db")})
    mod.app.config["TESTING"] = True
    client = mod.app.test_client()
    resp = client.get("/no-such-page-xyz")
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert "Traceback" not in body
    assert "SECRET_KEY" not in body
