#!/usr/bin/env bash
set -euo pipefail
# Setup Python virtualenv and install project dependencies (run as project user)

PROJECT_USER=aiuser
PROJECT_DIR=/opt/home-ai
VENV=${PROJECT_DIR}/venv

if [ ! -d "${PROJECT_DIR}" ]; then
  echo "Project directory ${PROJECT_DIR} does not exist. Create it and re-run as root or create as ${PROJECT_USER}."
  exit 1
fi

if [ ! -f "/usr/bin/python3" ]; then
  echo "python3 missing; install via apt before running this script."
  exit 1
fi

echo "Creating virtualenv (if missing)..."
python3 -m venv ${VENV}
chown -R ${PROJECT_USER}:${PROJECT_USER} ${VENV}

echo "Installing pip packages from requirements.txt..."
${VENV}/bin/pip install --upgrade pip
if [ -f "$(pwd)/requirements.txt" ]; then
  ${VENV}/bin/pip install -r $(pwd)/requirements.txt
else
  echo "requirements.txt not found in current directory. Copy it to the repo root and re-run."
fi

echo "Done. Activate virtualenv: source ${VENV}/bin/activate"
exit 0
