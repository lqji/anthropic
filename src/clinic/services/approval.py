"""Staff decisions on approvals, resuming the paused workflow, and the startup reconcile."""

import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import psycopg
from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.errors import AlreadyDecided, AppError, NotFound, ResumeFailed, WorkflowUnavailable
from clinic.locks import ConversationLocks
from clinic.repositories import approvals, conversations, messages
from clinic.repositories.approvals import ApprovalRow
from clinic.workflows.approval import ApprovalOutcome

logger = logging.getLogger("clinic.approval")


class ApprovalResumer(Protocol):
    """Resumes the workflow paused at `await_approval`. `GraphApprovalResumer` implements it
    over CFD-05's compiled graph."""

    def resume(
        self, conversation_id: str, approval_id: str, decision: str, reason: str
    ) -> ApprovalOutcome: ...


class UnavailableResumer:
    """Used until CFD-05's graph is wired in. Without a graph no approval can be created, so
    this is never reached with a real pending approval."""

    def resume(
        self, conversation_id: str, approval_id: str, decision: str, reason: str
    ) -> ApprovalOutcome:
        raise WorkflowUnavailable("the conversation workflow is not available yet")


@dataclass(frozen=True)
class DecisionResult:
    approval_id: UUID
    status: str
    patient_message: str


class ApprovalService:
    def __init__(
        self,
        pool: ConnectionPool,
        clock: Clock,
        locks: ConversationLocks,
        resumer: ApprovalResumer,
    ) -> None:
        self._pool = pool
        self._clock = clock
        self._locks = locks
        self._resumer = resumer

    def list_by_status(self, status: str) -> list[ApprovalRow]:
        with self._pool.connection() as conn:
            return approvals.list_by_status(conn, status)

    def decide(
        self, approval_id: str, decision: str, decided_by: str, reason: str
    ) -> DecisionResult:
        """Record a staff decision, resume the paused workflow and tell the patient.

        404 if unknown. 409 already_decided if the approval was already decided and finished, or
        was decided the other way. 409 conversation_busy if the conversation is handling a
        message (nothing changes; try again). 503 resume_failed if the decision was saved but the
        workflow could not be resumed.

        The steps: take the conversation lock, record the decision with a conditional UPDATE
        (only one decision can win), resume the workflow, store the outcome.

        The decision is committed before the workflow resumes, so an approval can be "decided but
        not resumed" if the process dies or the resume fails in between. Two things finish the
        job: repeating the same decision (a retry, handled below) and `reconcile()` at the next
        start.
        """
        aid = _parse_id(approval_id)
        approval = self._get(aid)
        if approval.status != "pending" and not _awaiting_resume(approval, decision):
            raise AlreadyDecided(f"approval already {approval.status}")

        with self._locks.hold(str(approval.conversation_id)):
            # Read again under the lock: another request may have finished the job meanwhile.
            approval = self._get(aid)
            if approval.status == "pending":
                with self._pool.connection() as conn:
                    decided = approvals.decide(
                        conn, aid, decision, decided_by, reason, self._clock.now()
                    )
                if decided is None:
                    raise AlreadyDecided("approval already decided")  # another process won
                logger.info(
                    "approval.decided",
                    extra={
                        "fields": {
                            "approval_id": str(aid),
                            "kind": decided.kind,
                            "decision": decision,
                        }
                    },
                )
                approval = decided
            elif not _awaiting_resume(approval, decision):
                raise AlreadyDecided(f"approval already {approval.status}")
            # else: the same decision is saved but never finished (a failed resume): retry it.
            return self._resume_or_fail(approval)

    def reconcile(self) -> int:
        """Resume every decided approval whose workflow never resumed. Returns how many."""
        with self._pool.connection() as conn:
            pending_resumes = approvals.list_unresumed(conn)
        resumed = 0
        for approval in pending_resumes:
            try:
                with self._locks.hold(str(approval.conversation_id)):
                    self._resume(approval)
            except Exception:
                # One broken approval must not stop the others or the startup.
                logger.exception(
                    "approval.reconcile_failed", extra={"fields": {"approval_id": str(approval.id)}}
                )
                continue
            resumed += 1
            logger.info("approval.reconciled", extra={"fields": {"approval_id": str(approval.id)}})
        return resumed

    def _get(self, approval_id: UUID) -> ApprovalRow:
        with self._pool.connection() as conn:
            approval = approvals.get(conn, approval_id)
        if approval is None:
            raise NotFound("approval not found")
        return approval

    def _resume_or_fail(self, approval: ApprovalRow) -> DecisionResult:
        """`_resume`, but a failure is logged and becomes a JSON 503, never a bare 500."""
        try:
            return self._resume(approval)
        except Exception as exc:
            logger.exception(
                "approval.resume_failed",
                extra={"fields": {"approval_id": str(approval.id), "kind": approval.kind}},
            )
            if isinstance(exc, AppError | psycopg.OperationalError):
                raise  # already a JSON 503: workflow unavailable, or database unavailable
            raise ResumeFailed(
                "the decision is saved, but the conversation could not be resumed; "
                "repeat the same decision to finish"
            ) from exc

    def _resume(self, approval: ApprovalRow) -> DecisionResult:
        outcome = self._resumer.resume(
            str(approval.conversation_id), str(approval.id), approval.status, approval.reason or ""
        )
        now = self._clock.now()
        with self._pool.connection() as conn:
            messages.add(
                conn,
                approval.conversation_id,
                "assistant",
                outcome.reply,
                now,
                intent=approval.kind,
                outcome=outcome.outcome,
            )
            conversations.set_status(conn, approval.conversation_id, "active")
            approvals.mark_resumed(conn, approval.id, now)
        return DecisionResult(
            approval_id=approval.id, status=approval.status, patient_message=outcome.reply
        )


def _awaiting_resume(approval: ApprovalRow, decision: str) -> bool:
    """The same decision is already saved, but the workflow never finished resuming."""
    return approval.status == decision and approval.resumed_at is None


def _parse_id(approval_id: str) -> UUID:
    try:
        return UUID(approval_id)
    except ValueError:
        raise NotFound("approval not found") from None
