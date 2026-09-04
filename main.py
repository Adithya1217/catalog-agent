"""
main.py -- FastAPI app for Time & Co.'s AI-readiness layer.

Mounts static/dashboard.html and exposes the merchant-facing endpoints an
AI buyer agent talks to. Routes stay thin: real logic lives in
enrich.py / negotiate.py / payment.py / audit.py.

    GET  /catalog        (LLM-enriched, served from data/store.db)
    POST /negotiate      (LLM reasoning + hardcoded guardrails)
    POST /payment        (Razorpay test-mode)
    GET  /audit-log       (timeline feed for the dashboard)

Under 10 endpoints total, per the locked architecture.
"""

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import audit
import negotiate
import payment
from models import AuditLog, CatalogItem, Mandate, SessionLocal, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Time & Co. -- AI-Readiness Layer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def dashboard():
    """Serve the merchant's AI Activity dashboard."""
    return FileResponse("static/dashboard.html")


# ---------------------------------------------------------------------------
# GET /catalog
# ---------------------------------------------------------------------------

@app.get("/catalog")
def get_catalog(db: Session = Depends(get_db)):
    """Return Time & Co.'s AI-enriched catalog for buyer agents to browse."""
    items = db.query(CatalogItem).all()
    return [
        {
            "id": item.id,
            "name": item.raw_name,
            "description": item.clean_description or item.raw_description,
            "category": item.category,
            "tags": item.tags,
            "price": item.price,
            "stock": item.stock,
            "terms": item.terms,
        }
        for item in items
    ]


# ---------------------------------------------------------------------------
# POST /negotiate
# ---------------------------------------------------------------------------

class NegotiateRequest(BaseModel):
    agent_id: str
    item_id: int
    quantity: int
    requested_discount_pct: float = 0.0


@app.post("/negotiate")
def negotiate_offer(req: NegotiateRequest, db: Session = Depends(get_db)):
    """A buyer agent proposes terms; hardcoded guardrails decide, the LLM
    explains. Every attempt is written to the AI Activity Log."""
    item = db.get(CatalogItem, req.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Catalog item not found")

    try:
        result = negotiate.evaluate_offer(
            item_price=item.price or 0.0,
            quantity=req.quantity,
            requested_discount_pct=req.requested_discount_pct,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="Negotiation logic not yet implemented (Phase 4)")

    try:
        reason = negotiate.explain_decision(result)
    except NotImplementedError:
        raise HTTPException(
            status_code=501,
            detail="Negotiation explanation not yet implemented (Phase 4b, pending prompt approval)",
        )

    audit.log_event(
        actor="buyer_agent",
        action="negotiate_offer",
        decision="approved" if result.get("approved") else "blocked",
        reason=reason,
        reasoning_basis="rule-matched",
        linked_entity_type="catalog_item",
        linked_entity_id=item.id,
        log_metadata={"agent_id": req.agent_id, **result},
        db=db,
    )
    return result


# ---------------------------------------------------------------------------
# POST /payment
# ---------------------------------------------------------------------------

class PaymentRequest(BaseModel):
    agent_id: str
    mandate_id: int
    item_id: int
    quantity: int
    agreed_price: float


@app.post("/payment")
def fire_payment(req: PaymentRequest, db: Session = Depends(get_db)):
    """Verify the buyer agent's mandate spend cap and category scope, then
    fire a Razorpay test-mode payment. Blocked/approved either way, always
    logged."""
    mandate = db.get(Mandate, req.mandate_id)
    if mandate is None:
        raise HTTPException(status_code=404, detail="Mandate not found")

    item = db.get(CatalogItem, req.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Catalog item not found")

    total = req.agreed_price * req.quantity

    within_cap = payment.verify_mandate_cap(req.mandate_id, total, db=db)
    if not within_cap:
        audit.log_event(
            actor="buyer_agent",
            action="mandate_check",
            decision="blocked",
            reason=f"Requested amount {total} exceeds mandate {mandate.id} spend cap",
            reasoning_basis="blocked-mandate-violation",
            linked_entity_type="mandate",
            linked_entity_id=mandate.id,
            db=db,
        )
        raise HTTPException(status_code=403, detail="Mandate spend cap exceeded")

    in_scope = payment.check_mandate_scope(req.mandate_id, item.category, db=db)
    if not in_scope:
        audit.log_event(
            actor="buyer_agent",
            action="mandate_check",
            decision="blocked",
            reason=f"Item category {item.category!r} not covered by mandate {mandate.id}'s category scope",
            reasoning_basis="blocked-mandate-violation",
            linked_entity_type="mandate",
            linked_entity_id=mandate.id,
            db=db,
        )
        raise HTTPException(status_code=403, detail="Item category not covered by mandate")

    order = payment.create_order(amount=total, notes={"agent_id": req.agent_id, "item_id": req.item_id})

    # Razorpay has no server-to-server payment-completion API (see payment.py
    # docstring) -- polling here gives a human time to complete Checkout for
    # this order out-of-band while the request is held open, no webhooks.
    captured_payment = payment.poll_payment_status(order["id"])

    if captured_payment is None:
        audit.log_event(
            actor="merchant_system",
            action="payment",
            decision="blocked",
            reason=(
                f"No completed Razorpay payment found for order {order['id']} "
                f"within the polling window; buyer must complete Checkout before payment can be confirmed"
            ),
            linked_entity_type="catalog_item",
            linked_entity_id=req.item_id,
            log_metadata={"order": order, "agent_id": req.agent_id},
            db=db,
        )
        raise HTTPException(
            status_code=402,
            detail={"message": "Payment not completed in time", "order": order},
        )

    audit.log_event(
        actor="merchant_system",
        action="payment",
        decision="approved",
        reason=f"Payment of ₹{total} captured for order {order['id']}",
        linked_entity_type="catalog_item",
        linked_entity_id=req.item_id,
        log_metadata={"order": order, "payment_id": captured_payment["id"], "agent_id": req.agent_id},
        db=db,
    )
    return {"order": order, "payment": captured_payment}


# ---------------------------------------------------------------------------
# GET /audit-log
# ---------------------------------------------------------------------------

@app.get("/audit-log")
def get_audit_log(db: Session = Depends(get_db)):
    """Timeline feed for the live dashboard. Polled every 2s."""
    entries = db.query(AuditLog).order_by(AuditLog.timestamp.asc()).all()
    return [
        {
            "id": e.id,
            "timestamp": e.timestamp.isoformat() if isinstance(e.timestamp, datetime) else e.timestamp,
            "actor": e.actor,
            "action": e.action,
            "decision": e.decision,
            "reason": e.reason,
            "reasoning_basis": e.reasoning_basis,
            "linked_entity_type": e.linked_entity_type,
            "linked_entity_id": e.linked_entity_id,
            "metadata": e.log_metadata,
        }
        for e in entries
    ]
