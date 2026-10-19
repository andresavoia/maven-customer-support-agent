"""Shop model via OpenRouter. The agents never call AI Studio directly."""

from __future__ import annotations

import os

from google.adk.features import FeatureName, override_feature_enabled
from google.adk.models.lite_llm import LiteLlm

from support.config import MODEL

# Gemini on OpenRouter rejects ADK's JSON-schema function declarations
# (finish_reason error / MALFORMED_FUNCTION_CALL, empty content). The older
# Schema object serializes to a plain parameters object the model can call.
override_feature_enabled(FeatureName.JSON_SCHEMA_FOR_FUNC_DECL, False)


def agent_model() -> LiteLlm:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OpenRouter is not configured: OPENROUTER_API_KEY is missing")
    return LiteLlm(model=f"openrouter/{MODEL}", api_key=api_key, drop_params=True)
