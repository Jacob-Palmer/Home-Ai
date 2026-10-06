from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import os
import logging
from dotenv import load_dotenv

load_dotenv()

from clients import gemini, openrouter, anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gatekeeper")

app = FastAPI(title="Home AI Gatekeeper")


class RouteRequest(BaseModel):
	prompt: str
	metadata: Optional[Dict[str, Any]] = None


class RouteResponse(BaseModel):
	backend: str
	response: Dict[str, Any]


@app.get("/health")
async def health():
	return {"status": "ok"}


@app.post("/route", response_model=RouteResponse)
async def route(req: RouteRequest):
	prompt = req.prompt
	logger.info("Received prompt (len=%d)", len(prompt))

	# 1) Probe with Gemini Flash to decide routing
	try:
		decision = await gemini.probe_flash(prompt)
	except Exception as e:
		logger.exception("Gemini probe failed")
		raise HTTPException(status_code=502, detail=f"Gatekeeper probe failed: {e}")

	decision = (decision or "").strip().upper()
	logger.info("Gatekeeper decision: %s", decision)

	if decision == "GEMINI":
		resp = await gemini.call_gemini(prompt, metadata=req.metadata or {})
		backend = "GEMINI"
	elif decision == "OPENROUTER":
		resp = await openrouter.call_openrouter(prompt, metadata=req.metadata or {})
		backend = "OPENROUTER"
	elif decision == "CLAUDE":
		if os.getenv("ANTHROPIC_ALLOW", "false").lower() != "true":
			raise HTTPException(status_code=403, detail="Anthropic calls disabled by server policy")
		resp = await anthropic.call_claude(prompt, metadata=req.metadata or {})
		backend = "CLAUDE"
	else:
		raise HTTPException(status_code=400, detail=f"Unknown gatekeeper decision: {decision}")

	return RouteResponse(backend=backend, response={"result": resp})

