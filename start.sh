#!/usr/bin/env sh
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
echo "Cascade Guard is starting at http://localhost:8000  (Ctrl+C to stop)"
exec python3 -m uvicorn app.api:app --port 8000
