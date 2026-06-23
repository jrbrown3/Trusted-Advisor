"""
app/services/ai_service.py
──────────────────────────
Single AI integration point. All LLM calls route through here.

Design rules:
  - Never called directly from routers. Always via a feature-specific service.
  - Returns raw text or parsed dict. Validation happens in the caller via Pydantic.
  - Provider is determined by settings.llm_provider — no logic elsewhere.
  - Errors are raised as AIServiceError; caller decides how to surface them.
"""

import json
import logging
from typing import Any

import anthropic
import openai

from app.config import settings

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    """Raised when the LLM call fails or returns unusable output."""
    pass


# ── Lazy client initialisation ────────────────────────────────
# Clients are created once per process, not per request.

_claude_client: anthropic.AsyncAnthropic | None = None
_openai_client: openai.AsyncOpenAI | None = None


def _get_claude() -> anthropic.AsyncAnthropic:
    global _claude_client
    if _claude_client is None:
        if not settings.anthropic_api_key:
            raise AIServiceError("ANTHROPIC_API_KEY is not set in .env")
        _claude_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _claude_client


def _get_openai() -> openai.AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        if not settings.openai_api_key:
            raise AIServiceError("OPENAI_API_KEY is not set in .env")
        _openai_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


# ── Core completion function ──────────────────────────────────

async def complete(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
    expect_json: bool = False,
) -> str:
    """
    Send a completion request to the active LLM provider.

    Args:
        system_prompt:  Role/context instructions for the model.
        user_prompt:    The actual task or content to process.
        max_tokens:     Hard cap on response length.
        expect_json:    If True, instructs the model to return only valid JSON.

    Returns:
        Raw string response from the model.

    Raises:
        AIServiceError: On API failure or empty response.
    """
    if expect_json:
        system_prompt = (
            system_prompt.rstrip()
            + "\n\nIMPORTANT: Respond only with valid JSON. "
              "No preamble, no markdown fences, no explanation."
        )

    provider = settings.llm_provider
    logger.info(f"AI call → provider={provider} model={settings.active_model} expect_json={expect_json}")

    try:
        if provider == "claude":
            return await _complete_claude(system_prompt, user_prompt, max_tokens)
        elif provider == "openai":
            return await _complete_openai(system_prompt, user_prompt, max_tokens)
        else:
            raise AIServiceError(f"Unknown LLM_PROVIDER: {provider}")

    except AIServiceError:
        raise
    except Exception as e:
        raise AIServiceError(f"LLM call failed ({provider}): {e}") from e


async def _complete_claude(system: str, user: str, max_tokens: int) -> str:
    client = _get_claude()
    message = await client.messages.create(
        model=settings.llm_model_claude,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if not message.content:
        raise AIServiceError("Claude returned an empty response.")
    return message.content[0].text


async def _complete_openai(system: str, user: str, max_tokens: int) -> str:
    client = _get_openai()
    response = await client.chat.completions.create(
        model=settings.llm_model_openai,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise AIServiceError("OpenAI returned an empty response.")
    return content


# ── JSON helper ───────────────────────────────────────────────

async def complete_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
    retries: int = 2,
) -> dict[str, Any]:
    """
    Like complete(), but parses and returns a dict.
    Retries up to `retries` times on JSON parse failure.

    Raises:
        AIServiceError: If JSON cannot be parsed after all retries.
    """
    for attempt in range(1, retries + 1):
        raw = await complete(system_prompt, user_prompt, max_tokens, expect_json=True)
        try:
            # Strip accidental markdown fences (defensive)
            clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(clean)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed (attempt {attempt}/{retries}): {e}")
            if attempt == retries:
                raise AIServiceError(
                    f"Model returned invalid JSON after {retries} attempts. Raw: {raw[:200]}"
                ) from e
