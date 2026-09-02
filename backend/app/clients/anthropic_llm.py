"""Native Anthropic claim extractor (WS5 "read"). OPTIONAL — the mock is the default.

Only imported when DASFAX_LLM_PROVIDER=anthropic. Needs ANTHROPIC_API_KEY and nothing
else; the SDK import is lazy (see anthropic_compat), so the mock path never requires it.

Structurally a mirror of openai_llm.py: same WS5 extraction guidance (including the
single-source-sentence anchoring instruction, which tests pin across both providers), same
robustness contract — malformed rows are dropped, never raised. The one real difference is
how strict output is obtained: a forced `tool_use` against an input_schema rather than
OpenAI's JSON mode.
"""
from __future__ import annotations

from typing import Any

from app.clients.anthropic_compat import build_anthropic_client, json_via_tool
from app.clients.base import ExtractedClaim, LLMClient
from app.config import Settings

_MAX_TOKENS = 4096          # room for the 15 claims the prompt allows

# WS5's extraction prompt. Kept deliberately equivalent to openai_llm.py::_SYSTEM so an
# A/B between providers compares MODELS, not prompts; the only divergence is the closing
# output instruction, because this client asks for a tool call rather than raw JSON.
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
    "Report your answer by calling the record_claims tool."
)

_USER_TEMPLATE = (
    "Article title: {title}\n\n"
    'Article text:\n"""\n{text}\n"""\n\n'
    "Extract at most 15 candidate claims. Prefer specific, load-bearing factual claims."
)

_EXTRACT_TOOL: dict[str, Any] = {
    "name": "record_claims",
    "description": "Record the checkable claims extracted from the article.",
    "input_schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The claim as a single self-contained sentence.",
                        },
                        "claim_type": {
                            "type": "string",
                            "enum": ["factual", "opinion", "prediction"],
                        },
                        "checkworthiness": {
                            "type": "number",
                            "description": "0.0-1.0: how important and verifiable this claim is.",
                        },
                    },
                    "required": ["text", "claim_type", "checkworthiness"],
                },
            }
        },
        "required": ["claims"],
    },
}

_CLAIM_TYPES = {"factual", "opinion", "prediction"}


class AnthropicLLMClient(LLMClient):
    name = "anthropic"

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        # `client` is an injection seam for tests: pass a stub and no key, SDK or network
        # is needed. Production always goes through build_anthropic_client.
        self._client = client if client is not None else build_anthropic_client(
            settings, env_var="DASFAX_LLM_PROVIDER"
        )
        self._model = settings.anthropic_model
        self.name = f"anthropic:{settings.anthropic_model}"

    def extract_claims(self, *, title: str | None, text: str) -> list[ExtractedClaim]:
        data = json_via_tool(
            self._client,
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            user=_USER_TEMPLATE.format(title=title or "(none)", text=text[:12000]),
            tool=_EXTRACT_TOOL,
        )
        rows = data.get("claims")
        if not isinstance(rows, list):
            return []           # no usable answer -> no claims, no crash

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
