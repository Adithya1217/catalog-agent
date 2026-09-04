"""
test_negotiate_logic.py -- LOGIC-ONLY sanity check for evaluate_offer() (Phase 4).

Uses manually set, made-up item_price values -- NOT the real seeded
catalog's item.price, which is still null (enrichment hasn't run yet).
This only proves evaluate_offer()'s branching is internally consistent
given GUARDRAILS' current placeholder values; it says nothing about
behavior against real catalog data. That end-to-end pass happens later,
once enrichment has run, per the project's phased approval process.

Run: python test_negotiate_logic.py
"""

from negotiate import GUARDRAILS, evaluate_offer

CASES = [
    # (label, item_price, quantity, requested_discount_pct, expect_approved)
    ("no discount requested -> trivially approved", 500, 20, 0, True),
    ("discount within limits, order clears min -> approved", 500, 20, 5, True),
    ("discount exceeds max_discount_pct -> blocked", 500, 20, 10, False),
    ("discount within limits but order too small -> blocked", 500, 5, 5, False),
    ("large order, max discount exactly at boundary -> approved", 1000, 20, GUARDRAILS["max_discount_pct"], True),
]


def run():
    failures = 0
    for label, item_price, quantity, requested_discount_pct, expect_approved in CASES:
        result = evaluate_offer(item_price, quantity, requested_discount_pct)
        ok = result["approved"] == expect_approved
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"[{status}] {label}")
        print(f"       input: item_price={item_price} quantity={quantity} requested_discount_pct={requested_discount_pct}")
        print(f"       result: {result}")

    print()
    if failures:
        print(f"{failures}/{len(CASES)} logic cases FAILED.")
        raise SystemExit(1)
    print(f"All {len(CASES)} logic-only cases passed (evaluate_offer branching only -- not end-to-end).")


if __name__ == "__main__":
    run()
