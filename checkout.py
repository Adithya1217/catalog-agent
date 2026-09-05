"""
checkout.py -- reusable human-in-the-loop Razorpay Checkout helper (Phase 7).

POST /payment creates a real order, then blocks polling for up to 240s for
a human to complete Razorpay Checkout out-of-band -- see payment.py's
module docstring for why this has to work this way (Razorpay has no
server-to-server payment-completion API; even test mode requires a human
to click through the mock bank page). This module is the reusable half of
that dance, proven manually during Phase 5's de-risking and now callable
by demo_agent.py on every run instead of being a one-off scratch script:

  1. Fire the blocking POST /payment call in a background thread.
  2. Meanwhile, poll Razorpay's own Orders API for the order that call
     just created (matched by the agent_id/item_id in its notes -- the
     same trick used manually in Phase 5).
  3. Serve the exact proven Checkout.js page (Phase 5's checkout_derisk.html,
     now templated) against that real order, on a local throwaway server.
  4. Print the checkout URL clearly so a human can click through in time.
  5. Wait for step 1 to resolve and return its result.

If the payment call blocks on a mandate check (spend cap / category
scope) before ever calling payment.create_order(), no order ever appears
on Razorpay -- this is detected and reported rather than waited out.
"""

import http.server
import os
import threading
import time
import webbrowser

import requests
from dotenv import load_dotenv

import console_ui as ui
from payment import get_client

load_dotenv()

# The exact page proven working in Phase 5's manual de-risking
# (checkout_derisk.html / scenario1_checkout.html) -- only the key,
# amount, description and order_id are now templated.
CHECKOUT_HTML_TEMPLATE = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Time & Co. -- Checkout</title></head>
<body>
<h3>{description}</h3>
<button id="rzp-button1">Pay</button>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
var options = {{
    "key": "{key_id}",
    "amount": "{amount_paise}",
    "currency": "INR",
    "name": "Time & Co.",
    "description": "{description}",
    "order_id": "{order_id}",
    "handler": function (response){{
        document.title = "PAYMENT_SUCCESS";
        document.body.innerHTML = "<pre id='result'>" + JSON.stringify(response, null, 2) + "</pre>";
    }},
    "modal": {{
        "ondismiss": function() {{
            document.title = "MODAL_DISMISSED";
        }}
    }},
    "theme": {{ "color": "#cc785c" }}
}};
var rzp1 = new Razorpay(options);
rzp1.on('payment.failed', function (response){{
    document.title = "PAYMENT_FAILED";
    document.body.innerHTML = "<pre id='result'>" + JSON.stringify(response.error, null, 2) + "</pre>";
}});
document.getElementById('rzp-button1').onclick = function(e){{
    rzp1.open();
    e.preventDefault();
}}
</script>
</body>
</html>
"""


def _find_order_once(agent_id: str, item_id: int, since_ts: float) -> dict | None:
    """Look for the most recent Razorpay order matching this agent_id/item_id
    (as stored in payment.create_order()'s notes), created at or after
    since_ts so a stale order from an earlier run isn't matched by mistake.
    """
    client = get_client()
    orders = client.order.all({"count": 10}).get("items", [])
    for order in orders:
        notes = order.get("notes") or {}
        if (
            str(notes.get("agent_id")) == str(agent_id)
            and str(notes.get("item_id")) == str(item_id)
            and order.get("created_at", 0) >= int(since_ts) - 2  # small clock-skew allowance
        ):
            return order
    return None


def _serve_checkout(order: dict, description: str) -> str:
    """Serve the proven Checkout.js page for `order` on a local throwaway
    HTTP server (daemon thread, OS-assigned free port so concurrent runs
    never collide). Returns the URL to open."""
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    if not key_id:
        raise RuntimeError("RAZORPAY_KEY_ID not set")

    html = CHECKOUT_HTML_TEMPLATE.format(
        key_id=key_id,
        amount_paise=order["amount"],
        description=description,
        order_id=order["id"],
    ).encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, fmt, *args):
            pass  # keep the demo's terminal output clean

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/"


def pay_with_checkout(
    payload: dict,
    base_url: str = "http://localhost:8000",
    order_lookup_timeout: float = 60,
    request_timeout: float = 300,
) -> dict:
    """Fire POST /payment with `payload` (must include agent_id, item_id --
    used to find the resulting order), print a checkout link the moment a
    real order exists, and return {"status_code", "body"} once the request
    resolves (approved, blocked, or timed out).

    order_lookup_timeout keeps searching for the order for up to this long
    OR until the payment request itself finishes, whichever comes first --
    it is not a fixed short window disconnected from how long /payment
    actually takes. create_order() normally returns in well under a
    second, but a live run surfaced it occasionally taking noticeably
    longer than that on Razorpay's side; 60s gives real headroom without
    approaching poll_payment_status's own 240s ceiling.
    """
    since_ts = time.time()
    result_box: dict = {}
    done = threading.Event()

    def run_payment_request():
        try:
            resp = requests.post(f"{base_url}/payment", json=payload, timeout=request_timeout)
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            result_box["status_code"] = resp.status_code
            result_box["body"] = body
        except requests.RequestException as e:
            result_box["status_code"] = None
            result_box["body"] = {"error": str(e)}
        finally:
            done.set()

    thread = threading.Thread(target=run_payment_request, daemon=True)
    thread.start()

    order = None
    deadline = time.monotonic() + order_lookup_timeout
    while time.monotonic() < deadline and not done.is_set():
        order = _find_order_once(payload["agent_id"], payload["item_id"], since_ts)
        if order:
            break
        time.sleep(1)

    if order:
        # The order's existence is itself proof the mandate check already
        # passed -- payment.py only calls create_order() after that check
        # (see this module's docstring). Announce both stages now, in the
        # order they actually happened, instead of waiting for the whole
        # blocking call (which still has to wait on the human checkout
        # step) to resolve before the console says anything about them.
        ui.mandate_passed()
        ui.razorpay_order_created(order.get("id", "-"), order.get("amount"))

        description = f"Time & Co. -- item #{payload['item_id']} x{payload['quantity']}"
        url = _serve_checkout(order, description)
        ui.checkout_ready(url)
        try:
            # Best-effort convenience: the URL above is already the
            # source of truth and is printed regardless of whether this
            # succeeds (e.g. no GUI browser available on this machine).
            webbrowser.open(url)
        except Exception:
            pass
    elif not done.is_set():
        ui.checkout_note("(no matching Razorpay order appeared within the lookup window)")
    else:
        ui.checkout_note(
            "(payment call finished before any Razorpay order was created -- "
            "likely blocked by a mandate check before Razorpay was ever contacted)"
        )

    thread.join()
    return {
        "status_code": result_box.get("status_code"),
        "body": result_box.get("body"),
        # Lets the caller avoid re-announcing mandate-passed/order-created
        # when this function already did so above (the common case). Only
        # false in the rare case the order-lookup window expired before a
        # matching order was found, or no order was ever created at all.
        "order_announced": order is not None,
    }
