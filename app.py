"""StudyGenie web app — Flask + SQLite study planner.

Multi-user hosted app: accounts with per-user data isolation, CSRF
protection, and graceful no-key AI behavior. Secrets come from the
environment, never from code.
"""
import os
import secrets as _secrets_mod
from datetime import date, timedelta
from flask import Flask, render_template, request, redirect, url_for, \
    jsonify, send_file, abort
from flask_login import LoginManager, UserMixin, login_user, logout_user, \
    login_required, current_user
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.security import generate_password_hash, check_password_hash

from studygenie import db, scheduler, progress, ai, dashboard

DB_PATH = os.environ.get("STUDYGENIE_DB", "studygenie.db")

app = Flask(__name__)

# --- production configuration ---
# Set PRODUCTION=1 in real deployments. In production the app fails fast
# without an externally supplied SECRET_KEY and hardens cookies/proxy
# handling. Local development keeps working with zero configuration.
PRODUCTION = os.environ.get("PRODUCTION", "").lower() in ("1", "true", "yes")

app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    if PRODUCTION:
        raise RuntimeError(
            "SECRET_KEY is not set. Refusing to start in production without it."
            " Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"")
    app.secret_key = _secrets_mod.token_hex(32)
    print("WARNING: SECRET_KEY not set — using an ephemeral key."
          " Set SECRET_KEY in production.")

if PRODUCTION:
    app.config.update(
        SESSION_COOKIE_SECURE=True,    # cookies only over HTTPS
        SESSION_COOKIE_HTTPONLY=True,  # no JS access to the session cookie
        SESSION_COOKIE_SAMESITE="Lax",
        PREFERRED_URL_SCHEME="https",
    )
    # trust X-Forwarded-Proto/Host from the reverse proxy (Caddy/nginx)
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config["DEBUG"] = False

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to use StudyGenie."

csrf = CSRFProtect(app)


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.email = row["email"]


@login_manager.user_loader
def load_user(user_id):
    row = db.get_user(DB_PATH, int(user_id))
    return User(row) if row else None


@app.errorhandler(CSRFError)
def _csrf_error(error):
    return ("Security check failed. Please go back and try again.", 400)


@app.errorhandler(404)
def _not_found(error):
    return render_template("error.html", code=404,
                           message="That page doesn't exist."), 404


@app.errorhandler(500)
def _server_error(error):
    # Never expose stack traces or internals to users.
    return render_template("error.html", code=500,
                           message="Something went wrong on our end."), 500


@app.route("/healthz")
def healthz():
    """Unauthenticated health check for the reverse proxy / orchestrator."""
    try:
        with db.connect(DB_PATH) as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}, 200
    except Exception:
        return {"status": "error"}, 503


# Simple login rate limiting: 5 failures -> 15 min lockout per email+IP.
_LOGIN_ATTEMPTS = {}
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60


def _login_allowed(email, ip):
    import time
    key = (email.lower(), ip)
    attempts = [t for t in _LOGIN_ATTEMPTS.get(key, [])
                if time.time() - t < LOGIN_LOCKOUT_SECONDS]
    _LOGIN_ATTEMPTS[key] = attempts
    return len(attempts) < MAX_LOGIN_ATTEMPTS


def _record_login_failure(email, ip):
    import time
    _LOGIN_ATTEMPTS.setdefault((email.lower(), ip), []).append(time.time())

# Byte-size vs character-count limits (not interchangeable):
# - MAX_CONTENT_LENGTH caps the whole request body in BYTES, enforced by
#   Flask before the view runs (oversized -> 413).
# - MAX_UPLOAD_CHARS caps the decoded text in CHARACTERS, enforced after
#   UTF-8 decoding (a char can be up to 4 bytes, so the byte limit must
#   stay well above 4x the char limit).
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MB per request

MAX_UPLOAD_CHARS = 20000  # cap pasted/uploaded text sent to the AI
# Never hold more than this many raw bytes from an upload in memory.
MAX_UPLOAD_BYTES = MAX_UPLOAD_CHARS * 4


@app.errorhandler(413)
def _request_too_large(error):
    return ("That upload is too large. Please keep files under 1 MB.",
            413)


