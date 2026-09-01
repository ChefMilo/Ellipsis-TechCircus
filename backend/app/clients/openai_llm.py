"""Real LLM extractor (OpenAI). OPTIONAL — the mock is the default and needs none of this.

Only imported when DASFAX_LLM_PROVIDER=openai. Kept behind a lazy import so the mock
path never requires the `openai` package to be installed.

This is the extraction prompt WS5 owns. Iterate the prompt here; the interface and the
downstream pipeline do not change.
"""
from __future__ import annotations

import json

from app.clients.base import ExtractedClaim, LLMClient
from app.config import Settings

_SYSTEM = (
    "You are a fact-checking assistant that extracts CHECKABLE CLAIMS from a news article. "
    "A checkable claim is a specific, verifiable assertion about the world (who did what, "
    "when, how much, statistics, events). Do NOT extract opinions, value judgements, "
    "rhetorical questions, or predictions about the future as 'factual'. "
    "Rewrite each claim as a single self-contained sentence that makes sense without the "
    "surrounding article (resolve pronouns). Return STRICT JSON only."
)

_USER_TEMPLATE = (
    "Article title: {title}\n\n"
    "Article text:\n\"\"\"\n{text}\n\"\"\"\n\n"
    "Return JSON of the form:\n"
    '{{"claims": [{{"text": "<self-contained claim>", '
    '"claim_type": "factual|opinion|prediction", '
    '"checkworthiness": <0.0-1.0, how important/verifiable this claim is>}}]}}\n'
    "Extract at most 15 candidate claims. Prefer specific, load-bearing factual claims."
)


class OpenAILLMClient(LLMClient):
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("DASFAX_LLM_PROVIDER=openai but OPENAI_API_KEY is not set.")
        # Lazy import: only needed on the real path.
        from openai import OpenAI  # type: ignore

        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model
        self.name = f"openai:{settings.openai_model}"

    def extract_claims(self, *, title: str | None, text: str) -> list[ExtractedClaim]:
        resp = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _USER_TEMPLATE.format(title=title or "(none)", text=text[:12000])},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        out: list[ExtractedClaim] = []
        for c in data.get("claims", []):
            try:
                out.append(
                    ExtractedClaim(
                        text=str(c["text"]).strip(),
                        claim_type=str(c.get("claim_type", "factual")).lower(),
                        checkworthiness=float(c.get("checkworthiness", 0.5)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue  # skip malformed rows; never let bad model output crash WS5
        return out
