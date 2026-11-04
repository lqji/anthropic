#!/usr/bin/env bash
# R1 demo evidence: restart the api container in the middle of a conversation and show that the
# next message continues it.
#
#   experiments/checkpoints/restart_demo.sh [base-url]     # default http://localhost:8000
#
# Run it from the repository root on a running stack (docker compose up --build -d --wait).
# Needs curl, jq and docker compose. It books one slot tomorrow with Dr. Salma Nasser for the
# patient "restart-demo", so use it on a demo database.
#
# Why this proves it: "book the first one" names no doctor, day or time. The api can only book
# the right slot if it still has the list it offered before the restart, and that list lives in
# the conversation's checkpoint (offered_slots), not in the api process.
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
API="${BASE_URL%/}/api/v1"

for tool in curl jq docker; do
  command -v "$tool" >/dev/null || { echo "FAIL: $tool is not installed" >&2; exit 1; }
done

say() { # say <conversation id> <text> -> prints the reply, fails on a non-200 answer
  local body
  body="$(curl -sS --fail-with-body -X POST "$API/conversations/$1/messages" \
    -H 'Content-Type: application/json' -d "$(jq -cn --arg t "$2" '{text: $t}')")"
  printf '%s' "$body" | jq -r '"  [\(.outcome)] \(.reply)"'
  OUTCOME="$(printf '%s' "$body" | jq -r .outcome)"
}

wait_healthy() {
  for _ in $(seq 1 60); do
    if curl -sf "$BASE_URL/health" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "FAIL: the api did not come back within 60 s" >&2
  exit 1
}

wait_healthy
CID="$(curl -sS --fail-with-body -X POST "$API/conversations" -H 'Content-Type: application/json' \
  -d '{"patient_ref":"restart-demo"}' | jq -r .id)"
echo "1. conversation $CID"

echo "2. patient: When is Dr. Salma free tomorrow?"
say "$CID" "When is Dr. Salma free tomorrow?"
[ "$OUTCOME" = "slots_listed" ] || { echo "FAIL: expected a slot list" >&2; exit 1; }

STARTED_BEFORE="$(docker compose ps -q api | xargs docker inspect -f '{{.State.StartedAt}}')"
echo "3. restarting the api container (docker compose restart api)"
docker compose restart api >/dev/null 2>&1
wait_healthy
STARTED_AFTER="$(docker compose ps -q api | xargs docker inspect -f '{{.State.StartedAt}}')"
[ "$STARTED_BEFORE" != "$STARTED_AFTER" ] || { echo "FAIL: the api did not restart" >&2; exit 1; }
echo "   api process started at $STARTED_BEFORE, now $STARTED_AFTER"

echo "4. patient: book the first one"
say "$CID" "book the first one"
[ "$OUTCOME" = "booked" ] || { echo "FAIL: expected a booking from the list shown before" >&2; exit 1; }

echo "5. the stored conversation:"
curl -sS "$API/conversations/$CID" | jq -r '.messages[] | "  \(.role): \(.text | split("\n")[0])"'
echo "OK: the conversation continued after the restart"
