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

One run per invocation against the live API, on a plain-language buy
request that can come from three places (see the __main__ block below):
`--happy` or `--failure` replay one of two verified scenarios, anything
else on the command line is sent verbatim as a custom request, and no
arguments at all prompts interactively for a choice.
  * Happy path (HAPPY_PATH_REQUEST): the agent browses, optionally
    negotiates, and pays -- ending in a real captured Razorpay test-mode
    payment (a human completes Checkout live; see checkout.py) and a
    full audit trail.
  * Deliberate failure path (FAILURE_PATH_REQUEST): a bulk request sized
    to exceed the seeded mandate's spend cap -- blocked before any real
    payment is attempted, logged, and reported back to the customer
    instead of retried.
  * A custom request runs through the identical pipeline as either of
    the above; nothing about guardrails, tools, or the Gemini call
    changes based on which request text it's given.
"""

import sys

import requests
from dotenv import load_dotenv

import console_ui as ui
from checkout import pay_with_checkout
from llm import request_generate_content

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
    # /negotiate makes its own Gemini call internally (explain_decision(),
    # up to llm.py's 90s timeout) -- 30s here was too tight and could time
    # out client-side while the server was still legitimately working.
    resp = requests.post(f"{BASE_URL}/negotiate", json=payload, timeout=120)
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
    """Same request as before -- same model, same key, same tool schema --
    now routed through llm.request_generate_content() so a transient 503
    retries instead of crashing the run mid-demo."""
    return request_generate_content(
        {"contents": contents, "tools": [{"functionDeclarations": FUNCTION_DECLARATIONS}]}
    )


def run_agent(user_request: str, max_turns: int = MAX_TURNS):
    """Run one bounded tool-use loop for a single customer request. The
    model decides the sequence of tool calls itself; this just executes
    whatever it asks for against the real API and feeds the result back.

    Presentation is delegated to console_ui; the control flow, tool calls
    and API interactions below are unchanged. The local bookkeeping vars
    (catalog_items / selected_item / last_reasoning / negotiate_body) only
    feed the console's tables and final receipt -- they never influence
    what the agent decides or what gets sent anywhere.
    """
    ui.header()
    ui.purchase_intent(user_request, AGENT_ID, MANDATE_ID)

    catalog_items: list = []
    selected_item: dict | None = None
    last_reasoning: str | None = None
    negotiate_body: dict | None = None

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
            ui.note(f"(model returned no content this turn -- finishReason={reason}; stopping)")
            break

        for part in parts:
            text = part.get("text", "").strip()
            if text:
                last_reasoning = text
                ui.agent_reasoning(text)

        function_call_part = next((p for p in parts if "functionCall" in p), None)
        if function_call_part is None:
            break  # model gave its final answer, no more tool calls

        contents.append({"role": "model", "parts": parts})

        fc = function_call_part["functionCall"]
        name = fc["name"]
        args = fc.get("args", {})
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())

        if name == "get_catalog":
            ui.stage("AI BUYER AGENT → CATALOG")
        elif name == "negotiate":
            ui.stage("NEGOTIATION → GUARDRAIL CHECK")
        elif name == "pay":
            ui.stage("MERCHANT API → MANDATE CHECK → RAZORPAY")
        ui.tool_call(name, args_str)

        result = TOOL_IMPLS[name](**args)

        _render_tool_result(
            name,
            args,
            result,
            catalog_items,
            selected_item,
            last_reasoning,
            negotiate_body,
        )

        # Bookkeeping for the console's table/receipt only.
        if name == "get_catalog" and result.get("status_code") == 200:
            catalog_items = result.get("body") or []
        elif name in ("negotiate", "pay"):
            item_id = args.get("item_id")
            selected_item = next((i for i in catalog_items if i.get("id") == item_id), selected_item)
            if name == "negotiate" and result.get("status_code") == 200:
                negotiate_body = result.get("body") or {}
            if name == "pay":
                _render_receipt_if_complete(result, selected_item, negotiate_body)

        contents.append(
            {"role": "user", "parts": [{"functionResponse": {"name": name, "response": {"result": result}}}]}
        )
    else:
        ui.note("(reached max turns without a final answer)")

    ui.run_end()


def _render_tool_result(name, args, result, catalog_items, selected_item, last_reasoning, negotiate_body):
    """Render one real tool result. Values shown come straight from
    `result`; nothing is synthesised."""
    status = result.get("status_code")
    body = result.get("body")

    if name == "get_catalog":
        items = body or []
        ui.catalog_found(len(items))
        return

    if name == "negotiate":
        item_id = args.get("item_id")
        item = next((i for i in catalog_items if i.get("id") == item_id), None)
        if item:
            excerpt = (last_reasoning or "").strip()
            if len(excerpt) > 120:
                excerpt = excerpt[:117].rstrip() + "..."
            ui.comparison_table(catalog_items, item_id, excerpt or None)
            ui.product_selected(item, args.get("quantity"))
        if status == 200 and isinstance(body, dict):
            ui.negotiation_result(body)
        else:
            ui.blocked("NEGOTIATE CALL FAILED", _detail_text(body, status))
        return

    if name == "pay":
        # checkout.py already announces mandate-passed/order-created the
        # moment it finds the real order (before the human checkout step),
        # so the common case doesn't repeat them here. The fallback below
        # only fires in the rare case that announcement didn't happen yet
        # (the order-lookup window expired before checkout.py found it).
        already_announced = bool(result.get("order_announced"))

        if status == 403:
            # Mandate check (spend cap / category scope) refused it before
            # Razorpay was ever contacted.
            ui.mandate_blocked(_detail_text(body, status))
        elif status == 200 and isinstance(body, dict):
            if not already_announced:
                ui.mandate_passed()
                order = body.get("order") or {}
                ui.razorpay_order_created(order.get("id", "-"), order.get("amount"))
            payment = body.get("payment") or {}
            ui.payment_authorized(payment.get("id", "-"))
        elif status == 402:
            # Mandate passed; a real order exists but no human completed
            # checkout inside the polling window.
            if not already_announced:
                ui.mandate_passed()
                detail = body.get("detail") if isinstance(body, dict) else None
                order = (detail or {}).get("order") if isinstance(detail, dict) else None
                if isinstance(order, dict):
                    ui.razorpay_order_created(order.get("id", "-"), order.get("amount"))
            ui.payment_not_completed(_detail_text(body, status))
        else:
            ui.blocked("PAYMENT CALL FAILED", _detail_text(body, status))
        return


def _detail_text(body, status) -> str:
    """Pull the real reason string out of whatever shape the API returned."""
    if isinstance(body, dict):
        detail = body.get("detail", body)
        if isinstance(detail, dict):
            return str(detail.get("message") or detail)
        return str(detail)
    return f"HTTP {status}: {body}"


def _render_receipt_if_complete(result, selected_item, negotiate_body):
    """Final receipt, only on a genuinely captured payment, built solely
    from values the system returned."""
    if result.get("status_code") != 200:
        return
    body = result.get("body") or {}
    order = body.get("order") or {}
    payment = body.get("payment") or {}
    amount_paise = order.get("amount")
    amount = amount_paise / 100 if isinstance(amount_paise, (int, float)) else None
    ui.receipt(
        product_name=(selected_item or {}).get("name", "-"),
        amount=amount,
        order_id=order.get("id", "-"),
        guardrail_passed=bool((negotiate_body or {}).get("approved")),
        payment_id=payment.get("id", "-"),
    )


HAPPY_PATH_REQUEST = "Please buy some blue pens for the office."

FAILURE_PATH_REQUEST = (
    "I need 150 packs of the assorted sticky notes for a big office "
    "stock-up -- see if you can get a bulk discount and place the order."
)


def run_happy_path():
    """A reasonable buy request, well within the seeded mandate's ₹15,000
    cap -- the agent should browse, optionally negotiate, and pay, ending
    in a real captured Razorpay payment once a human completes Checkout."""
    run_agent(HAPPY_PATH_REQUEST)


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
    run_agent(FAILURE_PATH_REQUEST)


def _prompt_for_request() -> str:
    """Let a human picking this run live choose what to ask the agent to
    buy, instead of always silently replaying both canned scenarios.
    Runs the same run_agent() either way -- nothing about the guardrails,
    tools, or API calls changes based on which option is picked."""
    ui.console.print()
    ui.console.print("[bold]Choose a run:[/bold]")
    ui.console.print("  [bold cyan]1[/bold cyan]) Happy path demo  [dim](verified: blue pens, within mandate)[/dim]")
    ui.console.print("  [bold cyan]2[/bold cyan]) Failure path demo  [dim](verified: bulk sticky notes, trips a guardrail)[/dim]")
    ui.console.print("  [bold cyan]3[/bold cyan]) Type your own buy request")
    choice = input("Enter choice [1/2/3, default 1]: ").strip()

    if choice == "2":
        return FAILURE_PATH_REQUEST
    if choice == "3":
        custom = input("Buy request: ").strip()
        if custom:
            return custom
        ui.console.print("[dim](no request entered -- running the happy-path scenario instead)[/dim]")
    return HAPPY_PATH_REQUEST


if __name__ == "__main__":
    # `python demo_agent.py` with no arguments prompts interactively so a
    # live demo isn't stuck replaying the same two fixed requests every
    # time. CLI args are still supported for scripted/repeatable runs:
    #   --happy            run the verified happy-path scenario
    #   --failure          run the verified failure-path scenario
    #   <anything else>    treated as a custom buy request, e.g.
    #                      python demo_agent.py "buy 3 packs of blue pens"
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--happy":
            run_happy_path()
        elif arg == "--failure":
            run_failure_path()
        elif arg.startswith("--"):
            # An unrecognized flag is far more likely to be a typo than a
            # genuine buy request -- fail loudly instead of silently
            # sending the literal flag text to the agent as a purchase ask.
            print(
                f"Unrecognized flag: {arg!r}. Use --happy, --failure, "
                "a plain-text buy request, or no arguments to be prompted.",
                file=sys.stderr,
            )
            sys.exit(2)
        else:
            run_agent(" ".join(sys.argv[1:]))
    else:
        run_agent(_prompt_for_request())