def _read_document_content():
    """Pasted textarea text, or text extracted from an uploaded .txt file."""
    doc = request.form.get("document_content", "").strip()
    f = request.files.get("document_file")
    if f and f.filename:
        try:
            # bounded read: never load the whole stream blindly
            raw = f.read(MAX_UPLOAD_BYTES + 1)
            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                doc = text[:MAX_UPLOAD_CHARS]
        except Exception:
            pass
    return doc[:MAX_UPLOAD_CHARS]


@app.before_request
def _ensure_db():
    if not os.path.exists(DB_PATH):
        db.init_db(DB_PATH)
    db.migrate(DB_PATH)


# --- authentication ---

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        if "@" not in email or "." not in email.split("@")[-1]:
            error = "Please enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif db.get_user_by_email(DB_PATH, email) is not None:
            error = "An account with that email already exists."
        else:
            try:
                user_id = db.add_user(
                    DB_PATH, email, generate_password_hash(password))
            except Exception:
                error = "An account with that email already exists."
            else:
                # first account adopts any pre-account local data
                if db.orphan_course_count(DB_PATH):
                    db.adopt_orphans(DB_PATH, user_id)
                login_user(User(db.get_user(DB_PATH, user_id)))
                return redirect(url_for("index"))
    return render_template("register.html", error=error,
                           notice=request.args.get("notice"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        ip = request.remote_addr or "unknown"
        if not _login_allowed(email, ip):
            error = ("Too many failed attempts. Please wait 15 minutes"
                     " and try again.")
        else:
            row = db.get_user_by_email(DB_PATH, email)
            if row and check_password_hash(row["password_hash"], password):
                login_user(User(row))
                return redirect(request.args.get("next") or url_for("index"))
            _record_login_failure(email, ip)
            error = "Invalid email or password."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
@login_required
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/account")
@login_required
def account():
    return render_template("account.html", email=current_user.email)


@app.route("/account/export")
@login_required
def export_data():
    """Download all of the user's data as JSON."""
    import io, json
    data = db.export_user_data(DB_PATH, _uid())
    buf = io.BytesIO(json.dumps(data, indent=2).encode("utf-8"))
    return send_file(buf, mimetype="application/json", as_attachment=True,
                     download_name="studygenie-export.json")


@app.route("/account/delete", methods=["GET", "POST"])
@login_required
def delete_account():
    """Delete the account and all its data, after explicit confirmation."""
    if request.method == "POST":
        if request.form.get("confirm") == "yes":
            db.delete_user(DB_PATH, _uid())
            logout_user()
            return redirect(url_for("register",
                                    notice="account-deleted"))
        return redirect(url_for("account"))
    return render_template("account_delete.html", email=current_user.email)


def _uid():
    return int(current_user.id)


@app.route("/")
@login_required
def index():
    courses = db.list_courses(DB_PATH, _uid())
    by_id = {p["id"]: p for p in progress.course_progress(DB_PATH, _uid())}
    for c in courses:
        c["progress_pct"] = by_id.get(c["id"], {}).get("pct")
    return render_template(
        "index.html", courses=courses,
        notice=request.args.get("notice"),
        up_next=dashboard.up_next(DB_PATH, _uid()),
        overdue=dashboard.overdue(DB_PATH, _uid()),
        recent=dashboard.recently_completed(DB_PATH, _uid()),
        exams=dashboard.upcoming_exams(DB_PATH, _uid()),
        streak=progress.streak(DB_PATH, _uid()))


@app.route("/progress")
@login_required
def progress_page():
    pct, done, total = progress.weekly_completion(DB_PATH, _uid())
    return render_template(
        "progress.html",
        streak=progress.streak(DB_PATH, _uid()),
        weekly_pct=pct,
        weekly_done=done,
        weekly_total=total,
        total_minutes=progress.total_minutes(DB_PATH, _uid()),
        chart=progress.minutes_by_day(DB_PATH, _uid()),
    )


@app.route("/courses/new", methods=["GET", "POST"])
@login_required
def new_course():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        # exam_date is validated RAW: no stripping/normalizing before the
        # format check, so whitespace-padded input is rejected, not fixed.
        raw_date = request.form.get("exam_date", "")
        error = None
        if not name:
            error = "Please enter a course name."
        elif not _valid_exam_date(raw_date):
            error = "Please enter a valid exam date (YYYY-MM-DD)."
        if error is None:
            course_id = db.add_course(DB_PATH, _uid(), name, raw_date)
            return redirect(url_for("course_detail", course_id=course_id))
        return render_template("new_course.html", error=error,
                               name=name, exam_date=raw_date)
    return render_template("new_course.html")


def _valid_exam_date(value):
    """Exactly YYYY-MM-DD with ASCII digits, and a real calendar date.

    date.fromisoformat() alone is too permissive (it accepts compact
    '20261220' and week dates like '2026-W51-1'), and \\d matches Unicode
    digits, so the shape is enforced with an ASCII-only regex first.
    """
    import re
    if not isinstance(value, str):
        return False
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


@app.route("/courses/<int:course_id>")
@login_required
def course_detail(course_id):
    course = db.get_course(DB_PATH, course_id, _uid())
    if course is None:
        return "Course not found", 404
    topics = db.list_topics(DB_PATH, course_id, _uid())
    sessions = db.list_sessions(DB_PATH, course_id, _uid())
    return render_template(
        "course_detail.html", course=course, topics=topics,
        session_count=len(sessions), ai_enabled=ai.is_enabled(),
        ai_plan=None, notice=request.args.get("notice"))


@app.route("/courses/<int:course_id>/edit", methods=["GET", "POST"])
@login_required
def edit_course(course_id):
    course = db.get_course(DB_PATH, course_id, _uid())
    if course is None:
        return "Course not found", 404
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        raw_date = request.form.get("exam_date", "")
        error = None
        if not name:
            error = "Please enter a course name."
        elif not _valid_exam_date(raw_date):
            error = "Please enter a valid exam date (YYYY-MM-DD)."
        if error is None:
            db.update_course(DB_PATH, course_id, _uid(), name, raw_date)
            return redirect(url_for("course_detail", course_id=course_id,
                                    notice="course-updated"))
        return render_template("edit_course.html", course=course, error=error,
                               name=name, exam_date=raw_date)
    return render_template("edit_course.html", course=course)


@app.route("/courses/<int:course_id>/delete", methods=["GET", "POST"])
@login_required
def delete_course(course_id):
    course = db.get_course(DB_PATH, course_id, _uid())
    if course is None:
        return "Course not found", 404
    if request.method == "POST":
        if request.form.get("confirm") == "yes":
            db.delete_course(DB_PATH, course_id, _uid())
            return redirect(url_for("index", notice="course-deleted"))
        return redirect(url_for("course_detail", course_id=course_id))
    preview = db.course_delete_preview(DB_PATH, course_id, _uid())
    return render_template("delete_course.html", course=course,
                           preview=preview)


def _get_course_topic_or_404(course_id, topic_id):
    """Topic belonging to the course, else None (covers missing course)."""
    course = db.get_course(DB_PATH, course_id, _uid())
    if course is None:
        return None, None
    topic = db.get_topic(DB_PATH, topic_id, _uid())
    if topic is None or topic["course_id"] != course_id:
        return course, None
    return course, topic


@app.route("/courses/<int:course_id>/topics/<int:topic_id>/edit",
           methods=["GET", "POST"])
@login_required
def edit_topic(course_id, topic_id):
    course, topic = _get_course_topic_or_404(course_id, topic_id)
    if course is None or topic is None:
        return "Topic not found", 404
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        try:
            difficulty = int(request.form.get("difficulty", 3))
        except (TypeError, ValueError):
            difficulty = 3
        error = None
        if not title:
            error = "Please enter a topic title."
        elif not 1 <= difficulty <= 5:
            error = "Difficulty must be between 1 and 5."
        if error is None:
            db.update_topic(DB_PATH, topic_id, _uid(), title, difficulty)
            return redirect(url_for("course_detail", course_id=course_id,
                                    notice="topic-updated"))
        return render_template("edit_topic.html", course=course, topic=topic,
                               error=error, title=title, difficulty=difficulty)
    return render_template("edit_topic.html", course=course, topic=topic)


@app.route("/courses/<int:course_id>/topics/<int:topic_id>/delete",
           methods=["GET", "POST"])
@login_required
def delete_topic(course_id, topic_id):
    course, topic = _get_course_topic_or_404(course_id, topic_id)
    if course is None or topic is None:
        return "Topic not found", 404
    if request.method == "POST":
        if request.form.get("confirm") == "yes":
            db.delete_topic(DB_PATH, topic_id, _uid())
            return redirect(url_for("course_detail", course_id=course_id,
                                    notice="topic-deleted"))
        return redirect(url_for("course_detail", course_id=course_id))
    session_count = db.count_topic_sessions(DB_PATH, topic_id, _uid())
    return render_template("delete_topic.html", course=course, topic=topic,
                           session_count=session_count)


@app.route("/courses/<int:course_id>/topics", methods=["POST"])
@login_required
def add_topic(course_id):
    if db.get_course(DB_PATH, course_id, _uid()) is None:
        return "Course not found", 404
    title = request.form.get("title", "").strip()
    difficulty = request.form.get("difficulty", 3)
    if title:
        db.add_topic(DB_PATH, course_id, _uid(), title, difficulty)
    return redirect(url_for("course_detail", course_id=course_id))


@app.route("/courses/<int:course_id>/plan", methods=["POST"])
@login_required
def generate_plan(course_id):
    course = db.get_course(DB_PATH, course_id, _uid())
    if course is None:
        return "Course not found", 404
    minutes = request.form.get("minutes_per_day", 60, type=int)
    use_avail = request.form.get("use_availability") == "1"
    try:
        plan = scheduler.generate_plan(DB_PATH, course_id, _uid(),
                                       minutes_per_day=minutes,
                                       use_availability=use_avail)
    except ValueError:
        # defensive: a stored exam date that is not a real YYYY-MM-DD
        # (possible only in databases predating input validation)
        return ("This course has an invalid exam date and cannot be "
                "scheduled.", 400)
    scheduler.save_plan(DB_PATH, course_id, _uid(), plan)
    return redirect(url_for("course_detail", course_id=course_id))


@app.route("/plan")
@login_required
def plan():
    """Seven-day view of planned sessions, today highlighted."""
    today = date.today()
    sessions = db.list_sessions_for_week(DB_PATH, _uid(), today.isoformat())
    by_day = {}
    for s in sessions:
        by_day.setdefault(s["planned_date"], []).append(s)
    days = []
    for i in range(7):
        day = today + timedelta(days=i)
        days.append({
            "date": day.strftime("%b %d"),
            "iso": day.isoformat(),
            "label": "Today" if day == today else day.strftime("%A"),
            "is_today": day == today,
            "sessions": by_day.get(day.isoformat(), []),
        })
    return render_template("plan.html", days=days)


@app.route("/courses/<int:course_id>/quiz", methods=["GET", "POST"])
@login_required
def quiz(course_id):
    """Optional AI practice: pick a topic, get a quiz, reveal answers."""
    course = db.get_course(DB_PATH, course_id, _uid())
    if course is None:
        return "Course not found", 404
    topics = db.list_topics(DB_PATH, course_id, _uid())
    quiz_data, tips, quiz_error = None, None, None
    if request.method == "POST":
        if request.form.get("action") == "tips":
            tips = ai.generate_tips(course["name"])
        elif request.form.get("topic"):
            topic_title = request.form["topic"]
            # the topic must belong to this course; never trust raw input
            if topic_title not in {t["title"] for t in topics}:
                quiz_error = ("That topic does not belong to this course. "
                              "Please pick one from the list.")
            else:
                quiz_data = ai.generate_quiz(
                    topic_title, request.form.get("difficulty", "medium"))
    return render_template(
        "quiz.html", course=course, topics=topics,
        ai_enabled=ai.is_enabled(), quiz=quiz_data, tips=tips,
        quiz_error=quiz_error,
    )


@app.route("/sessions/<int:session_id>/done", methods=["POST"])
@login_required
def toggle_done(session_id):
    db.toggle_session(DB_PATH, session_id, _uid())
    return redirect(request.referrer or url_for("plan"))


@app.route("/notes", methods=["GET", "POST"])
@login_required
def notes():
    """AI study notes: paste material, pick a style, get structured notes."""
    result, doc, style, focus = None, "", "comprehensive", ""
    if request.method == "POST":
        doc = _read_document_content()
        style = request.form.get("note_style", "comprehensive")
        focus = request.form.get("focus_area", "").strip()
        if len(doc) >= 50:
            result = ai.generate_notes(doc, style, focus or None)
        else:
            result = {"enabled": True,
                      "error": "Please paste at least 50 characters."}
    return render_template(
        "notes.html", ai_enabled=ai.is_enabled(), result=result,
        document_content=doc, note_style=style, focus_area=focus,
    )


@app.route("/flashcards", methods=["GET", "POST"])
@login_required
def flashcards():
    """AI flashcards: paste material, flip through generated cards."""
    result, doc, count = None, "", 8
    if request.method == "POST":
        doc = _read_document_content()
        count = request.form.get("count", 8, type=int)
        if len(doc) >= 50:
            result = ai.generate_flashcards(doc, count)
        else:
            result = {"enabled": True,
                      "error": "Please paste at least 50 characters."}
    return render_template(
        "flashcards.html", ai_enabled=ai.is_enabled(), result=result,
        document_content=doc, count=count,
    )


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]


@app.route("/availability", methods=["GET", "POST"])
@login_required
def availability():
    """Set per-weekday study minutes used by the scheduler."""
    saved = False
    if request.method == "POST":
        minutes = {}
        for wd in range(7):
            raw = request.form.get(f"day_{wd}", "")
            try:
                minutes[wd] = max(0, int(raw))
            except (TypeError, ValueError):
                minutes[wd] = db.DEFAULT_MINUTES
        db.set_availability(DB_PATH, _uid(), minutes)
        saved = True
    return render_template(
        "availability.html",
        days=[{"weekday": i, "name": n} for i, n in enumerate(WEEKDAYS)],
        availability=db.get_availability(DB_PATH, _uid()),
        saved=saved,
    )


@app.route("/courses/<int:course_id>/ai-plan", methods=["POST"])
@login_required
def ai_plan(course_id):
    """AI-written weekly plan for a course (complements the scheduler)."""
    course = db.get_course(DB_PATH, course_id, _uid())
    if course is None:
        return "Course not found", 404
    topics = db.list_topics(DB_PATH, course_id, _uid())
    sessions = db.list_sessions(DB_PATH, course_id, _uid())
    plan = ai.generate_weekly_plan(
        course["name"], topics, course.get("exam_date"))
    return render_template(
        "course_detail.html", course=course, topics=topics,
        session_count=len(sessions), ai_enabled=ai.is_enabled(),
        ai_plan=plan)


def _safe_filename(name):
    """Course name -> safe download filename stem."""
    stem = "".join(c if c.isalnum() else "-" for c in name.strip().lower())
    stem = "-".join(filter(None, stem.split("-")))
    return (stem or "study-guide")[:60]


# --- interactive study sessions (server-side state, no persistence) ---

import secrets as _secrets
import time as _time

# Sessions live only in process memory and are lost on app restart.
# Eviction policy (deterministic, documented honestly):
#  1. Idle expiry: sessions untouched for SESSION_TTL_SECONDS are removed.
#     Every request touching a session refreshes its timestamp, so an
#     actively-answering user is never expired by this rule.
#  2. Hard cap: if sessions still exceed MAX_SESSIONS after idle expiry,
#     the least-recently-active sessions are evicted first. In the extreme
#     case where every session is within its TTL, the cap is still
#     enforced — a recently-active session CAN be evicted to keep memory
#     bounded. This is the documented tradeoff, not a bug.
SESSIONS = {}  # token -> session dict
SESSION_TTL_SECONDS = 2 * 60 * 60  # 2 hours idle
MAX_SESSIONS = 20


def _now():
    return _time.time()


def _prune_sessions(now=None):
    """Apply the documented eviction policy; never lets SESSIONS grow."""
    now = now if now is not None else _now()
    # phase 1: idle expiry
    for token in [t for t, s in SESSIONS.items()
                  if now - s["last_active"] > SESSION_TTL_SECONDS]:
        del SESSIONS[token]
    # phase 2: hard cap, least-recently-active first
    while len(SESSIONS) > MAX_SESSIONS:
        oldest = min(SESSIONS, key=lambda t: SESSIONS[t]["last_active"])
        del SESSIONS[oldest]
SESSION_LENGTHS = (5, 10, 15)

PRACTICE_TEMPLATES = (
    "In your own words, explain: {title}.",
    "What are the key ideas someone must understand about {title}?",
    "What mistakes do students commonly make with {title}, "
    "and how would you avoid them?",
)


def _practice_questions(topics, length):
    """Deterministic questions from topic titles; never duplicated."""
    questions = []
    for t in topics:
        for tpl in PRACTICE_TEMPLATES:
            q = tpl.format(title=t["title"])
            if q not in [x["question"] for x in questions]:
                questions.append({"topic": t["title"], "question": q})
            if len(questions) >= length:
                return questions
    return questions


def _get_session_or_404(token):
    _prune_sessions()
    sess = SESSIONS.get(token)
    if sess is None or sess.get("user_id") != _uid():
        return None
    sess["last_active"] = _now()  # active use keeps the session alive
    return sess


@app.route("/courses/<int:course_id>/session", methods=["GET"])
@login_required
def session_setup(course_id):
    """Study session setup: topics, length, AI Coach vs Practice Mode."""
    course = db.get_course(DB_PATH, course_id, _uid())
    if course is None:
        return "Course not found", 404
    topics = db.list_topics(DB_PATH, course_id, _uid())
    return render_template(
        "session_setup.html", course=course, topics=topics,
        ai_enabled=ai.is_enabled(), lengths=SESSION_LENGTHS)


@app.route("/courses/<int:course_id>/session/start", methods=["POST"])
@login_required
def session_start(course_id):
    """Validate setup choices and create the session."""
    course = db.get_course(DB_PATH, course_id, _uid())
    if course is None:
        return "Course not found", 404
    topics = db.list_topics(DB_PATH, course_id, _uid())
    valid_ids = {t["id"] for t in topics}
    try:
        chosen_ids = {int(i) for i in request.form.getlist("topic_ids")}
    except (TypeError, ValueError):
        chosen_ids = set()
    chosen_ids &= valid_ids
    chosen = [t for t in topics if t["id"] in chosen_ids]
    if not chosen:
        return render_template(
            "session_setup.html", course=course, topics=topics,
            ai_enabled=ai.is_enabled(), lengths=SESSION_LENGTHS,
            error="Select at least one topic to start a session."), 400
    try:
        length = int(request.form.get("length", 5))
    except (TypeError, ValueError):
        length = 5
    if length not in SESSION_LENGTHS:
        length = 5
    mode = request.form.get("mode", "practice")
    if mode == "ai":
        if not ai.is_enabled():
            return render_template(
                "session_setup.html", course=course, topics=topics,
                ai_enabled=False, lengths=SESSION_LENGTHS,
                error="AI Coach needs a GEMINI_API_KEY. "
                      "Use Practice Mode instead."), 400
        first = ai.generate_coach_question(course["name"], chosen)
        if first.get("error") or not first.get("question"):
            return render_template(
                "session_setup.html", course=course, topics=topics,
                ai_enabled=True, lengths=SESSION_LENGTHS,
                error=first.get("error") or "Could not start the session."), 502
        questions = [{"topic": None, "question": first["question"]}]
    else:
        mode = "practice"
        questions = _practice_questions(chosen, length)

    token = _secrets.token_urlsafe(16)
    SESSIONS[token] = {
        "user_id": _uid(),
        "course_id": course_id, "course_name": course["name"],
        "topics": [{"title": t["title"]} for t in chosen],
        "mode": mode, "length": length,
        "questions": questions,
        "available": len(questions),  # practice may offer fewer than length
        "answers": [], "feedbacks": [],
        "current": 0, "finished": False,
        "error": None, "pending_answer": "",
        "last_active": _now(),
    }
    _prune_sessions()
    return redirect(url_for("study_session", token=token))


@app.route("/session/<token>", methods=["GET"])
@login_required
def study_session(token):
    """Current question (or final review when finished)."""
    sess = _get_session_or_404(token)
    if sess is None:
        return "Session not found or expired.", 404
    return render_template("session.html", sess=sess, token=token,
                           max_answer=ai.MAX_ANSWER_CHARS)


@app.route("/session/<token>/answer", methods=["POST"])
@login_required
def session_answer(token):
    """Submit an answer; AI mode evaluates, practice mode acknowledges."""
    sess = _get_session_or_404(token)
    if sess is None:
        return "Session not found or expired.", 404
    if sess["finished"]:
        return redirect(url_for("study_session", token=token))

    answer = request.form.get("answer", "").strip()
    if not answer:
        sess["error"] = "Please write an answer before submitting."
        sess["pending_answer"] = ""
        return redirect(url_for("study_session", token=token))
    if len(answer) > ai.MAX_ANSWER_CHARS:
        sess["error"] = (f"Answer too long (max {ai.MAX_ANSWER_CHARS} "
                         "characters). Please shorten it and retry.")
        sess["pending_answer"] = answer[:ai.MAX_ANSWER_CHARS]
        return redirect(url_for("study_session", token=token))

    idx = sess["current"]
    question = sess["questions"][idx]["question"]

    if sess["mode"] == "ai":
        history = [{"question": sess["questions"][i]["question"],
                    "answer": sess["answers"][i],
                    "feedback": _feedback_text(sess["feedbacks"][i])}
                   for i in range(idx)]
        result = ai.evaluate_coach_answer(
            question, answer, sess["course_name"], sess["topics"])
        if result.get("error"):
            # do not advance; preserve answer for retry
            sess["error"] = result["error"]
            sess["pending_answer"] = answer
            return redirect(url_for("study_session", token=token))
        feedback = {k: result[k] for k in
                    ("covered", "missed", "suggestion", "followup")}
        sess["answers"].append(answer)
        sess["feedbacks"].append(feedback)
        sess["questions"].append(
            {"topic": None, "question": result["followup"]})
    else:
        sess["answers"].append(answer)
        sess["feedbacks"].append(None)

    sess["current"] += 1
    sess["error"] = None
    sess["pending_answer"] = ""
    if sess["mode"] == "ai":
        done = len(sess["answers"]) >= sess["length"]
    else:
        done = sess["current"] >= len(sess["questions"])
    if done:
        sess["finished"] = True
    return redirect(url_for("study_session", token=token))


@app.route("/session/<token>/finish", methods=["POST"])
@login_required
def session_finish(token):
    """End the session early; review shows completed interactions."""
    sess = _get_session_or_404(token)
    if sess is None:
        return "Session not found or expired.", 404
    sess["finished"] = True
    sess["error"] = None
    return redirect(url_for("study_session", token=token))


def _feedback_text(feedback):
    if not feedback:
        return ""
    return (f"Covered: {feedback['covered']} "
            f"Missed: {feedback['missed']} "
            f"Suggestion: {feedback['suggestion']}")


@app.route("/courses/<int:course_id>/guide", methods=["GET", "POST"])
@login_required
def study_guide(course_id):
    """AI study guide built from the course's topics plus optional notes."""
    course = db.get_course(DB_PATH, course_id, _uid())
    if course is None:
        return "Course not found", 404
    topics = db.list_topics(DB_PATH, course_id, _uid())
    valid_ids = {t["id"] for t in topics}

    result, notes, depth = None, "", "standard"
    selected_ids = set(valid_ids)  # default: all topics
    if request.method == "POST":
        raw_ids = request.form.getlist("topic_ids")
        try:
            selected_ids = {int(i) for i in raw_ids}
        except (TypeError, ValueError):
            selected_ids = set()
        # reject unknown ids and ids from other courses
        selected_ids &= valid_ids
        notes = request.form.get("additional_notes", "")[:ai.MAX_NOTES_CHARS]
        depth = request.form.get("depth", "standard")
        if depth not in ai.GUIDE_DEPTHS:
            depth = "standard"
        chosen = [t for t in topics if t["id"] in selected_ids]
        if not chosen and not notes.strip():
            result = {"enabled": True, "error":
                      "Select at least one topic or add some notes."}
        else:
            result = ai.generate_study_guide(
                course["name"], chosen, notes or None, depth)

    return render_template(
        "guide.html", course=course, topics=topics,
        selected_ids=selected_ids, additional_notes=notes, depth=depth,
        ai_enabled=ai.is_enabled(), result=result,
        download_name=f"{_safe_filename(course['name'])}-study-guide.txt",
    )


if __name__ == "__main__":
    app.run(debug=True)
