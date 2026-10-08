"""StudyGenie web app — day 3: week timetable with session check-off."""
import os
from datetime import date, timedelta
from flask import Flask, render_template, request, redirect, url_for

from studygenie import db, scheduler

DB_PATH = os.environ.get("STUDYGENIE_DB", "studygenie.db")

app = Flask(__name__)


@app.before_request
def _ensure_db():
    if not os.path.exists(DB_PATH):
        db.init_db(DB_PATH)


@app.route("/")
def index():
    courses = db.list_courses(DB_PATH)
    return render_template("index.html", courses=courses)


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
        session_count=len(sessions))


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
    plan = scheduler.generate_plan(DB_PATH, course_id, minutes_per_day=minutes)
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


@app.route("/sessions/<int:session_id>/done", methods=["POST"])
def toggle_done(session_id):
    db.toggle_session(DB_PATH, session_id)
    return redirect(request.referrer or url_for("plan"))


if __name__ == "__main__":
    app.run(debug=True)
