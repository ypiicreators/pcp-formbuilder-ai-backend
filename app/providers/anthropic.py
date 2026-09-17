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
import base64
import logging
import time

import httpx

from app.providers.base import Attachment, LLMError, LLMProvider

logger = logging.getLogger("app.providers.anthropic")

#: Content types Claude reads as a native base64 `document` block.
_DOCUMENT_TYPES = {"application/pdf"}
#: Content types Claude reads as a native base64 `image` block.
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
#: Content types forwarded inline as a plain-text block.
_TEXT_TYPES = {"text/plain"}


class AnthropicProvider(LLMProvider):
    """LLMProvider backed by the Anthropic Messages API (Claude)."""

    name = "anthropic"
    # Claude 3.5+/4 read PDFs, images, and text natively as content blocks.
    supports_document_input = True
    # No separate JSON-mode flag; we instruct via the system prompt.
    supports_json_mode = False
    # Supports SSE token streaming via generate_stream().
    supports_streaming = True

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
        payload = self._build_payload(system_prompt, user_prompt, attachments)
        headers = self._build_headers()

        started = time.monotonic()
        has_attachments = bool(attachments)
        raw = await self._post_with_retry(payload, headers)
        elapsed = time.monotonic() - started
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        logger.info(
            "Anthropic generate: model=%s attachments=%s took=%.1fs "
            "input_tokens=%s output_tokens=%s",
            self._model,
            has_attachments,
            elapsed,
            usage.get("input_tokens", "?"),
            usage.get("output_tokens", "?"),
        )
        content = self._extract_content(raw)
        return self.strip_code_fences(content)

    async def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        attachments: list[Attachment] | None = None,
    ):
        """
        Stream the Messages API response, yielding raw text fragments as they
        arrive (an async generator of `str`).

        Uses Claude's server-sent-events streaming (`"stream": true`). The API
        emits a sequence of events; the text lives in `content_block_delta`
        events with a `text_delta`. We yield each `text` piece verbatim — fence
        stripping and JSON parsing happen at the END, on the accumulated text,
        so this method stays a dumb passthrough.

        NOTE: no retry here. Retrying a partially consumed stream would replay
        fragments the caller already emitted downstream; the caller (document
        flow / route) surfaces a stream error instead. The non-streaming
        `generate()` still has retry for the edit/generate paths.
        """
        payload = self._build_payload(system_prompt, user_prompt, attachments)
        payload["stream"] = True
        headers = self._build_headers()

        started = time.monotonic()
        yielded_any = False
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST", self._url, json=payload, headers=headers
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        raise LLMError(
                            f"Anthropic stream error {resp.status_code}: {body[:300]}"
                        )
                    async for line in resp.aiter_lines():
                        text = self._parse_sse_line(line)
                        if text:
                            yielded_any = True
                            yield text
        except httpx.RequestError as exc:
            raise LLMError(f"Anthropic stream request failed: {exc}") from exc

        logger.info(
            "Anthropic generate_stream: model=%s attachments=%s took=%.1fs any=%s",
            self._model,
            bool(attachments),
            time.monotonic() - started,
            yielded_any,
        )
        if not yielded_any:
            raise LLMError("Anthropic stream returned no content.")

    @staticmethod
    def _parse_sse_line(line: str) -> str | None:
        """
        Extract the text fragment from one SSE line, or None if it carries no
        text.

        Anthropic SSE lines look like `data: {json}` (plus `event:` lines and
        blank separators we ignore). We only care about `content_block_delta`
        events whose delta is a `text_delta`.
        """
        if not line or not line.startswith("data:"):
            return None
        raw = line[len("data:"):].strip()
        if not raw or raw == "[DONE]":
            return None
        try:
            import json

            evt = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if evt.get("type") != "content_block_delta":
            return None
        delta = evt.get("delta") or {}
        if delta.get("type") == "text_delta":
            return delta.get("text") or None
        return None

    def _build_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        attachments: list[Attachment] | None,
    ) -> dict:
        """Assemble the Messages API request body (shared by both call paths)."""
        # With no attachments the content is a plain string (text-only edit/
        # generate). With attachments it becomes a content-block array: each file
        # rendered as a document/image/text block, then the instruction text last
        # so the model reads the file, then the ask.
        user_content: object = user_prompt
        if attachments:
            blocks = [self._attachment_block(a) for a in attachments]
            blocks.append({"type": "text", "text": user_prompt})
            user_content = blocks

        payload: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            # Anthropic takes `system` as a top-level field, not a message.
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_content},
            ],
        }
        # `temperature` is deprecated/rejected on newer models (e.g. Sonnet 5),
        # so only send it when explicitly configured.
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        return payload

    def _build_headers(self) -> dict:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": self._api_version,
            "content-type": "application/json",
        }

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
    def _attachment_block(att: Attachment) -> dict:
        """
        Render one Attachment as an Anthropic content block.

        PDF   -> {"type": "document", "source": {base64}}   (read natively)
        image -> {"type": "image",    "source": {base64}}   (read natively)
        text  -> {"type": "text", "text": "..."}            (inlined verbatim)

        DOCX/XLSX are NOT accepted here — the orchestration layer converts a
        DOCX to PDF before it reaches the provider (see document_flow).
        """
        ctype = (att.content_type or "").split(";")[0].strip().lower()

        if ctype in _DOCUMENT_TYPES:
            return {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": ctype,
                    "data": base64.standard_b64encode(att.data).decode("ascii"),
                },
            }
        if ctype in _IMAGE_TYPES:
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": ctype,
                    "data": base64.standard_b64encode(att.data).decode("ascii"),
                },
            }
        if ctype in _TEXT_TYPES:
            try:
                text = att.data.decode("utf-8", errors="replace")
            except Exception as exc:  # pragma: no cover - defensive
                raise LLMError(f"Could not decode text attachment: {exc}") from exc
            return {"type": "text", "text": text}

        raise LLMError(
            f"Unsupported attachment content type for Claude: '{att.content_type}'. "
            "Supported: PDF, PNG, JPEG, GIF, WebP, plain text."
        )

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
