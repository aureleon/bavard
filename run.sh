#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv --prompt bavard
    ./.venv/bin/pip install --upgrade pip
fi

# (Re)install when a requirements file changes. Kyutai (moshi_mlx) goes in
# with --no-deps: it pins an MLX version that would break Gemma 4.
if [ ! -f ".venv/.installed" ] || [ requirements.txt -nt .venv/.installed ] \
        || [ requirements-kyutai.txt -nt .venv/.installed ]; then
    echo "Installing dependencies..."
    ./.venv/bin/pip install -r requirements.txt
    ./.venv/bin/pip install --no-deps -r requirements-kyutai.txt
    touch .venv/.installed
fi

./.venv/bin/python tutor.py "$@"
