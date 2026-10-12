"""Optional Gemini AI features (day 5).

Practice quizzes and study tips, powered by the Gemini API.
Everything works WITHOUT an API key — the helpers report
enabled=False and the UI shows how to turn AI on instead.
Keys come from the GEMINI_API_KEY environment variable,
never from code or config files.
"""
import json
import os

MODEL = "gemini-1.5-flash"
API_KEY_ENV = "GEMINI_API_KEY"


def is_enabled():
    """True only when an API key is available in the environment."""
    return bool(os.environ.get(API_KEY_ENV))


def _model():
    """Build the Gemini client; None when AI is disabled."""
    if not is_enabled():
        return None
    import google.generativeai as genai  # late import: app runs without it
    genai.configure(api_key=os.environ[API_KEY_ENV])
    return genai.GenerativeModel(MODEL)


def _ask_json(model, prompt):
    """Ask the model for a JSON object; returns (data, error)."""
    try:
        text = model.generate_content(prompt).text.strip()
        if text.startswith("```"):  # strip markdown fences if the model wraps it
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text), None
    except Exception as exc:  # never break the app over an AI hiccup
        return None, str(exc)


def generate_quiz(topic_title, difficulty="medium"):
    """Five multiple-choice practice questions about a topic.

    Difficulty is one of easy/medium/hard, ported from the capstone's
    quiz service design. Returns {"enabled": False} without an API key,
    or a dict with the questions (and an "error" key if the API call
    failed). Never raises.
    """
    model = _model()
    if model is None:
        return {"enabled": False}
    if difficulty not in ("easy", "medium", "hard"):
        difficulty = "medium"
    difficulty_guidance = {
        "easy": "basic definitions and straightforward facts",
        "medium": "application of concepts with moderate complexity",
        "hard": "synthesis and analysis requiring deeper reasoning",
    }
    prompt = (
        f"Write 5 multiple-choice practice questions about the topic "
        f"'{topic_title}', for a college student preparing for an exam. "
        f"Difficulty: {difficulty} — {difficulty_guidance[difficulty]}. "
        "Reply with ONLY a JSON object shaped like this:\n"
        '{"questions": [{"question": "...", "choices": ["A", "B", "C", "D"], '
        '"answer": "B"}]}\n'
        "Write the answer exactly as one of the listed choices."
    )
    data, error = _ask_json(model, prompt)
    if error:
        return {"enabled": True, "error": error}
    return {"enabled": True, "topic": topic_title, "difficulty": difficulty,
            "questions": data.get("questions", [])}


def generate_tips(course_name):
    """Three short study tips for a course. Same contract as generate_quiz."""
    model = _model()
    if model is None:
        return {"enabled": False}
    prompt = (
        f"Give 3 short, practical study tips for a college course named "
        f"'{course_name}'. Reply with ONLY a JSON object shaped like this:\n"
        '{"tips": ["tip 1", "tip 2", "tip 3"]}'
    )
    data, error = _ask_json(model, prompt)
    if error:
        return {"enabled": True, "error": error}
    return {"enabled": True, "course": course_name, "tips": data.get("tips", [])}


NOTE_STYLES = ("comprehensive", "concise", "bullet-points", "outline")


