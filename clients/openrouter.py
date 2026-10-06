"""OpenRouter client wrapper using the public OpenRouter API.

Requires OPENROUTER_API_KEY in environment on Render.
"""
import os
import requests
import logging
from typing import Dict, Any

logger = logging.getLogger("clients.openrouter")


async def call_openrouter(prompt: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Call OpenRouter chat completions endpoint (sync HTTP inside async function).

    Uses environment variables OPENROUTER_API_KEY and OPENROUTER_MODEL.
    """
    url = os.getenv("OPENROUTER_URL", "https://api.openrouter.ai/v1/chat/completions")
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_MODEL", "qwen-2.5-coder")

    if not api_key:
        logger.warning("OPENROUTER_API_KEY not set; returning fallback")
        return {"text": "(OPENROUTER-FALLBACK) API key not configured."}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.getenv("OPENROUTER_MAX_TOKENS", "1024")),
    }

    try:
        # Keep request synchronous for simplicity; Render supports this pattern.
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.exception("OpenRouter call failed")
        return {"error": str(e)}
