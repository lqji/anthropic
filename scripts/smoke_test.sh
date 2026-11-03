#!/usr/bin/env bash
# Smoke test for a running Clinic Front-Desk Assistant: one pass through every feature.
#
#   scripts/smoke_test.sh [base-url]        # default http://localhost:8000
#
# Needs curl and jq. Stops at the first failing check, prints its name and what was wrong, and
# exits 1. Exits 0 only if every check passed.
#
# It uses its own patient_ref, so it never touches real patients. It books a slot on a later day
# and cancels it again, so the slot is free afterwards; the emergency flag it creates stays in
# the staff list. The booking steps need a free slot on one of the next 4 days: the demo slots
# are seeded once for 5 days, so on an old database run `docker compose down -v` first.
set -u -o pipefail

BASE_URL="${1:-${BASE_URL:-http://localhost:8000}}"
BASE_URL="${BASE_URL%/}"
CLINIC_TIMEZONE="${CLINIC_TIMEZONE:-Asia/Muscat}"
TIMEOUT_S=30

for tool in curl jq; do
  command -v "$tool" >/dev/null 2>&1 || { echo "FAIL setup: $tool is not installed" >&2; exit 1; }
done

BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT
STATUS=""
BODY=""
CHECKS=0

fail() { # fail <check> <what was wrong>
  echo "FAIL $1: $2" >&2
  [ -n "$BODY" ] && echo "  last response ($STATUS): $(printf '%s' "$BODY" | head -c 400)" >&2
  echo "Smoke test failed at check $((CHECKS + 1)): $1" >&2
  exit 1
}

pass() { CHECKS=$((CHECKS + 1)); echo "ok   $1"; }

# call <method> <path> [json body] -> sets STATUS and BODY. A connection failure gives 000.
call() {
  local method="$1" path="$2" data="${3:-}"
  if [ -n "$data" ]; then
    STATUS="$(curl -sS -o "$BODY_FILE" -w '%{http_code}' --max-time "$TIMEOUT_S" \
      -X "$method" -H 'Content-Type: application/json' -d "$data" "$BASE_URL$path" 2>/dev/null)" || STATUS="000"
  else
    STATUS="$(curl -sS -o "$BODY_FILE" -w '%{http_code}' --max-time "$TIMEOUT_S" \
      -X "$method" "$BASE_URL$path" 2>/dev/null)" || STATUS="000"
  fi
  BODY="$(cat "$BODY_FILE")"
}

expect_status() { # expect_status <check> <status>
  [ "$STATUS" = "$2" ] || fail "$1" "expected HTTP $2 but got $STATUS ($BASE_URL)"
}

field() { printf '%s' "$BODY" | jq -r "$1" 2>/dev/null; }

expect_field() { # expect_field <check> <jq filter> <expected value>
  local actual
  actual="$(field "$2")"
  [ "$actual" = "$3" ] || fail "$1" "$2 is '$actual', expected '$3'"
}

say() { # say <conversation id> <text> -> sets STATUS and BODY
  call POST "/api/v1/conversations/$CONVERSATION/messages" "$(jq -nc --arg text "$1" '{text: $text}')"
}

day_in_clinic() { # day_in_clinic <days from today> -> YYYY-MM-DD in clinic time (GNU or BSD date)
  TZ="$CLINIC_TIMEZONE" date -d "+$1 day" +%F 2>/dev/null || TZ="$CLINIC_TIMEZONE" date -v+"$1"d +%F
}

# 1. The service and its database are up.
call GET /health
expect_status "health" 200
expect_field "health" '.status' ok
expect_field "health" '.database' ok
pass "health: service and database are up"

# 2. The demo data is there, and there is a free slot on a later day to book.
call GET /api/v1/doctors
expect_status "doctors" 200
[ "$(field 'length')" -gt 0 ] 2>/dev/null || fail "doctors" "no doctors (is the database seeded?)"
DOCTOR_ID="$(field '.[0].id')"
DOCTOR_NAME="$(field '.[0].name')"
pass "doctors: $DOCTOR_NAME and others are listed"

SLOT_DAY=""
for offset in 1 2 3 4; do
  day="$(day_in_clinic "$offset")"
  call GET "/api/v1/slots?date=$day&doctor_id=$DOCTOR_ID"
  expect_status "free slots" 200
  if [ "$(field 'length')" -gt 0 ]; then SLOT_DAY="$day"; break; fi
