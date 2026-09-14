"""
Azure OpenAI provider adapter.

Talks to an Azure OpenAI chat-completions deployment over REST (httpx) and
returns clean JSON text. Modeled on the proven adapter pattern from the
pcp-document-text-extractor service (fence-stripping + parse, config-driven).

All Azure-specific knobs (endpoint, deployment, key, api-version, temperature,
JSON mode, retry) live here, hidden behind the LLMProvider interface.
"""

from __future__ import annotations

import asyncio

import httpx

from app.providers.base import Attachment, LLMError, LLMProvider


class AzureOpenAIProvider(LLMProvider):
    """LLMProvider backed by an Azure OpenAI chat-completions deployment."""

    name = "azure_openai"
    # Azure OpenAI (GPT-4o / GPT-5.x) reads image/document parts natively; we
    # keep text-only here for v1 and enable document input when wired in doc mode.
    supports_document_input = False
    supports_json_mode = True

    def __init__(
        self,
        endpoint: str,
        deployment: str,
        api_key: str,
        api_version: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 8000,
        timeout_seconds: float = 90.0,
        max_retries: int = 2,
    ) -> None:
        if not endpoint or not deployment or not api_key:
            raise LLMError(
                "Azure OpenAI provider is not configured: "
                "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT and "
                "AZURE_OPENAI_API_KEY are all required."
            )
        self._endpoint = endpoint.rstrip("/")
        self._deployment = deployment
        self._api_key = api_key
        self._api_version = api_version
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout_seconds
        self._max_retries = max_retries

    @property
    def _url(self) -> str:
        return (
            f"{self._endpoint}/openai/deployments/{self._deployment}"
            f"/chat/completions?api-version={self._api_version}"
        )

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        attachments: list[Attachment] | None = None,
    ) -> str:
        """Call the deployment and return clean JSON text."""
        payload: dict = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        # Native JSON mode: ask the model to emit a JSON object.
        if self.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"api-key": self._api_key, "Content-Type": "application/json"}

        raw = await self._post_with_retry(payload, headers)
        content = self._extract_content(raw)
        # Normalise to clean JSON text for the validation layer.
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
                    # Retry on rate-limit / transient server errors only.
                    if resp.status_code in (429, 500, 502, 503, 504):
                        last_exc = LLMError(
                            f"Azure OpenAI transient error {resp.status_code}: {resp.text[:300]}"
                        )
                    else:
                        raise LLMError(
                            f"Azure OpenAI error {resp.status_code}: {resp.text[:300]}"
                        )
                except httpx.RequestError as exc:
                    last_exc = LLMError(f"Azure OpenAI request failed: {exc}")

                if attempt < self._max_retries:
                    await asyncio.sleep(2**attempt)  # 1s, 2s, ...

        raise last_exc or LLMError("Azure OpenAI call failed after retries.")

    @staticmethod
    def _extract_content(raw: dict) -> str:
        """Pull the assistant message text out of the chat-completions envelope."""
        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected Azure OpenAI response shape: {exc}") from exc
        if not content or not content.strip():
            raise LLMError("Azure OpenAI returned empty content.")
        return content
