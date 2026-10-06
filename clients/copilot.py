"""Client for local Copilot OpenAI-compatible proxy.

Expects environment variable COPILOT_PROXY_URL (e.g. http://127.0.0.1:3000/v1/chat/completions)
"""
import os
import requests
import logging
from typing import Dict, Any

logger = logging.getLogger("clients.copilot")


def call_copilot(prompt: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    url = os.getenv("COPILOT_PROXY_URL", "http://127.0.0.1:3000/v1/chat/completions")
    headers = {"Content-Type": "application/json"}
    body = {
        "model": os.getenv("COPILOT_MODEL", "gpt-4o-mini-code"),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.getenv("COPILOT_MAX_TOKENS", "1024")),
    }

    try:
        r = requests.post(url, json=body, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.exception("Copilot proxy call failed")
        return {"error": str(e)}