def generate_notes(document_content, note_style="comprehensive",
                   focus_area=None):
    """Turn pasted study material into structured notes.

    Ported from the AI Study Organizer capstone's generate-notes flow:
    four styles plus an optional focus area, with graceful fallback
    when AI is unavailable. Returns {"enabled": False} without an API
    key, otherwise {"enabled": True, "notes": "..."} or an "error" key.
    Never raises.
    """
    model = _model()
    if model is None:
        return {"enabled": False}
    if note_style not in NOTE_STYLES:
        note_style = "comprehensive"
    focus = f"\nFocus Area: {focus_area}" if focus_area else ""
    style_guides = {
        "comprehensive": (
            "## Summary\n[2-3 sentence summary]\n\n"
            "## Key Concepts\n[detailed explanations]\n\n"
            "## Important Details\n[facts, numbers, dates]\n\n"
            "## Examples & Applications\n[real-world examples]\n\n"
            "## Takeaways\n[main points to remember]"
        ),
        "concise": (
            "## Summary\n[one sentence]\n\n"
            "## Key Points\n[2-3 brief points]\n\n"
            "## Important Details\n[essential facts only]"
        ),
        "bullet-points": (
            "• [key point with brief explanation], one per line"
        ),
        "outline": (
            "I. [Main topic]\n   A. [Sub-topic]\n   B. [Sub-topic]\n"
            "II. [Main topic]\n   A. [Sub-topic]"
        ),
    }
    prompt = (
        "You are an expert at summarizing study material into clear, "
        "well-organized notes.\n"
        f"Note Style: {note_style}{focus}\n\n"
        f"Use this format:\n{style_guides[note_style]}\n\n"
        f"Document Content:\n{document_content}"
    )
    try:
        notes = model.generate_content(prompt).text.strip()
        return {"enabled": True, "notes": notes, "style": note_style}
    except Exception as exc:
        return {"enabled": True, "error": str(exc)}


def generate_flashcards(document_content, count=8):
    """Generate real flashcards from study material.

    The capstone's flashcards were pseudo-text; this is the working
    version: Gemini returns front/back pairs as JSON. Same contract
    as the other helpers — {"enabled": False} without a key, never raises.
    """
    model = _model()
    if model is None:
        return {"enabled": False}
    count = max(2, min(int(count or 8), 20))
    prompt = (
        "You are an expert at creating concise, effective flashcards. "
        f"Based on the study material below, generate exactly {count} "
        "flashcards covering the main points. Each flashcard has a "
        "'front' (question, term, or key concept) and a 'back' (answer, "
        "definition, or explanation). Keep each side to 1-2 sentences.\n"
        "Reply with ONLY a JSON object shaped like this:\n"
        '{"flashcards": [{"front": "...", "back": "..."}]}\n\n'
        f"Study material:\n{document_content}"
    )
    data, error = _ask_json(model, prompt)
    if error:
        return {"enabled": True, "error": error}
    cards = data.get("flashcards", [])
    # keep only well-formed cards
    cards = [c for c in cards
             if isinstance(c, dict) and c.get("front") and c.get("back")]
    return {"enabled": True, "flashcards": cards[:count]}


def generate_weekly_plan(course_name, topics, exam_date=None):
    """AI-written hour-by-hour weekly study plan.

    Ported from the capstone's generate-schedule flow: Gemini takes the
    course, its topics (with difficulty), and the exam date, and writes
    a natural-language weekly plan. Complements the algorithmic
    scheduler — this is the human-readable version. Never raises.
    """
    model = _model()
    if model is None:
        return {"enabled": False}
    topic_lines = "\n".join(
        f"- {t['title']} (difficulty {t.get('difficulty', 3)}/5)"
        for t in topics) or "- (no topics yet)"
    exam = f"\nExam date: {exam_date}" if exam_date else ""
    prompt = (
        "You are an expert academic planner. Based on the course below, "
        "generate a detailed, hour-by-hour study schedule for one week.\n\n"
        "Instructions:\n"
        "- Cover all topics, giving harder topics earlier and longer blocks.\n"
        "- Include realistic activities (review notes, practice problems, "
        "self-quizzing) and short breaks.\n"
        "- Keep a positive, encouraging tone.\n"
        "- Format neatly by day of the week.\n\n"
        f"Course: {course_name}{exam}\n"
        f"Topics:\n{topic_lines}"
    )
    try:
        plan = model.generate_content(prompt).text.strip()
        return {"enabled": True, "plan": plan, "course": course_name}
    except Exception as exc:
        return {"enabled": True, "error": str(exc)}
