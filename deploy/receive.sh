#!/bin/bash
# Forced command for the CI deploy key.
# Reads a gzipped tar of the repo from stdin, installs it, and restarts the app.
set -euo pipefail

home=/opt/race-planner
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
# rsync -a copies the mode of the stage root to the app dir, and mktemp -d makes it 700.
chmod 750 "$stage"

tar -xz -C "$stage"

rsync -a --delete --exclude /data/ --exclude __pycache__/ "$stage"/ "$home"/app/

/usr/local/bin/uv pip install -q --python "$home"/.venv/bin/python -r "$home"/app/requirements.txt

# The restart waits for the migrations in ExecStartPre, then uvicorn needs a few seconds to import.
if ! sudo -n /usr/bin/systemctl restart race-planner \
  || ! curl -fsS -o /dev/null --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:8000/login; then
  systemctl status race-planner --no-pager || true
  exit 1
fi
echo "race-planner deployed and answering"
