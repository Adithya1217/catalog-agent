"""
llm.py -- shared Gemini API wrapper.

Direct HTTP via requests, no SDK / agent framework. enrich.py (catalog
enrichment) and negotiate.py (negotiation explanations) both call this for
their LLM calls.

Model choice: gemini-3.8-flash, selected by measured availability rather
than spec-sheet reasoning -- Google's per-model capacity shifts, so the
"right" Flash model is whichever is actually answering. Calls retry with
backoff and then fall back through GEMINI_FALLBACK_MODELS. If the primary
model starts returning 503s, re-measure before switching.
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
