"""
audit.py -- helper for writing to the AI Activity Log.

Every money-adjacent or AI-driven decision in this system (enrichment,
negotiation, mandate checks, payment) must call log_event() so it shows
up on the merchant's live dashboard timeline. Keep `reason` in plain,
merchant-facing English -- it's rendered directly on dashboard.html,
not a stack trace.
"""

from sqlalchemy.orm import Session

from models import AuditLog, SessionLocal


def log_event(
    *,
    actor: str,
    action: str,
    decision: str,
    reason: str,
    reasoning_basis: str | None = None,
    linked_entity_type: str | None = None,
    linked_entity_id: int | None = None,
    log_metadata: dict | None = None,
    db: Session | None = None,
) -> AuditLog:
    """Write one entry to the AI Activity Log (AuditLog table).

    Args:
        actor: "buyer_agent" or "merchant_system".
        action: "catalog_query" | "enrichment" | "negotiate_offer"
            | "mandate_check" | "payment".
        decision: "approved" | "blocked" | "info".
        reason: Plain-English explanation, shown on the dashboard.
        reasoning_basis: "inferred" | "rule-matched"
            | "blocked-mandate-violation", optional.
        linked_entity_type / linked_entity_id: e.g. ("catalog_item", 3).
        log_metadata: optional structured extras (amounts, discount %, etc).
        db: existing session to reuse; a new one is opened and committed
            if omitted.

    Returns:
        The created AuditLog row (detached if a local session was used).
    """
    owns_session = db is None
    session = db or SessionLocal()
    try:
        entry = AuditLog(
            actor=actor,
            action=action,
            decision=decision,
            reason=reason,
            reasoning_basis=reasoning_basis,
            linked_entity_type=linked_entity_type,
            linked_entity_id=linked_entity_id,
            log_metadata=log_metadata,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry
    finally:
        if owns_session:
            session.close()
