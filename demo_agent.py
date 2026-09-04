"""
demo_agent.py -- test harness (Phase 7), not the hero.

One live Gemini call with tool-use per run: given a plain-language buy
request, the model decides for itself whether/how to browse the catalog,
negotiate, and pay -- using the three real endpoints as its only tools.
Nothing here is scripted turn-by-turn; the model picks the sequence, and
its reasoning is printed as it goes, since watching it decide is the
actual demo. No persistent UI, no memory beyond one run.

Reuses llm.py's GEMINI_MODEL / GEMINI_API_URL / auth pattern (same model,
same key, same direct-HTTP-via-requests approach) rather than adding a
second LLM integration -- llm.py's own call_gemini() is single-turn
text/JSON only, so the multi-turn tool-calling loop lives here instead of
being bolted onto that module.

Two runs against the live API:
  1. Happy path: a plain-language buy request the agent browses,
     optionally negotiates, and pays for -- ending in a real captured
     Razorpay test-mode payment (a human completes Checkout live; see
     checkout.py) and a full audit trail.
  2. Deliberate failure path: a bulk request sized to exceed the seeded
     mandate's spend cap -- blocked before any real payment is attempted,
     logged, and reported back to the customer instead of retried.
"""

import json
import os

import requests
from dotenv import load_dotenv

from checkout import pay_with_checkout
from llm import GEMINI_API_URL, GEMINI_MODEL

load_dotenv()

BASE_URL = "http://localhost:8000"

# Matches the mandate seeded in Phase 2 (seed.py's DEMO_MANDATE) -- this
# script always transacts as that one buyer agent under that one mandate.
AGENT_ID = "demo-buyer-agent-01"
MANDATE_ID = 1

MAX_TURNS = 6

SYSTEM_PREAMBLE = """You are an AI buyer agent purchasing stationery from Time & Co.'s live API \
on behalf of a customer. Before every tool call, briefly explain your reasoning in one or two \
plain-English sentences -- this is shown directly to the customer, so make it read naturally.

You have three tools: get_catalog, negotiate, and pay. Browse the catalog first so you have \
real item ids, prices and stock levels -- never invent them. Requesting a discount via negotiate \
is optional; only ask for one if the order is genuinely large, and never ask for more than a \
modest discount. Only call pay once you have a specific item, quantity and an agreed price \
(the negotiated price if you negotiated, otherwise the plain catalog price).

If negotiate or pay comes back blocked, explain plainly to the customer why, in your own words \
based on the reason given -- and stop. Do not retry a blocked request with the same terms, and \
do not invent a workaround the API didn't offer you."""

FUNCTION_DECLARATIONS = [
    {
        "name": "get_catalog",
        "description": (
            "Browse Time & Co.'s current catalog: item ids, names, descriptions, "
            "categories, prices in INR, and stock levels."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "negotiate",
        "description": (
            "Propose an order (item, quantity, optionally a requested discount percentage) "
            "and get back a guardrail-checked decision: approved or blocked, with the final "
            "price and a plain-English reason."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer", "description": "The catalog item's id."},
                "quantity": {"type": "integer", "description": "How many units to buy."},
                "requested_discount_pct": {
                    "type": "number",
                    "description": "Discount percentage requested, 0 if none.",
                },
            },
            "required": ["item_id", "quantity"],
        },
    },
    {
        "name": "pay",
        "description": (
            "Attempt to pay for an order. Checked against the buyer's mandate (spend cap and "
            "category scope) before any real payment is attempted. If checks pass, a real "
            "Razorpay test-mode order is created and a checkout link is printed for a human to "
            "complete live -- this call waits for that to happen, so it can take a while."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "quantity": {"type": "integer"},
                "agreed_price": {
                    "type": "number",
                    "description": "Final per-unit price agreed (negotiated price, or the plain catalog price).",
                },
            },
            "required": ["item_id", "quantity", "agreed_price"],
        },
    },
]


def tool_get_catalog():
    resp = requests.get(f"{BASE_URL}/catalog", timeout=30)
    return {"status_code": resp.status_code, "body": resp.json()}


