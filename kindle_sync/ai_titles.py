"""
Generates short descriptive titles for Kindle highlights using Gemini 1.5 Flash (free tier).
One batched API call per book — all highlights sent together for efficiency.
Falls back to first-N-words if the API call fails or key is missing.
"""
import logging
import re

log = logging.getLogger(__name__)

MODEL = "models/gemma-4-31b-it"
EXCERPT_LEN = 400  # chars sent to the model per highlight


def get_client(api_key: str):
    from google import genai
    return genai.Client(api_key=api_key)


def generate_titles(highlights: list[dict], client) -> list[str]:
    """
    Return one short title per highlight, in the same order.
    Falls back to first-8-words on any error.
    """
    if not highlights:
        return []

    try:
        excerpts = [h["text"][:EXCERPT_LEN].strip() for h in highlights]
        numbered = "\n\n".join(f"{i + 1}. {t}" for i, t in enumerate(excerpts))

        prompt = (
            "You are building a personal knowledge base from book highlights. "
            "Write a concise 5-8 word title that captures the core idea of each highlight below. "
            "Return ONLY a numbered list — one title per line, nothing else.\n\n"
            + numbered
        )

        import time as _time
        response = None
        for attempt in range(3):
            try:
                response = client.models.generate_content(model=MODEL, contents=prompt)
                break
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    wait = 65
                    log.warning(f"Rate limited — waiting {wait}s before retry {attempt + 2}/3...")
                    _time.sleep(wait)
                else:
                    raise
        raw = response.text.strip()
        lines = [re.sub(r"^\d+\.\s*", "", l).strip() for l in raw.splitlines() if l.strip()]

        if len(lines) == len(highlights):
            return lines

        log.warning(f"Gemini returned {len(lines)} titles for {len(highlights)} highlights — using fallback")
    except Exception as e:
        log.warning(f"AI title generation failed: {e}")

    return [_first_words(h["text"]) for h in highlights]


def _first_words(text: str, n: int = 8) -> str:
    words = text.split()
    snippet = " ".join(words[:n])
    return snippet + ("…" if len(words) > n else "")
