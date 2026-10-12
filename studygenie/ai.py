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


def generate_quiz(topic_title):
    """Five multiple-choice practice questions about a topic.

    Returns {"enabled": False} without an API key, or a dict with the
    questions (and an "error" key if the API call failed). Never raises.
    """
    model = _model()
    if model is None:
        return {"enabled": False}
    prompt = (
        f"Write 5 multiple-choice practice questions about the topic "
        f"'{topic_title}', for a college student preparing for an exam. "
        "Reply with ONLY a JSON object shaped like this:\n"
        '{"questions": [{"question": "...", "choices": ["A", "B", "C", "D"], '
        '"answer": "B"}]}\n'
        "Write the answer exactly as one of the listed choices."
    )
    data, error = _ask_json(model, prompt)
    if error:
        return {"enabled": True, "error": error}
    return {"enabled": True, "topic": topic_title,
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
