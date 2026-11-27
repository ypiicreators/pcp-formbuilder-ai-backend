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
import json
import logging
import time

import httpx

from app.core.doc_trace import stage
from app.providers.base import Attachment, LLMError, LLMProvider

logger = logging.getLogger("app.providers.anthropic")

def _describe(exc: Exception) -> str:
    """Readable error text; httpx timeouts have an EMPTY str(), so include the type."""
    return f"{type(exc).__name__}: {exc}".rstrip(": ")


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
        *,
        max_tokens: int | None = None,
    ) -> str:
        """Call the Messages API and return clean JSON text."""
        payload = self._build_payload(
            system_prompt, user_prompt, attachments, max_tokens=max_tokens
        )
        headers = self._build_headers()

        with stage(
            logger,
            "llm.generate",
            model=self._model,
            prompt_chars=len(system_prompt) + len(user_prompt),
            attachments=len(attachments or []),
            max_tokens=payload["max_tokens"],
            timeout=f"{self._timeout:.0f}s/read",
        ) as info:
            raw = await self._post_with_retry(payload, headers, info)
            usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
            stop = raw.get("stop_reason") if isinstance(raw, dict) else None
            info.update(
                input_tokens=usage.get("input_tokens", "?"),
                output_tokens=usage.get("output_tokens", "?"),
                stop_reason=stop,
            )
            if stop == "max_tokens":
                logger.warning(
                    "llm.generate output TRUNCATED at max_tokens=%s - JSON is likely cut off "
                    "(raise the token limit or shrink the request)",
                    payload["max_tokens"],
                )
            content = self._extract_content(raw)
            return self.strip_code_fences(content)

    async def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        attachments: list[Attachment] | None = None,
        *,
        max_tokens: int | None = None,
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
        payload = self._build_payload(
            system_prompt, user_prompt, attachments, max_tokens=max_tokens
        )
        payload["stream"] = True
        headers = self._build_headers()

        started = time.monotonic()
        yielded_any = False
        chars = 0
        first_token_at: float | None = None
        last_progress = started
        meta: dict = {}
        with stage(
            logger,
            "llm.stream",
            model=self._model,
            prompt_chars=len(system_prompt) + len(user_prompt),
            attachments=len(attachments or []),
            max_tokens=payload["max_tokens"],
            timeout=f"{self._timeout:.0f}s/read",
        ) as info:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    async with client.stream(
                        "POST", self._url, json=payload, headers=headers
                    ) as resp:
                        info["http_status"] = resp.status_code
                        info["connect_and_headers"] = f"{time.monotonic() - started:.2f}s"
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode("utf-8", errors="replace")
                            logger.error(
                                "llm.stream HTTP %s: %s", resp.status_code, body[:300]
                            )
                            raise LLMError(
                                f"Anthropic stream error {resp.status_code}: {body[:300]}"
                            )
                        async for line in resp.aiter_lines():
                            text = self._parse_sse_line(line)
                            if text:
                                now = time.monotonic()
                                if first_token_at is None:
                                    first_token_at = now
                                    logger.info(
                                        "llm.stream first token after %.1fs",
                                        now - started,
                                    )
                                yielded_any = True
                                chars += len(text)
                                if now - last_progress >= 15:
                                    last_progress = now
                                    logger.info(
                                        "llm.stream progress: %d chars after %.0fs",
                                        chars,
                                        now - started,
                                    )
                                yield text
                            else:
                                meta.update(self._parse_sse_meta(line))
            except httpx.RequestError as exc:
                logger.error(
                    "llm.stream request error after %.1fs (chars so far=%d): %s",
                    time.monotonic() - started,
                    chars,
                    _describe(exc),
                )
                raise LLMError(f"Anthropic stream request failed: {_describe(exc)}") from exc
            finally:
                info.update(
                    chars=chars,
                    first_token=(
                        f"{first_token_at - started:.1f}s" if first_token_at else "never"
                    ),
                    input_tokens=meta.get("input_tokens", "?"),
                    output_tokens=meta.get("output_tokens", "?"),
                    stop_reason=meta.get("stop_reason"),
                )

            if meta.get("stop_reason") == "max_tokens":
                logger.warning(
                    "llm.stream output TRUNCATED at max_tokens=%s - JSON is likely cut off "
                    "(raise the token limit or shrink the request)",
                    payload["max_tokens"],
                )
            if not yielded_any:
                raise LLMError("Anthropic stream returned no content.")

    @staticmethod
    def _parse_sse_meta(line: str) -> dict:
        """Token usage / stop_reason from the non-text SSE events (diagnostics only)."""
        if not line or not line.startswith("data:"):
            return {}
        try:
            evt = json.loads(line[len("data:"):].strip())
        except (TypeError, ValueError):
            return {}
        out: dict = {}
        kind = evt.get("type")
        if kind == "message_start":
            usage = (evt.get("message") or {}).get("usage") or {}
            if "input_tokens" in usage:
                out["input_tokens"] = usage["input_tokens"]
        elif kind == "message_delta":
            usage = evt.get("usage") or {}
            if "output_tokens" in usage:
                out["output_tokens"] = usage["output_tokens"]
            stop = (evt.get("delta") or {}).get("stop_reason")
            if stop:
                out["stop_reason"] = stop
        elif kind == "error":
            logger.error("llm.stream error event: %s", str(evt.get("error"))[:300])
        return out

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
        *,
        max_tokens: int | None = None,
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
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
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

    async def _post_with_retry(
        self, payload: dict, headers: dict, info: dict | None = None
    ) -> dict:
        """POST with bounded retry + exponential backoff on transient errors."""
        last_exc: Exception | None = None
        attempts = self._max_retries + 1
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(attempts):
                attempt_started = time.monotonic()
                if info is not None:
                    info["attempts"] = attempt + 1
                try:
                    resp = await client.post(self._url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        return resp.json()
                    if resp.status_code in (429, 500, 502, 503, 504, 529):
                        last_exc = LLMError(
                            f"Anthropic transient error {resp.status_code}: {resp.text[:300]}"
                        )
                    else:
                        logger.error(
                            "llm.generate HTTP %s after %.1fs: %s",
                            resp.status_code,
                            time.monotonic() - attempt_started,
                            resp.text[:300],
                        )
                        raise LLMError(
                            f"Anthropic error {resp.status_code}: {resp.text[:300]}"
                        )
                except httpx.RequestError as exc:
                    last_exc = LLMError(f"Anthropic request failed: {_describe(exc)}")

                logger.warning(
                    "llm.generate attempt %d/%d failed after %.1fs: %s%s",
                    attempt + 1,
                    attempts,
                    time.monotonic() - attempt_started,
                    last_exc,
                    "" if attempt + 1 < attempts else " (giving up)",
                )
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

        DOCX/XLSX are NOT accepted here — the orchestration layer extracts DOCX
        text before it reaches the provider (see document_flow).
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
