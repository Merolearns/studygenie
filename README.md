# StudyGenie

Turn your courses into a day-by-day study plan.

Add a course with its exam date, break it into topics by difficulty, and
StudyGenie builds a study schedule that puts harder, more urgent topics
first and resurfaces them on a spaced-repetition rhythm (1, 3, 7 days).
Check off sessions as you go and watch your streak, weekly completion,
and minutes studied grow.

## Screenshots

Run the app (see above), then capture these four pages and save them under
`docs/screenshots/`:

- `home.png` — the home page with course cards and progress bars
- `course.png` — a course page: topics, difficulty ratings, the "Generate plan" button
- `plan.png` — the 7-day week view with today highlighted
- `progress.png` — the progress page: streak, completion %, weekly Chart.js chart

## Run it

```bash
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

The database is plain SQLite (`studygenie.db` in the working directory),
created automatically on first run. Delete the file to start over.

## Features

- **Courses and topics** — add a course with an exam date, then add topics
  rated 1–5 for difficulty.
- **Scheduling engine** (`studygenie/scheduler.py`) — scores each topic by
  urgency (`1 / days until exam`) and difficulty, then packs each day up to
  your minutes-per-day budget. Sessions repeat on a 1/3/7-day spaced
  cadence so nothing is studied once and forgotten.
- **Week view** — see the next 7 days of sessions, with today highlighted,
  and check off completed ones.
- **Progress tracking** — current streak (consecutive days with a completed
  session), completion % this week, total minutes studied, per-course
  progress bars, and a Chart.js bar chart of the last 7 days.
- **Optional Gemini AI** — generate a 5-question practice quiz per topic
  and 3 study tips per course.

## Optional: enable the AI features

Everything works without an API key — the quiz page just explains how to
turn AI on. To enable it:

```bash
export GEMINI_API_KEY="your-key-from-aistudio.google.com"
python app.py
```

The key comes from the `GEMINI_API_KEY` environment variable only. It is
never hardcoded, never stored in a file, and the app never errors when the
key is missing.

## Tests

```bash
python -m pytest -q
```

38 tests: SQLite layer, scheduling engine (overdue exam prioritizes
correctly, harder topics get more minutes, every topic is covered),
progress stats, and route tests against a throwaway database.

## The 6-day build story

This repo was built in public, one day at a time, with a small honest
commit history:

1. **Scaffold** — Flask app, SQLite schema (courses, topics, sessions),
   course CRUD, base templates, first DB tests.
2. **Scheduling engine** — urgency scoring, difficulty weighting, spaced
   repetition; a "Generate plan" button that saves sessions to the DB.
3. **Timetable UI** — 7-day week view with today highlighted and
   session check-off.
4. **Progress tracking** — streak, weekly completion, minutes studied,
   per-course progress bars, Chart.js weekly chart.
5. **Optional Gemini AI** — practice quizzes and study tips via the
   Gemini API, fully graceful without a key.
6. **Polish & ship** — this README, a fresh-DB QA pass, and screenshots.

See [BUILD_PLAN.md](BUILD_PLAN.md) for the original plan.

## Project layout

```
studygenie/
├── app.py                 # Flask routes
├── requirements.txt
├── studygenie/
│   ├── db.py              # SQLite layer: courses, topics, sessions
│   ├── scheduler.py       # plan generation: urgency, difficulty, spaced repetition
│   ├── progress.py        # streak, weekly completion, minutes-by-day
│   └── ai.py              # optional Gemini quiz + tips (env-key, no-key safe)
├── templates/             # Jinja pages
├── static/style.css
└── tests/                 # pytest suite (throwaway DBs)
```
