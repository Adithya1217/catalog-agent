"""
payment.py -- Razorpay test-mode wrapper (Phase 5).

Create order, verify mandate spend cap before firing. Follows Razorpay's
official REST/Python-SDK docs (razorpay.com/docs/api/orders,
razorpay.com/docs/api/payments) -- shapes below were confirmed live
against a real test-mode account, not guessed.

STATUS: live. get_client()/create_order()/poll_payment_status() all real.

Architectural note (de-risked before writing this): Razorpay has no
server-to-server endpoint to complete a payment. Their own Payments API
docs say it can only "retrieve payment details or change the status from
authorized to captured -- not to collect payments." Every payment,
even in test mode, must go through Checkout (a mock bank page with
OTP/Success-Failure), which is a browser/human step. Headless automation
of that step (Playwright) was tried and reliably hangs at the OTP-send
step -- Razorpay's Checkout embeds real bot-detection (hCaptcha invisible,
Sardine.ai device fingerprinting, Stripe's Human Security), which is not
something to try to defeat. So: create_order() is fully real/server-side.
Actual payment completion happens via a human completing real Checkout
out-of-band (proven working manually). poll_payment_status() then confirms
completion via polling (GET /v1/orders/:id/payments) -- no webhooks, per
project scope.
"""

import os
import time

import razorpay
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from models import Mandate, SessionLocal

load_dotenv()


def get_client() -> razorpay.Client:
    """Return an authenticated Razorpay test-mode client.

    Reads RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET from the environment.
    """
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        raise RuntimeError("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set")
    return razorpay.Client(auth=(key_id, key_secret))


def verify_mandate_cap(mandate_id: int, amount: float, db: Session | None = None) -> bool:
    """Check `amount` against the mandate's spend cap.

    mandate_type="one_time" mandates (the only kind seeded so far) treat
    spend_cap as a single-transaction ceiling, not a running balance --
    there is no "spent so far" column on Mandate (models.py is frozen), so
    this is a straight amount <= spend_cap comparison. Must be called --
    and must pass -- before any payment call fires.
    """
    owns_session = db is None
    session = db or SessionLocal()
    try:
        mandate = session.get(Mandate, mandate_id)
        if mandate is None:
            return False
        return amount <= mandate.spend_cap
    finally:
        if owns_session:
            session.close()


def check_mandate_scope(mandate_id: int, category: str | None, db: Session | None = None) -> bool:
    """Check whether `category` is covered by the mandate's category_scope.

    An empty or null category_scope means unrestricted (any category
    passes). Otherwise, each scope entry is treated as a category-tree
    prefix, not just an exact string -- e.g. scope=["stationery"] covers
    category="stationery/writing". Enrichment (Phase 3) produces
    hierarchical "top/sub" categories, so a bare top-level scope entry
    like "stationery" would never exact-match any real enriched category
    and would block every payment; prefix matching is the only reading
    under which a mandate scoped to "stationery" actually authorizes
    stationery purchases. Must be called -- and must pass -- before any
    payment call fires, alongside verify_mandate_cap().
    """
    owns_session = db is None
    session = db or SessionLocal()
    try:
        mandate = session.get(Mandate, mandate_id)
        if mandate is None:
            return False
        scope = mandate.category_scope
        if not scope:
            return True
        if category is None:
            return False
        return any(category == s or category.startswith(f"{s}/") for s in scope)
    finally:
        if owns_session:
            session.close()


def create_order(amount: float, currency: str = "INR", notes: dict | None = None) -> dict:
    """Create a Razorpay test-mode order. Returns the SDK's order dict.

    `amount` is in rupees (matches the rest of this project); Razorpay's
    Orders API takes the amount in paise, so it's converted here.
    """
    client = get_client()
    return client.order.create(
        data={
            "amount": int(round(amount * 100)),
            "currency": currency,
            "notes": notes or {},
        }
    )


def poll_payment_status(order_id: str, timeout_seconds: float = 240, interval_seconds: float = 3) -> dict | None:
    """Poll (no webhooks) for a captured payment against `order_id`.

    Payment completion happens out-of-band, via a human completing
    Razorpay Checkout (see module docstring) -- this just watches for the
    result. Returns the first captured payment dict found, or None if
    none is captured before timeout_seconds elapses.
    """
    client = get_client()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        payments = client.order.payments(order_id).get("items", [])
        for payment in payments:
            if payment.get("status") == "captured":
                return payment
        time.sleep(interval_seconds)
    return None
