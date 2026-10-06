from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()

logger = logging.getLogger("home_ai_gatekeeper")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Home AI Gatekeeper")
DB_PATH = os.path.join(os.path.dirname(__file__), "custom_functions.db")


def _init_function_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_functions (
            name TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            inputs TEXT NOT NULL,
            return_value TEXT NOT NULL,
            code TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


def _load_custom_functions_from_db() -> Dict[str, Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT name, description, inputs, return_value, code FROM custom_functions ORDER BY name"
    ).fetchall()
    conn.close()

    functions: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        namespace: Dict[str, Any] = {}
        try:
            exec(row["code"], namespace)
        except Exception as exc:
            logger.exception("Failed to reload custom function %s from storage: %s", row["name"], exc)
            continue

        fn = namespace.get(row["name"])
        if callable(fn):
            functions[row["name"]] = {
                "name": row["name"],
                "description": row["description"],
                "inputs": row["inputs"],
                "return_value": row["return_value"],
                "code": row["code"],
                "callable": fn,
            }
    return functions


def _persist_custom_function(data: Dict[str, Any]) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO custom_functions (name, description, inputs, return_value, code)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            description = excluded.description,
            inputs = excluded.inputs,
            return_value = excluded.return_value,
            code = excluded.code
        """,
        (
            data["name"],
            data["description"],
            data["inputs"],
            data["return_value"],
            data["code"],
        ),
    )
    conn.commit()
    conn.close()


_init_function_db()


class RouteRequest(BaseModel):
    prompt: str
    metadata: Optional[Dict[str, Any]] = None


class RouteResponse(BaseModel):
    backend: str
    response: Dict[str, Any]


CUSTOM_FUNCTIONS: Dict[str, Dict[str, Any]] = _load_custom_functions_from_db()
CUSTOM_FUNCTION_PLANS: Dict[str, Dict[str, Any]] = {}


class FunctionPlanRequest(BaseModel):
    goal: str
    context: Optional[str] = None


class FunctionCreateRequest(BaseModel):
    session_id: str
    goal: str
    answers: Dict[str, str]


class FunctionExecuteRequest(BaseModel):
    function_name: str
    arguments: Dict[str, Any]


def _bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_decision(raw: str) -> str:
    cleaned = (raw or "").strip().upper()
    if not cleaned:
        return ""
    token = cleaned.replace(",", "").replace(".", "").replace("!", "").replace("?", "").split()[0]
    return token


def _fallback_gatekeeper(prompt: str) -> str:
    low = prompt.lower()
    if any(word in low for word in [
        "function", "def ", "class ", "bug", "fix", "code", "python", "javascript",
        "json", "api", "feature", "onshape", "featurescript", "rest api"
    ]):
        return "OPENROUTER"
    if any(word in low for word in [
        "refactor", "algorithm", "prove", "formal", "geometry", "multi-file",
        "complex", "optimize", "cad", "mathematics", "hard logic"
    ]):
        return "CLAUDE"
    return "GEMINI"


async def gatekeeper_decision(prompt: str) -> str:
    system_instruction = (
        "You are a strict routing gatekeeper. Reply with exactly ONE word only and nothing else. "
        "Allowed words: GEMINI, OPENROUTER, CLAUDE. "
        "Use GEMINI for daily life tasks, summaries, brainstorming, general knowledge, and large-context reading. "
        "Use OPENROUTER for standard coding, bug fixing, boilerplate generation, and software engineering tasks. "
        "Use CLAUDE only for highly complex multi-file refactoring, advanced algorithmic reasoning, deep geometry, or difficult CAD engineering logic. "
        "Do NOT explain, do NOT add punctuation, do NOT include markdown, and do NOT output anything besides the single word."
    )

    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_FLASH_MODEL", "gemini-2.0-flash")

    if not api_key:
        logger.warning("GEMINI_API_KEY missing; using heuristic fallback")
        return _fallback_gatekeeper(prompt)

    try:
        try:
            from google import genai as google_genai
            client = google_genai.Client(api_key=api_key)

            def _call() -> str:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[{"role": "user", "content": prompt}],
                    config={
                        "system_instruction": system_instruction,
                        "temperature": 0.0,
                        "max_output_tokens": 16,
                    },
                )
                text = getattr(response, "text", None)
                if text:
                    return str(text)
                candidates = getattr(response, "candidates", None) or []
                if candidates:
                    content = getattr(candidates[0], "content", None)
                    parts = getattr(content, "parts", None) or []
                    if parts:
                        first = getattr(parts[0], "text", None)
                        if first:
                            return str(first)
                return ""

            decision = await asyncio.to_thread(_call)
            decision = _sanitize_decision(decision)
            if decision in {"GEMINI", "OPENROUTER", "CLAUDE"}:
                return decision
        except Exception:
            logger.exception("Primary Google GenAI SDK failed; trying legacy SDK")

        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)

            def _legacy_call() -> str:
                model = genai.GenerativeModel(
                    model_name=model_name,
                    system_instruction=system_instruction,
                )
                response = model.generate_content(
                    prompt,
                    generation_config={"temperature": 0.0, "max_output_tokens": 16},
                )
                text = getattr(response, "text", None)
                if text:
                    return str(text)
                candidates = getattr(response, "candidates", None) or []
                if candidates:
                    first = candidates[0]
                    parts = getattr(first, "content", None)
                    try:
                        if parts is not None:
                            return str(parts.parts[0].text)
                    except Exception:
                        pass
                return ""

            decision = await asyncio.to_thread(_legacy_call)
            decision = _sanitize_decision(decision)
            if decision in {"GEMINI", "OPENROUTER", "CLAUDE"}:
                return decision
        except Exception:
            logger.exception("Legacy Google SDK failed")
    except Exception as exc:
        logger.warning("Gemini gatekeeper call failed; using fallback: %s", exc)

    return _fallback_gatekeeper(prompt)


async def call_gemini(prompt: str) -> Dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    if not api_key:
        return {"text": "(GEMINI-FALLBACK) GEMINI_API_KEY not configured."}

    system_prompt = "You are a helpful assistant. Answer clearly, accurately, and concisely."

    try:
        try:
            from google import genai as google_genai
            client = google_genai.Client(api_key=api_key)

            def _call() -> Any:
                return client.models.generate_content(
                    model=model_name,
                    contents=[{"role": "user", "content": prompt}],
                    config={
                        "system_instruction": system_prompt,
                        "temperature": 0.3,
                        "max_output_tokens": 2048,
                    },
                )

            result = await asyncio.to_thread(_call)
            text = getattr(result, "text", None)
            if not text:
                candidates = getattr(result, "candidates", None) or []
                if candidates:
                    content = getattr(candidates[0], "content", None)
                    parts = getattr(content, "parts", None) or []
                    if parts:
                        text = getattr(parts[0], "text", None)
            if text:
                return {"text": str(text), "backend": "GEMINI", "raw": result}
            return {"text": "No response returned by Gemini.", "backend": "GEMINI"}
        except Exception:
            logger.exception("New Gemini SDK failed; trying legacy SDK")

        import google.generativeai as genai
        genai.configure(api_key=api_key)

        def _legacy_call() -> Any:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_prompt,
            )
            return model.generate_content(
                prompt,
                generation_config={"temperature": 0.3, "max_output_tokens": 2048},
            )

        result = await asyncio.to_thread(_legacy_call)
        text = getattr(result, "text", None)
        if text:
            return {"text": str(text), "backend": "GEMINI", "raw": result}
        return {"text": "No response returned by Gemini.", "backend": "GEMINI"}
    except Exception as exc:
        logger.exception("Gemini call failed")
        return {"text": f"(GEMINI-FALLBACK) {prompt[:400]}", "backend": "GEMINI", "error": str(exc)}


async def call_openrouter(prompt: str) -> Dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return {
            "text": "OPENROUTER_API_KEY is not set. Add it in Render > Environment Variables to enable OpenRouter routing.",
            "backend": "OPENROUTER",
            "needs_api_key": "OPENROUTER_API_KEY",
            "status": "missing_api_key",
        }

    url = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
    model = os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-coder-32b-instruct:free")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("APP_URL", "https://example.com"),
        "X-Title": "Home AI Gatekeeper",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.getenv("OPENROUTER_MAX_TOKENS", "2048")),
        "temperature": 0.3,
    }

    def _post() -> requests.Response:
        return requests.post(url, headers=headers, json=payload, timeout=90)

    try:
        response = await asyncio.to_thread(_post)
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return {"text": "No response returned by OpenRouter.", "backend": "OPENROUTER", "raw": data}
        message = choices[0].get("message", {})
        content = message.get("content", "")
        return {"text": content, "backend": "OPENROUTER", "raw": data}
    except Exception as exc:
        logger.exception("OpenRouter call failed")
        return {"text": f"OPENROUTER request failed. Check OPENROUTER_API_KEY and model access. Details: {exc}", "backend": "OPENROUTER", "error": str(exc)}


async def call_claude(prompt: str) -> Dict[str, Any]:
    if not _bool_env("ANTHROPIC_ALLOW", False):
        return {
            "text": "Anthropic is disabled. Set ANTHROPIC_ALLOW=true in Render to enable Claude, and add ANTHROPIC_API_KEY.",
            "backend": "CLAUDE",
            "needs_api_key": "ANTHROPIC_API_KEY",
            "status": "disabled_or_missing",
        }

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "text": "ANTHROPIC_API_KEY is not set. Add it in Render > Environment Variables to enable Claude routing.",
            "backend": "CLAUDE",
            "needs_api_key": "ANTHROPIC_API_KEY",
            "status": "missing_api_key",
        }

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        def _call() -> Any:
            return client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
                max_tokens=2048,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
                system="You are a careful, precise assistant. Answer clearly and directly.",
            )

        response = await asyncio.to_thread(_call)
        content_blocks = getattr(response, "content", []) or []
        if content_blocks:
            first_block = content_blocks[0]
            text = getattr(first_block, "text", None)
            if text:
                return {"text": str(text), "backend": "CLAUDE", "raw": response}
        return {"text": "No response returned by Claude.", "backend": "CLAUDE", "raw": response}
    except Exception as exc:
        logger.exception("Claude call failed")
        return {"text": f"Claude request failed. Check ANTHROPIC_API_KEY and model access. Details: {exc}", "backend": "CLAUDE", "error": str(exc)}


async def list_google_drive_files() -> Dict[str, Any]:
    json_string = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON") or os.getenv("GOOGLE_DRIVE_CREDENTIALS_JSON")
    file_path = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE")

    if not json_string and not file_path:
        return {
            "status": "not_configured",
            "message": "Google Drive is not configured. Add GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON or GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE.",
            "files": [],
        }

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        if json_string:
            creds = service_account.Credentials.from_service_account_info(json.loads(json_string))
        else:
            creds = service_account.Credentials.from_service_account_file(file_path)

        scoped = creds.with_scopes(["https://www.googleapis.com/auth/drive.readonly"])
        service = build("drive", "v3", credentials=scoped, cache_discovery=False)
        folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

        if folder_id:
            query = f"' {folder_id} ' in parents and trashed=false"
        else:
            query = "trashed=false"

        results = service.files().list(
            q=query,
            pageSize=20,
            fields="files(id, name, mimeType, webViewLink, modifiedTime)",
            orderBy="modifiedTime desc",
        ).execute()

        files = results.get("files", [])
        return {
            "status": "connected",
            "message": "Google Drive connected successfully.",
            "files": files,
        }
    except Exception as exc:
        logger.exception("Google Drive access failed")
        return {
            "status": "error",
            "message": f"Google Drive could not be accessed: {exc}",
            "files": [],
        }


def _default_function_questions(goal: str) -> list[str]:
    return [
        "What should this function be called?",
        "What should it do in plain English?",
        "What inputs should it accept? (example: name, amount, url)",
        "What should it return? (example: string, dict, bool, file path)",
        "What edge cases or validation should it handle?",
        "Should I add a Python docstring and a safe default fallback?",
    ]


def _normalize_function_name(name: str) -> str:
    cleaned = (name or "custom_function").strip().replace("-", "_").replace(" ", "_")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch == "_")
    if not cleaned:
        cleaned = "custom_function"
    if cleaned[0].isdigit():
        cleaned = f"func_{cleaned}"
    return cleaned


def _build_custom_function_code(goal: str, answers: Dict[str, str]) -> str:
    func_name = _normalize_function_name(answers.get("function_name") or goal.split()[0])
    description = (answers.get("description") or goal).strip()
    inputs = (answers.get("inputs") or "value: str").strip()
    validation = (answers.get("validation") or "Validate the input and raise a ValueError if the required data is missing.").strip()

    if not inputs:
        inputs = "value: str"

    function_code = (
        f"def {func_name}({inputs}):\n"
        f"    \"\"\"{description}\n\n"
        f"    Generated from a custom AI function request.\n"
        f"    \"\"\"\n"
        f"    {validation}\n"
        f"    result = {{\n"
        f"        'name': '{func_name}',\n"
        f"        'description': {description!r},\n"
        f"        'input': locals().copy(),\n"
        f"        'status': 'completed'\n"
        f"    }}\n"
        f"    return result\n"
    )
    return function_code


async def _generate_custom_function(goal: str, answers: Dict[str, str]) -> Dict[str, Any]:
    code = _build_custom_function_code(goal, answers)
    namespace: Dict[str, Any] = {}
    try:
        exec(code, namespace)
    except Exception as exc:
        logger.exception("Custom function code execution failed")
        return {
            "status": "error",
            "message": f"Generated code could not be executed: {exc}",
            "code": code,
        }

    fn_name = _normalize_function_name(answers.get("function_name") or goal.split()[0])
    function_obj = namespace.get(fn_name)
    if function_obj is None:
        return {
            "status": "error",
            "message": "The generated function could not be registered.",
            "code": code,
        }

    CUSTOM_FUNCTIONS[fn_name] = {
        "name": fn_name,
        "description": (answers.get("description") or goal).strip(),
        "inputs": answers.get("inputs") or "value: str",
        "return_value": answers.get("return_value") or "dict",
        "code": code,
        "callable": function_obj,
    }
    _persist_custom_function(CUSTOM_FUNCTIONS[fn_name])

    return {
        "status": "created",
        "name": fn_name,
        "description": CUSTOM_FUNCTIONS[fn_name]["description"],
        "code": code,
        "message": f"Function '{fn_name}' was added to your agent capabilities.",
    }


async def _plan_custom_function(goal: str) -> Dict[str, Any]:
    session_id = f"func_{abs(hash(goal + str(asyncio.get_running_loop().time())))[:12]}"
    CUSTOM_FUNCTION_PLANS[session_id] = {
        "goal": goal,
        "answers": {},
        "questions": _default_function_questions(goal),
    }
    return {"session_id": session_id, "questions": CUSTOM_FUNCTION_PLANS[session_id]["questions"]}


@app.get("/functions")
async def list_functions() -> Dict[str, Any]:
    return {
        "status": "ok",
        "functions": [{
            "name": name,
            "description": data["description"],
            "inputs": data["inputs"],
            "return_value": data["return_value"],
        } for name, data in sorted(CUSTOM_FUNCTIONS.items())],
    }


@app.post("/functions/plan")
async def plan_function(req: FunctionPlanRequest):
    if not req.goal or not req.goal.strip():
        raise HTTPException(status_code=400, detail="A function goal is required.")
    return await _plan_custom_function(req.goal)


@app.post("/functions/create")
async def create_function(req: FunctionCreateRequest):
    if not req.session_id or req.session_id not in CUSTOM_FUNCTION_PLANS:
        raise HTTPException(status_code=400, detail="Invalid function session.")
    plan = CUSTOM_FUNCTION_PLANS[req.session_id]
    plan["answers"] = req.answers or {}
    outcome = await _generate_custom_function(req.goal, plan["answers"])
    if outcome.get("status") == "created":
        CUSTOM_FUNCTION_PLANS.pop(req.session_id, None)
    return outcome


@app.post("/functions/execute")
async def execute_function(req: FunctionExecuteRequest):
    if req.function_name not in CUSTOM_FUNCTIONS:
        raise HTTPException(status_code=404, detail=f"Function '{req.function_name}' is not registered.")
    fn = CUSTOM_FUNCTIONS[req.function_name]["callable"]
    try:
        if req.arguments is None:
            req.arguments = {}
        result = fn(**req.arguments)
        return {"status": "ok", "function_name": req.function_name, "result": result}
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid arguments for {req.function_name}: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Function execution failed: {exc}") from exc


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    return HTMLResponse("""
    <html>
      <head>
        <title>Home AI Jarvis</title>
        <style>
          body { font-family: Arial, sans-serif; background: linear-gradient(135deg, #081221, #0d223b); color: #eaf4ff; margin: 0; padding: 32px; }
          .card { max-width: 980px; margin: 0 auto; background: rgba(13, 18, 32, 0.92); border: 1px solid rgba(255,255,255,0.12); border-radius: 18px; padding: 28px; box-shadow: 0 20px 60px rgba(0,0,0,0.35); }
          h1 { margin-top: 0; letter-spacing: 0.08em; }
          .status { margin: 12px 0 24px; color: #9fe6ff; }
          .panel { display: grid; grid-template-columns: 1.5fr 1fr; gap: 24px; }
          .box { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 18px; }
          button { background: #4ec9ff; color: #07141d; border: none; padding: 12px 18px; border-radius: 10px; cursor: pointer; font-weight: 700; }
          button.secondary { background: #90a0b0; }
          textarea { width: 100%; min-height: 110px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.08); background: rgba(0,0,0,0.2); color: white; font-size: 16px; padding: 12px; }
          .files { list-style: none; padding: 0; margin: 0; }
          .files li { padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.08); }
          .tiny { font-size: 12px; opacity: 0.8; }
          @media (max-width: 760px) { .panel { grid-template-columns: 1fr; } }
        </style>
      </head>
      <body>
        <div class="card">
          <h1>JARVIS</h1>
          <div class="status">Voice assistant + connected file overview</div>
          <div class="panel">
            <div class="box">
              <textarea id="promptBox" placeholder="Ask me anything..."></textarea>
              <div style="margin-top:16px; display:flex; gap:12px; flex-wrap: wrap;">
                <button id="askBtn">Ask</button>
                <button id="addFunctionBtn">Add Function</button>
                <button id="micBtn" class="secondary">🎙 Listen</button>
              </div>
              <div id="responseBox" style="margin-top:18px; min-height: 80px; background: rgba(0,0,0,0.18); border-radius: 8px; padding: 12px; white-space: pre-wrap;">Waiting for a command...</div>
            </div>
            <div class="box">
              <h3>Connected files</h3>
              <ul class="files" id="fileList">
                <li>Loading Google Drive files...</li>
              </ul>
              <h3 style="margin-top: 24px;">Custom functions</h3>
              <ul class="files" id="functionList">
                <li>No custom functions yet.</li>
              </ul>
            </div>
          </div>
        </div>
        <script>
          const promptBox = document.getElementById('promptBox');
          const responseBox = document.getElementById('responseBox');
          const fileList = document.getElementById('fileList');
          const functionList = document.getElementById('functionList');

          function renderCustomFunctions(functions) {
            if (!Array.isArray(functions) || functions.length === 0) {
              functionList.innerHTML = '<li>No custom functions yet.</li>';
              return;
            }

            functionList.innerHTML = functions.map((fn) => {
              return `<li><strong>${fn.name}</strong><div class="tiny">${fn.description}</div><div class="tiny">Inputs: ${fn.inputs || 'none'}</div></li>`;
            }).join('');
          }

          async function refreshCustomFunctions() {
            try {
              const res = await fetch('/functions');
              const data = await res.json();
              renderCustomFunctions(data.functions || []);
            } catch (err) {
              functionList.innerHTML = '<li>Unable to load custom functions.</li>';
            }
          }

          function speak(text) {
            if (!('speechSynthesis' in window)) return;
            const utterance = new SpeechSynthesisUtterance(text);
            utterance.rate = 1.0;
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(utterance);
          }

          async function loadFiles() {
            try {
              const res = await fetch('/drive/files');
              const data = await res.json();
              if (data.status !== 'connected' || !Array.isArray(data.files) || data.files.length === 0) {
                fileList.innerHTML = '<li>' + (data.message || 'No connected files found.') + '</li>';
                return;
              }
              fileList.innerHTML = data.files.map(file => {
                const link = file.webViewLink ? `<a href="${file.webViewLink}" target="_blank" style="color:#9fe6ff;">Open</a>` : 'No link';
                return `<li><strong>${file.name}</strong><div class="tiny">${file.mimeType || 'file'} • ${link}</div></li>`;
              }).join('');
            } catch (err) {
              fileList.innerHTML = '<li>Unable to load drive files right now.</li>';
            }
          }

          async function sendPrompt(prompt) {
            responseBox.textContent = 'Thinking...';
            const res = await fetch('/route', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ prompt })
            });
            const data = await res.json();
            const text = data?.response?.result?.text || JSON.stringify(data, null, 2);
            responseBox.textContent = text;
            speak(text);
          }

          async function addFunctionWizard() {
            const goal = window.prompt('What should this new function do?');
            if (!goal || !goal.trim()) {
              responseBox.textContent = 'Function creation cancelled.';
              return;
            }

            responseBox.textContent = 'Planning your function...';

            const planRes = await fetch('/functions/plan', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ goal: goal.trim() })
            });

            const planData = await planRes.json();
            if (!planData.session_id) {
              responseBox.textContent = planData.detail || 'Unable to create function plan.';
              return;
            }

            const questionMap = [
              { key: 'function_name', question: 'What should this function be called?' },
              { key: 'description', question: 'What should it do in plain English?' },
              { key: 'inputs', question: 'What inputs should it accept? Example: name: str, amount: float' },
              { key: 'return_value', question: 'What should it return? Example: dict, str, bool' },
              { key: 'validation', question: 'What validation or edge cases should it handle?' },
              { key: 'docstring', question: 'Add a Python docstring and safe fallback? Reply yes or no.' }
            ];

            const answers = {};
            for (const item of questionMap) {
              const answer = window.prompt(item.question, '');
              if (answer === null) {
                responseBox.textContent = 'Function creation cancelled.';
                return;
              }
              answers[item.key] = answer.trim();
            }

            const createRes = await fetch('/functions/create', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                session_id: planData.session_id,
                goal: goal.trim(),
                answers
              })
            });

            const createData = await createRes.json();
            responseBox.textContent = createData.message || 'Function created.';
            speak(createData.message || 'Function created.');
            await refreshCustomFunctions();
          }

          document.getElementById('askBtn').addEventListener('click', () => {
            const prompt = promptBox.value.trim();
            if (!prompt) return;
            sendPrompt(prompt);
          });

          document.getElementById('addFunctionBtn').addEventListener('click', () => {
            addFunctionWizard();
          });

          document.getElementById('micBtn').addEventListener('click', () => {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {
              responseBox.textContent = 'Voice input is not supported in this browser.';
              return;
            }
            const recognition = new SpeechRecognition();
            recognition.lang = 'en-US';
            recognition.start();
            recognition.onresult = (event) => {
              const transcript = event.results[0][0].transcript;
              promptBox.value = transcript;
              sendPrompt(transcript);
            };
            recognition.onerror = () => {
              responseBox.textContent = 'Voice capture failed. Try typing instead.';
            };
          });

          loadFiles();
          refreshCustomFunctions();
        </script>
      </body>
    </html>
    """)

