#!/usr/bin/env bash
# Usage: bash run.sh <command>   e.g. bash run.sh check | run | list
cd "$(dirname "$0")"
source venv/bin/activate
python main.py "${@:-run}"
