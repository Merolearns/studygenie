"""StudyGenie web app — Flask + SQLite study planner."""
import os
from datetime import date, timedelta
from flask import Flask, render_template, request, redirect, url_for

from studygenie import db, scheduler, progress, ai

DB_PATH = os.environ.get("STUDYGENIE_DB", "studygenie.db")

app = Flask(__name__)

MAX_UPLOAD_CHARS = 20000  # cap pasted/uploaded text sent to the AI


def _read_document_content():
    """Pasted textarea text, or text extracted from an uploaded .txt file."""
    doc = request.form.get("document_content", "").strip()
    f = request.files.get("document_file")
    if f and f.filename:
        try:
            text = f.read().decode("utf-8", errors="replace").strip()
            if text:
                doc = text[:MAX_UPLOAD_CHARS]
        except Exception:
            pass
    return doc


@app.before_request
def _ensure_db():
    if not os.path.exists(DB_PATH):
        db.init_db(DB_PATH)


@app.route("/")
def index():
    courses = db.list_courses(DB_PATH)
    by_id = {p["id"]: p for p in progress.course_progress(DB_PATH)}
    for c in courses:
        c["progress_pct"] = by_id.get(c["id"], {}).get("pct")
    return render_template("index.html", courses=courses)


@app.route("/progress")
def progress_page():
    pct, done, total = progress.weekly_completion(DB_PATH)
    return render_template(
        "progress.html",
        streak=progress.streak(DB_PATH),
        weekly_pct=pct,
        weekly_done=done,
        weekly_total=total,
        total_minutes=progress.total_minutes(DB_PATH),
        chart=progress.minutes_by_day(DB_PATH),
    )


@app.route("/courses/new", methods=["GET", "POST"])
def new_course():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        exam_date = request.form.get("exam_date", "").strip()
        if name and exam_date:
            course_id = db.add_course(DB_PATH, name, exam_date)
            return redirect(url_for("course_detail", course_id=course_id))
    return render_template("new_course.html")


@app.route("/courses/<int:course_id>")
def course_detail(course_id):
    course = db.get_course(DB_PATH, course_id)
    if course is None:
        return "Course not found", 404
    topics = db.list_topics(DB_PATH, course_id)
    sessions = db.list_sessions(DB_PATH, course_id)
    return render_template(
        "course_detail.html", course=course, topics=topics,
        session_count=len(sessions), ai_enabled=ai.is_enabled(),
        ai_plan=None)


@app.route("/courses/<int:course_id>/topics", methods=["POST"])
def add_topic(course_id):
    title = request.form.get("title", "").strip()
    difficulty = request.form.get("difficulty", 3)
    if title:
        db.add_topic(DB_PATH, course_id, title, difficulty)
    return redirect(url_for("course_detail", course_id=course_id))


@app.route("/courses/<int:course_id>/plan", methods=["POST"])
def generate_plan(course_id):
    course = db.get_course(DB_PATH, course_id)
    if course is None:
        return "Course not found", 404
    minutes = request.form.get("minutes_per_day", 60, type=int)
    use_avail = request.form.get("use_availability") == "1"
    plan = scheduler.generate_plan(DB_PATH, course_id,
                                   minutes_per_day=minutes,
                                   use_availability=use_avail)
    scheduler.save_plan(DB_PATH, course_id, plan)
    return redirect(url_for("course_detail", course_id=course_id))


@app.route("/plan")
def plan():
    """Seven-day view of planned sessions, today highlighted."""
    today = date.today()
    sessions = db.list_sessions_for_week(DB_PATH, today.isoformat())
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
def quiz(course_id):
    """Optional AI practice: pick a topic, get a quiz, reveal answers."""
    course = db.get_course(DB_PATH, course_id)
    if course is None:
        return "Course not found", 404
    topics = db.list_topics(DB_PATH, course_id)
    quiz_data, tips = None, None
    if request.method == "POST":
        if request.form.get("action") == "tips":
            tips = ai.generate_tips(course["name"])
        elif request.form.get("topic"):
            quiz_data = ai.generate_quiz(
                request.form["topic"],
                request.form.get("difficulty", "medium"))
    return render_template(
        "quiz.html", course=course, topics=topics,
        ai_enabled=ai.is_enabled(), quiz=quiz_data, tips=tips,
    )


@app.route("/sessions/<int:session_id>/done", methods=["POST"])
def toggle_done(session_id):
    db.toggle_session(DB_PATH, session_id)
    return redirect(request.referrer or url_for("plan"))


@app.route("/notes", methods=["GET", "POST"])
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
        db.set_availability(DB_PATH, minutes)
        saved = True
    return render_template(
        "availability.html",
        days=[{"weekday": i, "name": n} for i, n in enumerate(WEEKDAYS)],
        availability=db.get_availability(DB_PATH),
        saved=saved,
    )


@app.route("/courses/<int:course_id>/ai-plan", methods=["POST"])
def ai_plan(course_id):
    """AI-written weekly plan for a course (complements the scheduler)."""
    course = db.get_course(DB_PATH, course_id)
    if course is None:
        return "Course not found", 404
    topics = db.list_topics(DB_PATH, course_id)
    sessions = db.list_sessions(DB_PATH, course_id)
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


@app.route("/courses/<int:course_id>/guide", methods=["GET", "POST"])
def study_guide(course_id):
    """AI study guide built from the course's topics plus optional notes."""
    course = db.get_course(DB_PATH, course_id)
    if course is None:
        return "Course not found", 404
    topics = db.list_topics(DB_PATH, course_id)
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
