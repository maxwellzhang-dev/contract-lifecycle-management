#!/bin/zsh
set -e

PROJECT_DIR="${0:A:h}"
NODE22_BIN="/opt/homebrew/opt/node@22/bin"

if [[ -d "$NODE22_BIN" ]]; then
  export PATH="$NODE22_BIN:$PATH"
fi

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  echo "Missing $PROJECT_DIR/.env. AI extraction will use the local fallback until Azure credentials are configured."
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/uvicorn" ]]; then
  echo "Backend dependencies are not installed. See README.md."
  exit 1
fi

if [[ ! -d "$PROJECT_DIR/frontend/node_modules" ]]; then
  echo "Frontend dependencies are not installed. See README.md."
  exit 1
fi

cd "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/uvicorn" backend.app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

cd "$PROJECT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Clause is starting: http://localhost:3000"
wait