def tool_negotiate(item_id: int, quantity: int, requested_discount_pct: float = 0.0):
    payload = {
        "agent_id": AGENT_ID,
        "item_id": item_id,
        "quantity": quantity,
        "requested_discount_pct": requested_discount_pct,
    }
    resp = requests.post(f"{BASE_URL}/negotiate", json=payload, timeout=30)
    return {"status_code": resp.status_code, "body": resp.json()}


def tool_pay(item_id: int, quantity: int, agreed_price: float):
    payload = {
        "agent_id": AGENT_ID,
        "mandate_id": MANDATE_ID,
        "item_id": item_id,
        "quantity": quantity,
        "agreed_price": agreed_price,
    }
    return pay_with_checkout(payload, base_url=BASE_URL)


TOOL_IMPLS = {"get_catalog": tool_get_catalog, "negotiate": tool_negotiate, "pay": tool_pay}


def _call_gemini_tools(contents: list) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    resp = requests.post(
        GEMINI_API_URL.format(model=GEMINI_MODEL),
        headers={"x-goog-api-key": api_key, "content-type": "application/json"},
        json={"contents": contents, "tools": [{"functionDeclarations": FUNCTION_DECLARATIONS}]},
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()


def run_agent(user_request: str, max_turns: int = MAX_TURNS):
    """Run one bounded tool-use loop for a single customer request. The
    model decides the sequence of tool calls itself; this just executes
    whatever it asks for against the real API and feeds the result back."""
    print(f"\n{'=' * 70}")
    print(f"DEMO AGENT RUN")
    print(f"Request: {user_request!r}")
    print(f"{'=' * 70}\n")

    contents = [
        {"role": "user", "parts": [{"text": SYSTEM_PREAMBLE + "\n\nCustomer request: " + user_request}]}
    ]

    for _ in range(max_turns):
        response = _call_gemini_tools(contents)
        candidate = response["candidates"][0]
        parts = candidate.get("content", {}).get("parts")
        if not parts:
            # No content parts -- e.g. finishReason SAFETY/RECITATION/MAX_TOKENS
            # with nothing generated. Real, if rare; report it and stop rather
            # than crash on a KeyError.
            reason = candidate.get("finishReason", "unknown")
            print(f"[agent] (model returned no content this turn -- finishReason={reason}; stopping)\n")
            break

        for part in parts:
            text = part.get("text", "").strip()
            if text:
                print(f"[agent] {text}\n")

        function_call_part = next((p for p in parts if "functionCall" in p), None)
        if function_call_part is None:
            break  # model gave its final answer, no more tool calls

        contents.append({"role": "model", "parts": parts})

        fc = function_call_part["functionCall"]
        name = fc["name"]
        args = fc.get("args", {})
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        print(f"[agent] -> calling {name}({args_str})")

        result = TOOL_IMPLS[name](**args)
        print(f"[agent] <- {name} returned: {json.dumps(result, default=str)[:400]}\n")

        contents.append(
            {"role": "user", "parts": [{"functionResponse": {"name": name, "response": {"result": result}}}]}
        )
    else:
        print("[agent] (reached max turns without a final answer)")

    print(f"{'=' * 70}")
    print("END OF RUN")
    print(f"{'=' * 70}\n")


def run_happy_path():
    """A reasonable buy request, well within the seeded mandate's ₹15,000
    cap -- the agent should browse, optionally negotiate, and pay, ending
    in a real captured Razorpay payment once a human completes Checkout."""
    run_agent("Please buy some blue pens for the office.")


def run_failure_path():
    """A bulk request sized to exceed the seeded mandate's ₹15,000 spend
    cap using sticky notes (SKU 3, ₹120/pack, 200 in stock): 150 packs is
    ₹18,000 even with zero discount requested, and comfortably within real
    stock -- unlike an earlier draft of this scenario (50x Wooden Desk
    Organiser), which turned out to be impossible to trip honestly: that
    item only has 12 in stock, capping its real value at ₹4,200, well
    under the cap, so a well-behaved agent that respects real stock (as
    it should, and did) can never exceed the mandate through it. Blocked
    at payment's mandate-cap check regardless of whether a discount is
    requested, before any real payment is attempted."""
    run_agent(
        "I need 150 packs of the assorted sticky notes for a big office "
        "stock-up -- see if you can get a bulk discount and place the order."
    )


if __name__ == "__main__":
    run_happy_path()
    run_failure_path()
