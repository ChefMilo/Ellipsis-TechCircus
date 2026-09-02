"""Standalone diagnostic for the OpenAI web-search path.

The real client (app/clients/openai_search.py) swallows ALL errors to [] by design, so a
bad key / missing model / no-web-search-access all look identical: "no evidence". This
script makes the RAW call with NO swallowing, so you see the real exception, and prints
what came back so you can tell whether web search + citations actually work on YOUR key.

Run from backend/:
    pip install openai                 # required for the real path anyway
    $env:OPENAI_API_KEY="sk-..."
    .\.venv\Scripts\python.exe diagnose_openai_search.py
Optionally test a specific model:
    $env:DASFAX_OPENAI_SEARCH_MODEL="gpt-4.1"
"""
from __future__ import annotations

import os
import sys

QUERY = "Singapore scam losses 2024 Monetary Authority of Singapore figures"


def main() -> None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("FAIL: OPENAI_API_KEY is not set in this shell.")
        sys.exit(1)

    try:
        from openai import OpenAI
    except ImportError:
        print("FAIL: the `openai` package isn't installed. Run:  pip install openai")
        sys.exit(1)

    base_url = os.environ.get("DASFAX_OPENAI_BASE_URL")  # should be UNSET for OpenAI search
    if base_url:
        print(f"WARNING: DASFAX_OPENAI_BASE_URL={base_url!r} is set. OpenAI web search only")
        print("         works on the real OpenAI endpoint — a Gemini/Groq URL fails here.")
        print("         Unset it for this test.\n")

    model = os.environ.get("DASFAX_OPENAI_SEARCH_MODEL", "gpt-4o")
    client = OpenAI(api_key=key, base_url=base_url or None)
    print(f"Testing OpenAI web search: model={model!r}\nquery={QUERY!r}\n")

    for tool_type in ("web_search", "web_search_preview"):
        print(f"--- attempt: tools=[{{'type': '{tool_type}'}}] ---")
        try:
            resp = client.responses.create(
                model=model,
                instructions="Find independent, primary sources for the query. Cite them.",
                input=QUERY,
                tools=[{"type": tool_type}],
            )
        except Exception as e:  # we WANT to see the real error here
            print(f"  ERROR ({type(e).__name__}): {e}\n")
            continue

        citations = []
        for item in getattr(resp, "output", []) or []:
            if getattr(item, "type", None) != "message":
                continue
            for part in getattr(item, "content", []) or []:
                if getattr(part, "type", None) != "output_text":
                    continue
                text = getattr(part, "text", "") or ""
                for ann in getattr(part, "annotations", []) or []:
                    if getattr(ann, "type", None) == "url_citation":
                        s, e = getattr(ann, "start_index", 0), getattr(ann, "end_index", 0)
                        snip = text[s:e] if 0 <= s < e <= len(text) else ""
                        citations.append((getattr(ann, "url", ""),
                                          getattr(ann, "title", ""), snip))

        print(f"  OK: call succeeded on '{tool_type}'. citations found: {len(citations)}")
        for i, (url, title, snip) in enumerate(citations[:5], 1):
            print(f"    [{i}] {title or '(no title)'}")
            print(f"        {url}")
            print(f"        snippet: {snip[:160]!r}")
        if not citations:
            print("  NOTE: call worked but returned NO citations — evidence would be empty.")
        print("\nVERDICT: web search WORKS on your key. You're good — the real client will")
        print(f"         use whichever tool name succeeded ('{tool_type}').")
        return

    print("VERDICT: web search did NOT work (both tool names failed above).")
    print("Likely: your key/tier has no web-search access, or the model is unavailable.")
    print("Fallback: DASFAX_SEARCH_PROVIDER=tavily + a free TAVILY_API_KEY (already built).")
    sys.exit(2)


if __name__ == "__main__":
    main()