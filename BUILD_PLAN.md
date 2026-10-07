# StudyGenie — 6-day build plan

AI-powered study schedule generator (Flask + SQLite + vanilla JS).
Original code, built in public, one honest step at a time.

## Day 1 (Oct 6) — Scaffold ✅
- Flask app structure, SQLite schema (courses, topics, sessions)
- Course CRUD (add/list/view), topic add
- Base templates + CSS, README skeleton, .gitignore
- DB unit tests passing

## Day 2 (Oct 7) — Scheduling engine
- `scheduler.py`: urgency score (days until exam), difficulty weight,
  spaced-repetition intervals (1/3/7 days)
- `generate_plan(course_id, minutes_per_day)` -> list of sessions
- Unit tests for the engine (overdue topic prioritized, etc.)

## Day 3 (Oct 8) — Timetable UI
- Week view + "today" view of planned sessions
- Check off completed sessions, minutes studied log
- Session model wired to the plan generator

## Day 4 (Oct 9) — Progress tracking
- Streak counter, completion %, per-course progress bars
- Chart.js weekly minutes chart
- Dashboard summary cards

## Day 5 (Oct 10) — Optional Gemini AI
- `ai.py`: practice quiz + study tips via Gemini API
- Works fully WITHOUT a key (graceful fallback message)
- Never hardcode keys; reads GEMINI_API_KEY env var

## Day 6 (Oct 11) — Polish & ship
- README with screenshots + demo GIF instructions
- Final QA pass, all tests green
- Pin repo on profile (replaces neon-drift)

## Rules
- 2–4 commits per day, natural messages, current dates only
- Every day's code must run; tests must pass before push
- No fake history, no backdating, no invented metrics
