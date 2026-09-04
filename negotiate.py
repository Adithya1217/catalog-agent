"""
negotiate.py -- guardrail config (hardcoded) + negotiation logic (Phase 4).

Guardrails are deterministic Python, never LLM-generated -- the LLM's job
is to explain a decision within the rules, not decide the rules.

STATUS: evaluate_offer() and explain_decision() both implemented (Phase 4).
EXPLAIN_DECISION_PROMPT approved after a 2-case dry run (approved/blocked)
against gemini-3.5-flash-lite -- see llm.py for the model choice rationale.
GUARDRAILS below is a placeholder shape; the user owns the actual rule
values (max discount %, margin floor, etc.). Do not treat the placeholder
as final.
"""

from llm import call_gemini

# TODO(user-approval-required): confirm real thresholds before Phase 4.
GUARDRAILS = {
    "max_discount_pct": 5,          # e.g. never discount more than 5%
    "min_order_for_discount": 10000,  # INR; below this, no discount offered
    # margin_floor_pct: UNSET, not used by evaluate_offer(). CatalogItem has
    # no cost/wholesale-cost column (models.py is frozen), so there is no
    # basis to compute a real (price - cost) / price margin. Descoped per
    # the project's risk register: evaluate_offer() below implements the
    # single deterministic rule instead ("<=5% off if order total clears
    # ₹10k, else decline"). If a margin floor is still wanted, it would need
    # to be redefined as a proxy -- e.g. "final discounted price must not
    # drop below X% of listed price" -- which needs an explicit user call,
    # since max_discount_pct already caps how low price can go.
    "margin_floor_pct": None,
}


def evaluate_offer(item_price: float, quantity: int, requested_discount_pct: float) -> dict:
    """Apply hardcoded guardrails to a buyer agent's negotiation request.

    Deterministic, single-rule flow (see GUARDRAILS): a discount is only
    granted when the order total clears min_order_for_discount, and never
    exceeds max_discount_pct. Anything else is blocked outright -- there is
    no partial-approval/counter-offer path.

    Returns a dict shaped like:
        {
            "approved": bool,
            "final_discount_pct": float,
            "final_price": float,
            "rule_applied": str,     # human-readable rule name/id
        }

    The LLM is used downstream only to phrase `reason` for the audit log,
    never to decide `approved` or `final_discount_pct`.
    """
    order_total = item_price * quantity
    max_discount_pct = GUARDRAILS["max_discount_pct"]
    min_order_for_discount = GUARDRAILS["min_order_for_discount"]

    if requested_discount_pct <= 0:
        return {
            "approved": True,
            "final_discount_pct": 0.0,
            "final_price": order_total,
            "rule_applied": "no discount requested",
        }

    if requested_discount_pct > max_discount_pct:
        return {
            "approved": False,
            "final_discount_pct": 0.0,
            "final_price": order_total,
            "rule_applied": (
                f"requested discount {requested_discount_pct}% exceeds "
                f"max_discount_pct ({max_discount_pct}%)"
            ),
        }

    if order_total < min_order_for_discount:
        return {
            "approved": False,
            "final_discount_pct": 0.0,
            "final_price": order_total,
            "rule_applied": (
                f"order total {order_total} below min_order_for_discount "
                f"({min_order_for_discount})"
            ),
        }

    final_price = order_total * (1 - requested_discount_pct / 100)
    return {
        "approved": True,
        "final_discount_pct": requested_discount_pct,
        "final_price": final_price,
        "rule_applied": (
            f"discount within max_discount_pct ({max_discount_pct}%) and "
            f"order total clears min_order_for_discount ({min_order_for_discount})"
        ),
    }


EXPLAIN_DECISION_PROMPT = """You are writing one line for Time & Co.'s AI Activity Log -- a merchant-
facing audit trail shown on the shop owner's dashboard. A deterministic
guardrail system (not you) has already decided the outcome below for a
buyer agent's discount negotiation. Your only job is to phrase that
decision in plain, friendly English for the shop owner to read at a
glance. You do not decide anything and you must not change, soften, or
second-guess the decision -- only explain it.

All amounts are in Indian Rupees. Always write them with the ₹ symbol
(e.g. ₹9,500) -- never $, USD, or any other currency symbol or code.

Decision already made:
  approved: {approved}
  final_discount_pct: {final_discount_pct}
  final_price: {final_price}
  rule_applied: {rule_applied}

Write ONE short sentence (no preamble, no markdown, just the sentence)
explaining WHY this decision was made, in terms a shop owner would
understand -- not a developer. Reference the actual numbers where useful.
Do not use the words "guardrail", "rule_applied", or other internal field
names verbatim; describe the reasoning in plain terms instead.

Return ONLY that one sentence."""


def explain_decision(offer_result: dict) -> str:
    """Ask Gemini (see llm.call_gemini) to phrase a plain-English
    explanation of a guardrail decision already made by evaluate_offer().
    The LLM reasons *within* the rule, it does not invent or override it.
    """
    prompt = EXPLAIN_DECISION_PROMPT.format(**offer_result)
    return call_gemini(prompt, json_mode=False).strip()
