"""
Anthropic (Claude) provider adapter.

Talks to the Anthropic Messages API over REST (httpx) and returns clean JSON
text. Mirrors the shape of the Azure adapter: all vendor knobs (model, version,
max tokens, retry/backoff) live here, hidden behind the LLMProvider interface.

Claude has no dedicated "JSON mode" like Azure's response_format, but it follows
a strong system instruction to emit JSON reliably; the base class's
strip_code_fences + parse_json recover the object if the model wraps it. The
orchestration layer stays identical regardless of provider.

Credentials: set ANTHROPIC_API_KEY in .env. Until then this adapter raises a
clear LLMError on construction, so the rest of the app stays importable and
testable with a fake/injected provider.
"""

from __future__ import annotations

import asyncio

import httpx

from app.providers.base import Attachment, LLMError, LLMProvider


class AnthropicProvider(LLMProvider):
    """LLMProvider backed by the Anthropic Messages API (Claude)."""

    name = "anthropic"
    # Claude 3.5+/4 can read documents/images natively; kept off for v1 (text
    # edit mode) and enabled when document mode is wired.
    supports_document_input = False
    # No separate JSON-mode flag; we instruct via the system prompt.
    supports_json_mode = False

    def __init__(
        self,
        api_key: str,
        model: str,
        api_version: str,
        base_url: str,
        *,
        max_tokens: int = 8000,
        temperature: float | None = None,
        timeout_seconds: float = 90.0,
        max_retries: int = 2,
    ) -> None:
        if not api_key:
            raise LLMError(
                "Anthropic provider is not configured: ANTHROPIC_API_KEY is required. "
                "Add it to .env to activate Claude."
            )
        self._api_key = api_key
        self._model = model
        self._api_version = api_version
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout_seconds
        self._max_retries = max_retries

    @property
    def _url(self) -> str:
        return f"{self._base_url}/v1/messages"

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        attachments: list[Attachment] | None = None,
    ) -> str:
        """Call the Messages API and return clean JSON text."""
        payload: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            # Anthropic takes `system` as a top-level field, not a message.
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
        }
        # `temperature` is deprecated/rejected on newer models (e.g. Sonnet 5),
        # so only send it when explicitly configured.
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._api_version,
            "content-type": "application/json",
        }

        raw = await self._post_with_retry(payload, headers)
        content = self._extract_content(raw)
        return self.strip_code_fences(content)

    async def _post_with_retry(self, payload: dict, headers: dict) -> dict:
        """POST with bounded retry + exponential backoff on transient errors."""
        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    resp = await client.post(self._url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        return resp.json()
                    if resp.status_code in (429, 500, 502, 503, 504, 529):
                        last_exc = LLMError(
                            f"Anthropic transient error {resp.status_code}: {resp.text[:300]}"
                        )
                    else:
                        raise LLMError(
                            f"Anthropic error {resp.status_code}: {resp.text[:300]}"
                        )
                except httpx.RequestError as exc:
                    last_exc = LLMError(f"Anthropic request failed: {exc}")

                if attempt < self._max_retries:
                    await asyncio.sleep(2**attempt)  # 1s, 2s, ...

        raise last_exc or LLMError("Anthropic call failed after retries.")

    @staticmethod
    def _extract_content(raw: dict) -> str:
        """
        Pull the assistant text out of the Messages envelope.

        Response shape: {"content": [{"type": "text", "text": "..."}], ...}.
        Concatenates all text blocks.
        """
        try:
            blocks = raw["content"]
            text = "".join(
                b.get("text", "") for b in blocks if b.get("type") == "text"
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise LLMError(f"Unexpected Anthropic response shape: {exc}") from exc
        if not text or not text.strip():
            raise LLMError("Anthropic returned empty content.")
        return text
