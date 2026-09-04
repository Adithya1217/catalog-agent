"""
llm.py -- shared Gemini API wrapper (Phase 3/4).

Direct HTTP via requests, no SDK / agent framework, per locked tech stack.
Both enrich.py (Phase 3 enrichment) and negotiate.py (Phase 4 negotiation
explanations) call this for their underlying LLM calls, so it's a shared
module rather than living inside either one.

Model choice: gemini-3.5-flash-lite. Originally picked gemini-3.6-flash on
paper (Google's current general-purpose Flash model), but empirical dry
runs against the real Phase 3 prompt (2026-09-04) showed 3.6-flash
returning HTTP 503 "experiencing high demand" on 2 of 3 test calls, one
taking 66s to fail and requiring a retry to succeed at all. flash-lite
answered all 3 calls (including both deliberately-ambiguous items) in
1.4-2.7s each, with reasoning quality and ambiguity-handling as good as
3.6-flash's -- e.g. correctly used the price-range midpoint AND flagged
the stock/description contradiction for item #15, exactly per the
prompt's instructions. gemini-2.5-flash is no longer available to new API
keys at all (confirmed via a live 404 pointing callers at 3.6-flash).
gemini-3.7-flash/3.8-flash remain unevaluated -- pitched specifically at
long-horizon coding/agentic workflows, not a fit for short structured-
extraction and one-line-explanation calls, so not worth testing here.
Reconsider if flash-lite's reasoning quality ever visibly degrades on the
full 15-item catalog -- 3.6-flash is a one-line swap back if so.

Rate limits: ai.google.dev no longer publishes exact free-tier RPM/RPD
numbers per model in its public docs -- it now points to a login-gated
AI Studio dashboard (aistudio.google.com/rate-limit) that this session
can't check on your behalf. Not independently confirmed as a hard number;
worth a 30-second look there once you have a key, though this project's
~30-40 total calls (15 enrichment + a couple dozen negotiation-explain
calls) is comfortably within what free-tier Flash models have historically
allowed.
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL = "gemini-3.5-flash-lite"


def call_gemini(prompt: str, json_mode: bool = False) -> str:
    """Call Gemini's generateContent endpoint with a single-turn text prompt.

    Set json_mode=True to ask Gemini to constrain output to syntactically
    valid JSON (via generationConfig.responseMimeType) -- used by
    enrich_item(), which expects a JSON object back. Leave it False for
    plain-text output -- used by explain_decision(), which expects one
    plain-English sentence, not JSON.

    Returns the model's raw text output. Raises requests.HTTPError on a
    non-2xx response (e.g. missing/invalid GEMINI_API_KEY, rate limit),
    and RuntimeError if no API key is configured at all.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_mode:
        payload["generationConfig"] = {"responseMimeType": "application/json"}

    resp = requests.post(
        GEMINI_API_URL.format(model=GEMINI_MODEL),
        headers={
            "x-goog-api-key": api_key,
            "content-type": "application/json",
        },
        json=payload,
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]
