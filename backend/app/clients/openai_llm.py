"""Real LLM extractor over an OpenAI-COMPATIBLE endpoint. OPTIONAL — the mock is the
default and needs none of this.

Only imported when DASFAX_LLM_PROVIDER=openai. The SDK import is lazy (see
`openai_compat.build_chat_client`) so the mock path never requires `openai` installed.
"openai" names the WIRE FORMAT: set DASFAX_OPENAI_BASE_URL to Gemini's or Groq's
OpenAI-compatible URL and this client is unchanged.

This is the extraction prompt WS5 owns. Iterate the prompt here; the interface and the
downstream pipeline do not change.
"""
from __future__ import annotations

from typing import Any

from app.clients.base import ExtractedClaim, LLMClient
from app.clients.openai_compat import build_chat_client, chat_json, loads_object
from app.config import Settings

_SYSTEM = (
    "You are a fact-checking assistant that extracts CHECKABLE CLAIMS from a news article. "
    "A checkable claim is a specific, verifiable assertion about the world (who did what, "
    "when, how much, statistics, events). Do NOT extract opinions, value judgements, "
    "rhetorical questions, or predictions about the future as 'factual'. "
    "Rewrite each claim as a single self-contained sentence that makes sense without the "
    "surrounding article (resolve pronouns). "
    # Anchoring guard: services/anchoring.py has to find each claim back in the source
    # text (Dice similarity, DASFAX_ANCHOR_MIN_SIMILARITY) so WS2 can highlight it in the
    # DOM. A claim fused from three sentences, or paraphrased freely, anchors to nothing.
    "Base each claim on a SINGLE source sentence and keep the wording as close to the "
    "original as possible, changing only what is needed to make it self-contained. "
    "Return STRICT JSON only."
)

_USER_TEMPLATE = (
    "Article title: {title}\n\n"
    'Article text:\n"""\n{text}\n"""\n\n'
    "Return JSON of the form:\n"
    '{{"claims": [{{"text": "<self-contained claim>", '
    '"claim_type": "factual|opinion|prediction", '
    '"checkworthiness": <0.0-1.0, how important/verifiable this claim is>}}]}}\n'
    "Extract at most 15 candidate claims. Prefer specific, load-bearing factual claims."
)

_CLAIM_TYPES = {"factual", "opinion", "prediction"}


class OpenAILLMClient(LLMClient):
    name = "openai"

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        # `client` is an injection seam for tests: pass a stub and no key, SDK or network
        # is needed. Production always goes through build_chat_client.
        self._client = client if client is not None else build_chat_client(
            settings, env_var="DASFAX_LLM_PROVIDER"
        )
        self._model = settings.openai_model
        self.name = f"openai:{settings.openai_model}"

    def extract_claims(self, *, title: str | None, text: str) -> list[ExtractedClaim]:
        raw = chat_json(
            self._client,
            model=self._model,
            system=_SYSTEM,
            user=_USER_TEMPLATE.format(title=title or "(none)", text=text[:12000]),
        )
        data = loads_object(raw)          # {} for unparseable output -> no claims, no crash
        rows = data.get("claims")
        if not isinstance(rows, list):
            return []

        out: list[ExtractedClaim] = []
        for c in rows:
            if not isinstance(c, dict):
                continue
            try:
                claim_text = str(c["text"]).strip()
                if not claim_text:
                    continue
                claim_type = str(c.get("claim_type", "factual")).strip().lower()
                out.append(
                    ExtractedClaim(
                        text=claim_text,
                        claim_type=claim_type if claim_type in _CLAIM_TYPES else "factual",
                        checkworthiness=min(1.0, max(0.0, float(c.get("checkworthiness", 0.5)))),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue  # skip malformed rows; never let bad model output crash WS5
        return out
