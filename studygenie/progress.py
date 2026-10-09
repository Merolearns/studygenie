"""Progress stats for StudyGenie (day 4).

Streak, weekly completion, and minutes studied — all computed from the
sessions table, no extra state to maintain.
"""
from datetime import date, timedelta

from studygenie import db


def _iso(day):
    return day.isoformat()


def streak(db_path, today=None):
    """Consecutive calendar days ending today with a completed session."""
    today = today or date.today()
    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT planned_date FROM sessions WHERE completed = 1"
        ).fetchall()
    done_days = {r["planned_date"] for r in rows}
    count = 0
    day = today
    while _iso(day) in done_days:
        count += 1
        day -= timedelta(days=1)
    return count


def weekly_completion(db_path, today=None):
    """(pct, done, total) for sessions planned in the last 7 days."""
    today = today or date.today()
    start = _iso(today - timedelta(days=6))
    with db.connect(db_path) as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions"
            " WHERE planned_date BETWEEN ? AND ?",
            (start, _iso(today)),
        ).fetchone()["n"]
        done = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions"
            " WHERE planned_date BETWEEN ? AND ? AND completed = 1",
            (start, _iso(today)),
        ).fetchone()["n"]
    pct = round(100 * done / total) if total else 0
    return pct, done, total


def total_minutes(db_path):
    """Minutes from all completed sessions."""
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(minutes), 0) AS m"
            " FROM sessions WHERE completed = 1"
        ).fetchone()
    return row["m"]


def minutes_by_day(db_path, today=None, days=7):
    """Minutes studied per day for the last `days` days, oldest first."""
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT planned_date, SUM(minutes) AS m FROM sessions"
            " WHERE completed = 1 AND planned_date BETWEEN ? AND ?"
            " GROUP BY planned_date",
            (_iso(start), _iso(today)),
        ).fetchall()
    by_date = {r["planned_date"]: r["m"] for r in rows}
    series = []
    for i in range(days):
        day = start + timedelta(days=i)
        series.append({
            "date": _iso(day),
            "label": day.strftime("%a"),
            "minutes": by_date.get(_iso(day), 0),
        })
    return series


def course_progress(db_path):
    """Per-course session progress. pct is None when no plan exists yet."""
    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT c.id, c.name,"
            " COUNT(s.id) AS total,"
            " COALESCE(SUM(s.completed), 0) AS done"
            " FROM courses c LEFT JOIN sessions s ON s.course_id = c.id"
            " GROUP BY c.id ORDER BY c.exam_date"
        ).fetchall()
    out = []
    for r in rows:
        total = r["total"]
        out.append({
            "id": r["id"],
            "name": r["name"],
            "done": r["done"],
            "total": total,
            "pct": round(100 * r["done"] / total) if total else None,
        })
    return out