done
[ -n "$SLOT_DAY" ] || fail "free slots" "no free slot for $DOCTOR_NAME on the next 4 days (reset the demo data: docker compose down -v)"
pass "free slots: $DOCTOR_NAME has slots on $SLOT_DAY"

# 3. A conversation.
PATIENT="smoke-test-$(date +%s)-$$"
call POST /api/v1/conversations "$(jq -nc --arg p "$PATIENT" '{patient_ref: $p}')"
expect_status "start conversation" 201
CONVERSATION="$(field '.id')"
[ -n "$CONVERSATION" ] && [ "$CONVERSATION" != null ] || fail "start conversation" "no id in the response"
pass "conversation: started for $PATIENT"

# 4. A clinic question is answered from the documents and names its source.
say "How should I prepare for my visit?"
expect_status "clinic question" 200
expect_field "clinic question" '.outcome' answered
[ "$(field '.sources | length')" -gt 0 ] 2>/dev/null || fail "clinic question" "the answer names no source"
case "$(field '.reply')" in *"Source:"*) ;; *) fail "clinic question" "the reply does not end with its source" ;; esac
pass "clinic question: answered from $(field '.sources[0].document')"

# 5. Something the documents do not cover is not answered.
say "Where can I park my car?"
expect_status "not in documents" 200
expect_field "not in documents" '.outcome' not_in_documents
pass "not in documents: the front desk is suggested instead of a guess"

# 6. A slot is offered and booked on a later day, without staff.
# The demo database is shared, so someone else may take the offered slot between the listing and
# the booking: then the api answers slot_taken with a fresh list, and we try once more.
for attempt in 1 2; do
  say "Show me free slots with $DOCTOR_NAME on $SLOT_DAY"
  expect_status "offer slots" 200
  expect_field "offer slots" '.outcome' slots_listed
  say "book number 1"
  expect_status "book" 200
  [ "$(field '.outcome')" = slot_taken ] && [ "$attempt" = 1 ] && continue
  break
done
expect_field "book" '.outcome' booked
case "$(field '.reply')" in
  *"$DOCTOR_NAME"*) ;;
  *) fail "book" "the confirmation does not name $DOCTOR_NAME" ;;
esac
pass "booking: confirmed at once with $DOCTOR_NAME for $SLOT_DAY"

# 7. A cancellation waits for staff, and approving it cancels the booking.
say "Cancel my appointment"
expect_status "cancel request" 200
expect_field "cancel request" '.status' awaiting_approval
APPROVAL="$(field '.approval_id')"
[ -n "$APPROVAL" ] && [ "$APPROVAL" != null ] || fail "cancel request" "no approval_id in the response"
call GET "/api/v1/approvals?status=pending&limit=500"
expect_status "pending approvals" 200
[ "$(printf '%s' "$BODY" | jq --arg id "$APPROVAL" '[.[] | select(.id == $id)] | length')" = 1 ] \
  || fail "pending approvals" "approval $APPROVAL is not in the pending list"
say "Is it done yet?"
expect_field "still waiting" '.outcome' still_waiting
call POST "/api/v1/approvals/$APPROVAL/approve" '{"decided_by":"smoke-test","reason":"smoke test"}'
expect_status "approve" 200
expect_field "approve" '.status' approved
case "$(field '.patient_message')" in *"cancelled"*) ;; *) fail "approve" "the patient is not told the booking is cancelled" ;; esac
pass "cancellation: paused for staff, approved, booking cancelled"

# 8. An emergency is answered at once and flagged for staff.
say "I have crushing chest pain"
expect_status "emergency" 200
expect_field "emergency" '.outcome' emergency
# The list has no paging and is newest first, so the flag raised just now is always in it.
call GET "/api/v1/emergencies?status=open"
expect_status "emergency flag" 200
[ "$(printf '%s' "$BODY" | jq --arg id "$CONVERSATION" '[.[] | select(.conversation_id == $id)] | length')" -ge 1 ] \
  || fail "emergency flag" "the emergency is not in the staff list"
pass "emergency: answered at once and flagged for staff"

echo "Smoke test passed: $CHECKS checks against $BASE_URL"
