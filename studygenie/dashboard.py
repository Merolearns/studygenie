"""Dashboard data for StudyGenie's home page.

Answers: what should I study next, what needs attention, what did I
recently complete, and how am I progressing. Everything is derived from
the existing sessions/topics/courses tables — no new state, no scores
invented. Reasons are explainable (exam proximity, difficulty, overdue).
"""
from datetime import date

from studygenie import db


def _today(today):
    return today or date.today()


def _enrich(rows, today):
    """Add course/topic context and a human-readable reason to sessions."""
    out = []
    for r in rows:
        exam = date.fromisoformat(r["exam_date"])
        days_left = (exam - today).days
        planned = date.fromisoformat(r["planned_date"])
        overdue_days = (today - planned).days
        if overdue_days > 0:
            reason = f"Planned {planned.strftime('%b %d')} — {overdue_days}d overdue"
        elif days_left <= 3:
            reason = f"Exam in {days_left}d · difficulty {r['difficulty']}/5"
        elif days_left <= 10:
            reason = f"Exam in {days_left}d"
        else:
            reason = f"Difficulty {r['difficulty']}/5 · exam in {days_left}d"
        out.append({
            "id": r["id"],
            "course_name": r["course_name"],
            "topic_title": r["topic_title"],
            "planned_date": r["planned_date"],
            "minutes": r["minutes"],
            "is_review": bool(r["is_review"]),
            "reason": reason,
            "overdue": overdue_days > 0,
            "_sort": (overdue_days <= 0, -overdue_days,
                      days_left, -r["difficulty"]),
        })
    return out


def _sessions_with_context(db_path):
    """All sessions joined to topics/courses, with review flags.

    A session is a review when the topic has an earlier session;
    the topic's earliest session is its first pass.
    """
    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT s.id, s.topic_id, s.planned_date, s.minutes, s.completed,"
            " c.name AS course_name, c.exam_date,"
            " t.title AS topic_title, COALESCE(t.difficulty, 3) AS difficulty,"
            " CASE WHEN s.planned_date > ("
            "   SELECT MIN(s2.planned_date) FROM sessions s2"
            "   WHERE s2.topic_id = s.topic_id) THEN 1 ELSE 0 END AS is_review"
            " FROM sessions s"
            " JOIN courses c ON s.course_id = c.id"
            " LEFT JOIN topics t ON s.topic_id = t.id"
        ).fetchall()
        return [dict(r) for r in rows]


def up_next(db_path, today=None, limit=5):
    """Most urgent incomplete sessions, most urgent first."""
    today = _today(today)
    rows = [r for r in _sessions_with_context(db_path) if not r["completed"]]
    enriched = _enrich(rows, today)
    enriched.sort(key=lambda s: s["_sort"])
    return enriched[:limit]


def overdue(db_path, today=None):
    """Incomplete sessions planned before today, most overdue first."""
    today = _today(today)
    iso = today.isoformat()
    rows = [r for r in _sessions_with_context(db_path)
            if not r["completed"] and r["planned_date"] < iso]
    enriched = _enrich(rows, today)
    enriched.sort(key=lambda s: s["planned_date"])
    return enriched


def recently_completed(db_path, limit=5):
    """Completed sessions, most recently planned first."""
    rows = [r for r in _sessions_with_context(db_path) if r["completed"]]
    rows.sort(key=lambda r: r["planned_date"], reverse=True)
    return [{
        "course_name": r["course_name"],
        "topic_title": r["topic_title"],
        "planned_date": r["planned_date"],
        "minutes": r["minutes"],
    } for r in rows[:limit]]


def upcoming_exams(db_path, today=None):
    """Courses with exams today or later, soonest first."""
    today = _today(today)
    exams = []
    for c in db.list_courses(db_path):
        try:
            exam = date.fromisoformat(c["exam_date"])
        except ValueError:
            continue  # legacy bad row; Phase 1 validation prevents new ones
        days_left = (exam - today).days
        if days_left >= 0:
            exams.append({"id": c["id"], "name": c["name"],
                          "exam_date": c["exam_date"], "days_left": days_left})
    exams.sort(key=lambda e: e["days_left"])
    return exams
