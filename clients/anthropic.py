"""Anthropic Claude client wrapper (minimal).

Requires ANTHROPIC_API_KEY and ANTHROPIC_ALLOW=true to execute.
"""
import os
import logging
import asyncio
from typing import Dict, Any

logger = logging.getLogger("clients.anthropic")

try:
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None


async def call_claude(prompt: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    if os.getenv("ANTHROPIC_ALLOW", "false").lower() != "true":
        raise RuntimeError("Anthropic calls are disabled by server policy")

    if anthropic is None or os.getenv("ANTHROPIC_API_KEY") is None:
        logger.warning("Anthropic SDK or API key missing; returning fallback message")
        return {"text": "(CLAUDE-FALLBACK) Anthropic not configured."}

    def do_call():
        client = anthropic.Client(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        return client.completions.create(model=os.getenv("ANTHROPIC_MODEL", "claude-3.5-sonnet"), prompt=prompt, max_tokens_to_sample=2048)

    resp = await asyncio.to_thread(do_call)
    return {"text": getattr(resp, "completion", None) or resp.get("completion") if isinstance(resp, dict) else getattr(resp, "completion", ""), "raw": resp}

