"""Study-plan scheduling engine (day 2).

Turns a course's topics into a day-by-day list of study sessions using:
  - urgency: topics for closer exams get scheduled first
  - difficulty: harder topics get bigger time blocks
  - spaced repetition: each topic resurfaces 1, 3 and 7 days after its
    first session
"""
from datetime import date, timedelta

from studygenie import db

MIN_BLOCK = 10            # shortest session worth scheduling, in minutes
REVIEW_OFFSETS = (1, 3, 7)  # days after the first session a topic resurfaces


def urgency_score(exam_day, day):
    """How urgent the exam is on a given day: 1 / days left.

    An overdue (or tomorrow) exam scores 1.0, the maximum; an exam
    10 days out scores 0.1.
    """
    days_left = (exam_day - day).days
    return 1.0 / max(1, days_left)


def difficulty_weight(difficulty):
    return difficulty / 3.0


def topic_priority(exam_day, difficulty, day):
    """Higher means: study it earlier, in a bigger block."""
    return urgency_score(exam_day, day) * difficulty_weight(difficulty)


def allocate_minutes(weights, budget):
    """Split a day's budget across sessions, proportional to weight.

    Every session gets at least MIN_BLOCK minutes when that fits;
    the day's total never exceeds the budget.
    """
    total = sum(weights)
    raw = [budget * w / total for w in weights]
    minimum = MIN_BLOCK if len(weights) * MIN_BLOCK <= budget else 1
    blocks = [max(minimum, int(r)) for r in raw]
    # hand out leftover minutes (or take back overruns) one at a time
    i = 0
    while sum(blocks) < budget:
        blocks[i % len(blocks)] += 1
        i += 1
    while sum(blocks) > budget:
        i = max(range(len(blocks)), key=lambda j: blocks[j] - minimum)
        if blocks[i] <= minimum:
            break
        blocks[i] -= 1
    return blocks


def _parse(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def generate_plan(db_path, course_id, minutes_per_day=60, start_date=None):
    """Build a study plan for a course.

    Returns a list of dicts: {date, topic_id, topic_title, minutes}.
    Harder + more urgent topics get earlier, bigger blocks; each day's
    total stays within minutes_per_day.
    """
    start = _parse(start_date) if start_date else date.today()
    course = db.get_course(db_path, course_id)
    exam = _parse(course["exam_date"])
    topics = db.list_topics(db_path, course_id)

    # hardest/most urgent topics first
    ranked = sorted(
        topics,
        key=lambda t: topic_priority(exam, t["difficulty"], start),
        reverse=True,
    )

    # days we can still study: today through the exam; an overdue
    # exam just leaves us today
    window = max(1, (exam - start).days + 1)
    per_day = max(1, minutes_per_day // MIN_BLOCK)

    # pack first sessions day by day, hardest first; overflow lands
    # on the last available day
    first_dates = {}
    day, placed_today = start, 0
    for t in ranked:
        first_dates[t["id"]] = day.isoformat()
        placed_today += 1
        if placed_today >= per_day and (day - start).days + 1 < window:
            day = day + timedelta(days=1)
            placed_today = 0

    # gather each day's sessions: first sessions plus spaced reviews
    by_day = {}
    for t in ranked:
        first = first_dates[t["id"]]
        by_day.setdefault(first, []).append((t, 1.0))
        for offset in REVIEW_OFFSETS:
            review = date.fromisoformat(first) + timedelta(days=offset)
            if review <= exam:
                # reviews are shorter than first passes
                by_day.setdefault(review.isoformat(), []).append((t, 0.5))

    # turn weights into minutes, keeping each day within budget
    plan = []
    for day_iso in sorted(by_day):
        sessions = by_day[day_iso]
        weights = [t["difficulty"] * kind_weight for t, kind_weight in sessions]
        for (t, _), minutes in zip(sessions, allocate_minutes(weights, minutes_per_day)):
            plan.append({
                "date": day_iso,
                "topic_id": t["id"],
                "topic_title": t["title"],
                "minutes": minutes,
            })
    return plan


def save_plan(db_path, course_id, plan):
    """Store a plan, replacing the course's unstarted sessions."""
    db.clear_pending_sessions(db_path, course_id)
    for s in plan:
        db.add_session(db_path, course_id, s["topic_id"], s["date"], s["minutes"])
    return len(plan)
