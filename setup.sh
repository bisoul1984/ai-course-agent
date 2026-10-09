#!/usr/bin/env bash
# One-time setup for Linux / Mac:  bash setup.sh
set -e
cd "$(dirname "$0")"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
[ -f .env ] || cp .env.example .env
echo "Setup finished. Add keys to .env, CV to documents/cv.pdf, then: bash run.sh check && bash run.sh auto"
