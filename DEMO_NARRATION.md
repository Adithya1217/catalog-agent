# Failure-path demo: narration for both outcomes

The failure-path run always uses the same setup — a buy request for 150x
"sticky notes asstd colors" (item 3, ₹120 each = ₹18,000 order total)
under mandate 1 (₹15,000 spend cap). What's *not* scripted is whether the
agent asks for a discount before checking out. That's a live model
decision, so the run can stop at either of two different guardrails.
Both are real, both are already verified in the audit log. Say whichever
one happens like you meant it to happen — because either way, it proves
the same point: guardrails hold at every layer, not just one.

## Outcome A — negotiation guardrail fires first

The agent asks for a discount above 5% (it asked for 10% in the last
run). `negotiate.py`'s `evaluate_offer()` is a hardcoded rule, not an LLM
call — it declines outright because the request exceeds
`max_discount_pct`. The order never even reaches a mandate check, because
there's no approved price yet to check.

**Say:** "The agent tried to negotiate — asked for 10% off. That's a
deterministic guardrail, not the model being nice: negotiation logic is
plain Python, so the discount ceiling can't be talked around no matter
how the agent phrases the ask. It's blocked before the order is even
priced."

Audit log line to point to: `action=negotiate_offer, decision=blocked,
reason="... exceeds your maximum allowed limit of 5%..."`

## Outcome B — mandate guardrail fires first

The agent doesn't ask for a discount (or the discount still leaves the
total over the cap). The full ₹18,000 — or a discounted ₹17,100 at 5%
off — still exceeds the ₹15,000 mandate spend cap, so `mandate_check`
blocks it before `payment.create_order()` is ever called. No Razorpay
order is created at all for this path.

**Say:** "This one got past negotiation but hit the actual spend cap on
the buyer's mandate — ₹18,000 against a ₹15,000 limit. That check runs
right before the agent would touch real money, and it's a hard stop: no
Razorpay order gets created, nothing touches the payment rail."

Audit log line to point to: `action=mandate_check, decision=blocked,
reason="Requested amount ... exceeds mandate 1 spend cap"`

## One-line bridge if you need to explain why it's not always the same

"Which guardrail catches it depends on what the agent decides to ask
for — that's live model behavior, not scripted — but every path through
this system passes through both checks, so nothing gets through
blocked-by-neither."

## Reminder: this is the failure path, not the whole demo

Always be ready to follow either blocked outcome with the happy path
(same mandate, an order that clears both negotiation and the spend cap,
ending in a real captured Razorpay test payment) so judges see the
system succeed as well as correctly refuse.
