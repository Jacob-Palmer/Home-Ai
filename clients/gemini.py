"""Minimal Gemini client wrappers.

Requires environment variable GOOGLE_API_KEY or application default credentials configured on the host.
"""
from typing import Dict, Any
import os
import logging
import asyncio
import time

logger = logging.getLogger("clients.gemini")

try:
    from google import generativeai as genai
except Exception:  # pragma: no cover
    genai = None


async def probe_flash(prompt: str) -> str:
    """Async probe to Gemini Flash that enforces a single-token response.

    Behavior:
    - If Google SDK or API key is missing, use a fast heuristic fallback.
    - Otherwise call Gemini Flash (via SDK) and validate the reply is exactly
      one of the allowed tokens. Retry a small number of times if validation fails.
    - If all retries fail, fall back to the heuristic to avoid blocking routing.
    """
    ALLOWED = {"GEMINI", "OPENROUTER", "CLAUDE"}

    def heuristic(p: str) -> str:
        low = p.lower()
        if any(k in low for k in ("function", "def ", "class ", "onshape", "featurescript", "rest api", "json", "api")):
            return "OPENROUTER"
        if any(k in low for k in ("refactor", "prove", "formal", "algorithm", "optimize", "complex", "multi-file", "geometry")):
            return "CLAUDE"
        return "GEMINI"

    system = (
        "You are a gatekeeper analyzer whose only job is to classify the user's prompt into exactly one of three categories. Reply with exactly one UPPERCASE word and nothing else: GEMINI, OPENROUTER, or CLAUDE.\n\n"
        "Classification rules (do not explain):\n"
        "- GEMINI: general knowledge, summarization, long-context document processing, everyday tasks, or web-like lookups.\n"
        "- OPENROUTER: code generation, programming help, boilerplate, standard debugging, or concise API/JSON generation.\n"
        "- CLAUDE: complex algorithm design, large-scale refactoring across multiple files, formal proofs, deep geometry/Onshape FeatureScript reasoning, or anything requiring heavy cognitive work.\n"
        "Return ONLY the single token exact-match one of: GEMINI, OPENROUTER, CLAUDE — no punctuation, no explanation, no extra text."
    )

    if genai is None or os.getenv("GOOGLE_API_KEY") is None:
        return heuristic(prompt)

    # Run blocking SDK call in a thread to avoid blocking the event loop
    async def call_once():
        return await asyncio.to_thread(
            lambda: genai.chat.create(model=os.getenv("GEMINI_PROBE_MODEL", "gemini-1.5-flash"), messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}], max_output_tokens=8)
        )

    # Try up to 2 times with a short delay if the model responds improperly
    for attempt in range(2):
        try:
            resp = await call_once()
            text = getattr(resp, "last", None) or getattr(resp, "output_text", None) or (resp and resp.get("output", {}).get("content", "")) or ""
            token = (text or "").strip().upper()
            # Extract the first token (in case model returns extra whitespace)
            token = token.split()[0] if token else ""
            if token in ALLOWED:
                return token
            logger.warning("Gemini probe returned invalid token '%s' (attempt %d)", token, attempt + 1)
        except Exception:
            logger.exception("Gemini probe attempt failed")

        # small backoff
        await asyncio.sleep(0.4 * (attempt + 1))

    # Final fallback to heuristic
    logger.warning("Gemini probe failed validation after retries; using heuristic fallback")
    return heuristic(prompt)


async def call_gemini(prompt: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Async call to Gemini Pro/1.5. Wraps blocking SDK calls in a thread.

    Returns a dict containing `text` and `raw`.
    """
    if genai is None or os.getenv("GOOGLE_API_KEY") is None:
        logger.warning("Google generative client not configured; returning echo fallback")
        return {"text": f"(GEMINI-FALLBACK) {prompt[:400]}"}

    genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))

    def do_call():
        return genai.chat.create(model=os.getenv("GEMINI_MODEL", "gemini-1.5-pro"), messages=[{"role": "user", "content": prompt}], max_output_tokens=2048)

    resp = await asyncio.to_thread(do_call)
    text = getattr(resp, "last", None) or getattr(resp, "output_text", None) or ""
    return {"text": text, "raw": resp}
