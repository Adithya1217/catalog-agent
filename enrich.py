"""
enrich.py -- LLM enrichment pipeline (Phase 3).

Raw SKU -> clean description, category, tags, numeric price. Must return
JSON + a one-line reasoning string suitable for the audit log.

STATUS: live. ENRICHMENT_PROMPT approved after a 3-item dry run (items
#1, #14, #15) against gemini-3.5-flash-lite -- see llm.py for the model
choice rationale.
"""

import json

from llm import call_gemini

ENRICHMENT_PROMPT = """You are helping Time & Co., a small stationery and office-supplies shop in
Chennai, turn one raw, messy product listing into clean structured data for
an AI-readable catalog. The shop's listings were typed up over years by the
owner in whatever format he was in a hurry with -- expect inconsistent
casing, abbreviations, stray units, and currency symbols.

Given the raw SKU fields below, return ONLY a single JSON object (no markdown
fences, no commentary before or after) shaped exactly like this:

{{
  "clean_description": string,
  "category": string,
  "tags": [string, ...],
  "price": number,
  "reasoning": string,
  "confidence": "inferred" | "rule-matched" | "explicit"
}}

Field rules:

- clean_description: One clear, merchant-neutral sentence describing the
  product, written for a buyer agent that has never seen the raw text.
  Expand abbreviations (e.g. "blk" -> "black", "asstd" -> "assorted").

- category: A short lowercase category path, e.g. "stationery/writing" or
  "stationery/organization". Pick the most specific defensible category.

- tags: 2-5 lowercase keyword tags a buyer agent might filter/search on
  (material, use case, color, pack type -- not duplicates of the category).

- price: A single numeric value (float), in rupees, for ONE sellable unit
  as the shop would ring it up at checkout.
    - Strip currency symbols and formatting: "₹45" -> 45.0, "Rs. 85/-" -> 85.0,
      "MRP 350" -> 350.0.
    - If price_raw gives a per-piece price but terms say items are sold as a
      pack/dozen/box (e.g. "₹8/pc, sold dozen only"), report the price for
      the actual sellable unit -- here, price for one dozen: 8 * 12 = 96.0.
      Explain that arithmetic in `reasoning`.
    - If price_raw is a range (e.g. "Rs. 40-65 depending on design"), you
      cannot report a single true price. Use the midpoint as your best-guess
      numeric value, but say plainly in `reasoning` that this is a midpoint
      of a stated range, not a fixed price, and set confidence to
      "inferred".

- reasoning: ONE plain-English sentence explaining your key judgment call
  for this item -- what you inferred and from what signal. This is shown
  directly to the merchant on their dashboard, so write for a shop owner,
  not a developer.

- confidence:
    - "explicit"     - the raw data stated this directly, no guessing needed.
    - "rule-matched" - you applied a clear, mechanical rule (e.g. currency
                        stripping, per-dozen math) to get a firm answer.
    - "inferred"      - you had to guess or resolve a genuine ambiguity in
                        the raw data.

Handling ambiguous listings -- do NOT silently pick an answer and hide the
ambiguity. Some listings in this catalog are deliberately incomplete or
contradictory. When you encounter one:
  - Still return your best-effort values for every field (never omit a
    field or return null).
  - Use `reasoning` to name the specific ambiguity or contradiction you
    found, and explain what assumption you made to resolve it.
  - Set confidence to "inferred" whenever you had to resolve a real
    ambiguity, even if your guess feels reasonable.

Two examples of what "naming the ambiguity" looks like in practice:
  - A combo/mixed-item listing with no fixed contents or single category:
    pick the most defensible broad category (e.g. "stationery/assorted"),
    and say in `reasoning` that contents vary and a single category is an
    approximation, not a fact about a fixed product.
  - A listing whose description says an item is unavailable/awaiting stock
    while the stock count field shows a nonzero number: do not silently
    trust one field over the other. Note the contradiction in `reasoning`
    (e.g. "description says stock is awaited but stock=18 is recorded;
    treated the numeric stock field as current and flagged the mismatch")
    and pick one interpretation, explaining why.

A field value of "(not provided)" below means the raw listing genuinely
omitted that field -- it is not itself a piece of product information.
Do not describe the product as being "not provided" or treat that string
as real data; just work with whatever fields ARE given.

Raw SKU:
  name: {raw_name}
  description: {raw_description}
  price_raw: {price_raw}
  stock: {stock}
  terms: {terms}

Return ONLY the JSON object."""

_REQUIRED_KEYS = {"clean_description", "category", "tags", "price", "reasoning", "confidence"}


def _fmt(value) -> str:
    return "(not provided)" if value is None else str(value)


def enrich_item(
    raw_name: str,
    raw_description: str | None,
    price_raw: str | None,
    stock: int | None = None,
    terms: str | None = None,
) -> dict:
    """Call Gemini to enrich a single raw SKU.

    Returns a dict shaped like:
        {
            "clean_description": str,
            "category": str,
            "tags": list[str],
            "price": float,
            "reasoning": str,          # one-line, plain English
            "confidence": str,          # "inferred" | "rule-matched" | "explicit"
        }

    Raises on malformed LLM output rather than guessing -- callers decide
    how to log/handle a failed enrichment.
    """
    prompt = ENRICHMENT_PROMPT.format(
        raw_name=raw_name,
        raw_description=_fmt(raw_description),
        price_raw=_fmt(price_raw),
        stock=_fmt(stock),
        terms=_fmt(terms),
    )
    raw_output = call_gemini(prompt, json_mode=True)

    try:
        result = json.loads(raw_output)
    except json.JSONDecodeError as e:
        raise ValueError(f"enrich_item: model did not return valid JSON for {raw_name!r}: {raw_output!r}") from e

    missing = _REQUIRED_KEYS - result.keys()
    if missing:
        raise ValueError(f"enrich_item: model output missing keys {missing} for {raw_name!r}: {result!r}")

    if result["confidence"] not in ("inferred", "rule-matched", "explicit"):
        raise ValueError(
            f"enrich_item: unexpected confidence value {result['confidence']!r} for {raw_name!r}"
        )

    return result
