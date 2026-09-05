"""
llm.py -- shared Gemini API wrapper (Phase 3/4).

Direct HTTP via requests, no SDK / agent framework, per locked tech stack.
Both enrich.py (Phase 3 enrichment) and negotiate.py (Phase 4 negotiation
explanations) call this for their underlying LLM calls, so it's a shared
module rather than living inside either one.

Model choice: gemini-3.8-flash. This has moved twice, both times driven by
measured availability rather than spec-sheet reasoning -- Google's
per-model capacity shifts, so the "right" Flash model is whichever is
actually answering.

  * Phase 3 (2026-09-04, morning): picked gemini-3.5-flash-lite after
    gemini-3.6-flash returned HTTP 503 "experiencing high demand" on 2 of
    3 dry-run calls. flash-lite answered all 3 in 1.4-2.7s with equal
    reasoning quality on the deliberately-ambiguous items.
  * Phase 8 (same day, evening): the congestion inverted. Measured 5
    attempts per model with the real tool-calling payload --
    3.5-flash-lite 1/5 (503s and 30s+ timeouts), 3.6-flash 5/5,
    3.8-flash 5/5 and fastest (1.1-15.3s). Switched to 3.8-flash, which
    Google also positions for agentic/tool-use workflows -- what
    demo_agent.py actually does. Confirmed "Free of charge" on the
    standard tier, same as 3.6-flash.

gemini-2.5-flash is not available to new API keys at all (confirmed via a
live 404 pointing callers at 3.6-flash). If 3.8-flash starts 503ing,
re-measure before switching -- 3.6-flash is the one-line fallback.

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
import time

import requests
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL = "gemini-3.8-flash"

# Tried in order when the primary is refusing. All measured 5/5 or better at
# some point on 2026-09-04; which one is healthy shifts through the day, so
# the fallback exists to ride out a single model's congestion rather than to
# express a quality preference.
GEMINI_FALLBACK_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]


def request_generate_content(payload: dict, timeout: float = 90, attempts: int = 3) -> dict:
    """POST `payload` to generateContent, retrying transient failures.

    Transport resilience only -- it does not alter the request body, the
    prompt, or anything the model is asked to decide. Gemini's Flash models
    intermittently return HTTP 503 "experiencing high demand" (measured as
    bad as 1-in-5 success on 2026-09-04), which would otherwise crash a
    live demo mid-run. Retries the primary model with backoff, then falls
    back through GEMINI_FALLBACK_MODELS. Raises the last error if every
    model and attempt fails.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    headers = {"x-goog-api-key": api_key, "content-type": "application/json"}
    last_error: Exception | None = None

    for model in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]:
        for attempt in range(attempts):
            try:
                resp = requests.post(
                    GEMINI_API_URL.format(model=model),
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    return resp.json()
                # 503/429/5xx are transient; anything else is a real error
                # worth surfacing immediately rather than masking by retrying.
                if resp.status_code not in (429, 500, 502, 503, 504):
                    resp.raise_for_status()
                last_error = requests.HTTPError(
                    f"{resp.status_code} from {model}: {resp.text[:200]}", response=resp
                )
            except requests.RequestException as e:
                last_error = e
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))  # 2s, 4s

    raise last_error if last_error else RuntimeError("generateContent failed with no error recorded")


def call_gemini(prompt: str, json_mode: bool = False) -> str:
    """Call Gemini's generateContent endpoint with a single-turn text prompt.

    Set json_mode=True to ask Gemini to constrain output to syntactically
    valid JSON (via generationConfig.responseMimeType) -- used by
    enrich_item(), which expects a JSON object back. Leave it False for
    plain-text output -- used by explain_decision(), which expects one
    plain-English sentence, not JSON.

    Returns the model's raw text output. Transient 5xx/429 responses are
    retried across models by request_generate_content(); a persistent
    failure raises, as does a missing GEMINI_API_KEY.
    """
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_mode:
        payload["generationConfig"] = {"responseMimeType": "application/json"}

    data = request_generate_content(payload)
    return data["candidates"][0]["content"]["parts"][0]["text"]
