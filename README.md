# Home AI — OCI Gatekeeper Router

This repo contains helper scripts to provision and harden an Ubuntu OCI A1.Flex instance and a minimal Python environment for the 3-tier gatekeeper router.

Quick steps

1. Upload your SSH public key to the instance and SSH in as `opc` or the cloud user.
2. Copy `scripts/provision_oci_harden.sh` to the server and run:

```bash
sudo bash provision_oci_harden.sh
```

3. Add your SSH public key to `/home/aiuser/.ssh/authorized_keys` (replace `aiuser` value if changed).
4. Copy the repo to `/opt/home-ai` (or clone it), then run (as root):

```bash
sudo mkdir -p /opt/home-ai
sudo chown aiuser:aiuser /opt/home-ai
sudo -u aiuser git clone <your-repo> /opt/home-ai
sudo -u aiuser bash scripts/setup_python_env.sh
```

5. Edit `/etc/systemd/system/gatekeeper.service` to point to your FastAPI `main:app` module and then enable it:

```bash
sudo systemctl enable --now gatekeeper.service
```

Next steps

- Install and run the local Copilot proxy on the instance (we'll add install steps next).
- Scaffold the FastAPI router and Telegram integration (I can scaffold this now).

Render Deployment (zero-maintenance, free tier)
---------------------------------------------

This section walks you through deploying the FastAPI app to Render's Free Web Service (no credit card). It includes exact environment variable keys/values you should paste into Render, and where to set build/start commands.

Prerequisites
- A GitHub repository containing this project.
- A Render account (Free tier) and access to the GitHub repo.

Step-by-step (with screenshot guidance)

1) Create a new Web Service on Render
- In Render dashboard click **New** → **Web Service**. (Screenshot: `screenshots/01-new-web-service.png` — show the New menu and Web Service option.)

2) Connect your GitHub repository
- Choose the repo and branch (usually `main` or `master`). (Screenshot: `screenshots/02-select-repo.png` — show repo selection screen.)

3) Configure the service
- Name: `home-ai-gatekeeper` (or any name you prefer)
- Region: choose the nearest region (defaults are fine)
- Branch: `main`
- Runtime: Python 3.11 (or 3.10/3.12 if available)
- Plan: Free (select free tier)
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn Main:app --host 0.0.0.0 --port $PORT`

(Screenshot: `screenshots/03-configure-service.png` — show the form with Build & Start commands filled.)

4) Add Environment Variables (render dashboard → Environment)
Paste the following key/value pairs into Render's Environment → Environment Variables section.

Exact env vars (copy/paste to your notes; enter each into Render UI):

GOOGLE_API_KEY=your-google-api-key
GEMINI_PROBE_MODEL=gemini-1.5-flash
GEMINI_MODEL=gemini-1.5-pro

OPENROUTER_API_KEY=your-openrouter-api-key
OPENROUTER_MODEL=qwen-2.5-coder
OPENROUTER_URL=https://api.openrouter.ai/v1/chat/completions

ANTHROPIC_API_KEY=your-anthropic-api-key
ANTHROPIC_MODEL=claude-3.5-sonnet
ANTHROPIC_ALLOW=false

PORT=$PORT   # Render injects this automatically; you do not need to set it manually

(Screenshot: `screenshots/04-env-vars.png` — show environment variables UI with keys filled except secrets.)

Notes on secrets
- `GOOGLE_API_KEY`: required if you want Gemini probing and full Gemini responses. If you prefer using Google ADC (service account JSON), you must upload the JSON to Render as a file and set `GOOGLE_APPLICATION_CREDENTIALS` to its path — Render handles file secrets differently; see Render docs.
- `OPENROUTER_API_KEY`: obtain from OpenRouter dashboard. Choose a free model such as `qwen-2.5-coder` or any other free coding model that OpenRouter exposes.
- `ANTHROPIC_API_KEY` and `ANTHROPIC_ALLOW`: leave `ANTHROPIC_ALLOW=false` unless you want to enable Claude. Setting `true` will allow the app to call Anthropic and may incur costs.

5) Deploy
- Click **Create Web Service** (or **Save**). Render will build and deploy your app. The service logs will show `uvicorn` starting and the public HTTPS URL once ready. (Screenshot: `screenshots/05-deploy-logs.png`)

6) Test the service (once deployed)
- Health check:

```bash
curl -sS https://<your-render-url>/health
```

- Route test example:

```bash
curl -sS -X POST https://<your-render-url>/route \
	-H "Content-Type: application/json" \
	-d '{"prompt":"Write a Python function to reverse a linked list"}'
```

If Gemini is configured, the probe will run first and return one of `GEMINI`, `OPENROUTER`, or `CLAUDE`, then the app will forward the original prompt to the selected backend.

Adding screenshots to the repo (optional)
- If you want the README to display screenshots in the repo, create a `screenshots/` folder at the repo root and upload the images named as suggested above. Then add inline markdown images, for example:

```md
![Select repo](screenshots/02-select-repo.png)
```

Troubleshooting
- If the probe sometimes returns unexpected text, ensure `GOOGLE_API_KEY` is correct and that the `GEMINI_PROBE_MODEL` is set to `gemini-1.5-flash`.
- If your app times out on OpenRouter calls, increase `OPENROUTER_MAX_TOKENS` (environment variable) and ensure the model you selected is available on OpenRouter free tier.
- Keep `ANTHROPIC_ALLOW=false` until you're ready to pay for Claude calls; the app enforces this guard.

Support & next steps
- I can add: a small test harness that posts example prompts to each intended backend, a Telegram bot webhook endpoint, or a simple UI to exercise routing manually.

