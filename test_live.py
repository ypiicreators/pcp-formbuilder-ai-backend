"""
Live end-to-end tester for the PCP AI Form Builder edit endpoint.

Drives the running server's POST /api/form-ai/generate with REAL forms from the
serviceJson corpus and several kinds of prompt, then prints readable results:
the outcome (proposal / clarification / error), what changed (diff), and any
warnings. This exercises the exact pipeline the frontend will use, including a
real Claude call.

USAGE
-----
1. Start the server in another terminal:
       .venv/bin/uvicorn app.main:app --reload --port 8000
2. Run this script:
       .venv/bin/python test_live.py
   Optional: point at a different server (ngrok / deployed):
       BASE_URL=https://abc123.ngrok.io .venv/bin/python test_live.py
   Optional: use a specific corpus form:
       FORM=incomeCertificate.json .venv/bin/python test_live.py

Nothing here needs the LLM key directly — the SERVER holds the key and makes the
Claude call. This script only speaks HTTP to the server.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

# --- Config (override via env) ---------------------------------------------

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
CORPUS = os.environ.get(
    "CORPUS",
    "/Users/yashashvipaliwal/Documents/GitHub/pcp-admin-portal/src/serviceJson",
)
FORM_NAME = os.environ.get("FORM", "residence.json")
TIMEOUT = float(os.environ.get("TIMEOUT", "120"))

# The prompts we send. Mix of: a simple edit, a dependency-bearing edit, an
# intentionally ambiguous one (should trigger clarification), and a broad one.
DEFAULT_PROMPTS = [
    "make certificate_no readonly",
    "make certificate_generate_status required",
    "make the state field mandatory",       # likely ambiguous in some forms
    "add a validator to certificate_no requiring at least 3 characters",
]

# --- Pretty printing --------------------------------------------------------

BAR = "=" * 72


def _print_header(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


def _print_diff(diff: list[dict]) -> None:
    if not diff:
        print("  (no diff entries)")
        return
    for d in diff:
        fid = d.get("fieldId")
        before = d.get("before")
        after = d.get("after")
        if before is None:
            print(f"  + ADDED   {fid}")
        elif after is None:
            print(f"  - REMOVED {fid}")
        else:
            # show which top-level props changed on the field
            changed = _changed_props(before, after)
            summary = ", ".join(
                f"{k}: {before.get(k)!r} -> {after.get(k)!r}" for k in changed
            ) or "(nested change)"
            print(f"  ~ CHANGED {fid}: {summary}")


def _changed_props(before: dict, after: dict) -> list[str]:
    keys = set(before) | set(after)
    return [k for k in keys if before.get(k) != after.get(k)]


def _health() -> bool:
    _print_header("OPTION 4 — HEALTH CHECK")
    try:
        r = httpx.get(f"{BASE_URL}/api/health", timeout=10)
    except httpx.RequestError as exc:
        print(f"  CANNOT REACH SERVER at {BASE_URL} -> {exc}")
        print("  Is the server running? Start it with:")
        print("    .venv/bin/uvicorn app.main:app --reload --port 8000")
        return False
    print(f"  GET {BASE_URL}/api/health -> {r.status_code}")
    print(f"  {r.text}")
    return r.status_code == 200


def _send_edit(base_form: dict, prompt: str) -> None:
    _print_header(f'EDIT PROMPT: "{prompt}"')
    data = {
        "mode": "edit_json",
        "prompt": prompt,
        "base_json": json.dumps(base_form),
    }
    try:
        r = httpx.post(f"{BASE_URL}/api/form-ai/generate", data=data, timeout=TIMEOUT)
    except httpx.RequestError as exc:
        print(f"  REQUEST FAILED: {exc}")
        return

    print(f"  HTTP {r.status_code}")
    try:
        body = r.json()
    except Exception:
        print(f"  (non-JSON body) {r.text[:500]}")
        return

    if r.status_code != 200:
        print(f"  ERROR body: {json.dumps(body, ensure_ascii=False)[:600]}")
        return

    status = body.get("status")
    if status == "needs_clarification":
        print(f"  STATUS: needs_clarification")
        print(f"  question: {body.get('question')}")
        for i, opt in enumerate(body.get("options", []), 1):
            print(f"    {i}. {opt['field_id']}  ({opt['label']})  [{opt['section_path']}]")
    elif status == "proposal":
        print(f"  STATUS: proposal")
        print("  DIFF:")
        _print_diff(body.get("diff") or [])
        warns = body.get("warnings") or []
        print(f"  warnings: {len(warns)}")
    else:
        print(f"  UNEXPECTED status: {status}")
        print(json.dumps(body, ensure_ascii=False)[:600])


def main() -> int:
    if not _health():
        return 1

    form_path = os.path.join(CORPUS, FORM_NAME)
    if not os.path.exists(form_path):
        print(f"\nForm not found: {form_path}")
        return 1
    with open(form_path) as f:
        base_form = json.load(f)

    _print_header(f"OPTION 3 — LIVE EDIT TESTS on {FORM_NAME}")
    print(f"  sections: {len(base_form.get('sections', []))}")

    prompts = DEFAULT_PROMPTS
    if len(sys.argv) > 1:
        # allow a single custom prompt: python test_live.py "make X required"
        prompts = [" ".join(sys.argv[1:])]

    for p in prompts:
        _send_edit(base_form, p)

    print(f"\n{BAR}\nDONE\n{BAR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
