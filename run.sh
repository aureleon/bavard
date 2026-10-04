#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv --prompt bavard
    ./.venv/bin/pip install --upgrade pip
fi

# (Re)install when requirements.txt changes
if [ ! -f ".venv/.installed" ] || [ requirements.txt -nt .venv/.installed ]; then
    echo "Installing dependencies..."
    ./.venv/bin/pip install -r requirements.txt
    touch .venv/.installed
fi

./.venv/bin/python tutor.py "$@"
