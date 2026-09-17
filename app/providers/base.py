"""
Provider-agnostic LLM interface for the PCP AI Form Builder.

The rest of the backend depends ONLY on this interface -- never on a specific
vendor. The active provider is selected by configuration (see factory.py) and
can be swapped without touching feature code.

Design constraint (plan section 4.2): the interface is the lowest common
denominator across providers -- a text system+user prompt in, clean JSON text
out, with an optional document attachment. Anything a provider can't do
natively is emulated inside its own adapter.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Attachment:
    """A document forwarded to a multimodal provider (document mode)."""

    filename: str
    content_type: str
    data: bytes


class LLMError(Exception):
    """Raised when a provider call fails or returns unusable output."""


class LLMProvider(ABC):
    """
    Abstract base every provider adapter implements.

    Subclasses declare their capabilities and implement `generate`, returning
    CLEAN JSON TEXT (fences stripped, envelope unwrapped) so the validation
    layer always sees the same contract regardless of vendor.
    """

    #: Human-readable provider name (e.g. "azure_openai").
    name: str = "base"

    #: Can this provider accept a PDF/DOCX/image attachment natively?
    supports_document_input: bool = False

    #: Does this provider have a native structured-output / JSON mode?
    supports_json_mode: bool = False

    #: Can this provider stream its response as text fragments (generate_stream)?
    supports_streaming: bool = False

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        attachments: list[Attachment] | None = None,
    ) -> str:
        """
        Send the prompts to the model and return clean JSON text.

        Implementations MUST normalise the response (strip markdown fences,
        unwrap the provider envelope) before returning.
        """
        raise NotImplementedError

    # --- Shared helpers available to all adapters -------------------------

    @staticmethod
    def strip_code_fences(text: str) -> str:
        """
        Remove surrounding markdown code fences from a model response.

        Handles ```json ... ``` and ``` ... ``` wrappers, plus stray leading/
        trailing prose the model may add despite instructions.
        """
        if not text:
            return text
        stripped = text.strip()

        # Remove a leading ```json / ``` and a trailing ``` if present.
        fence = re.match(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", stripped, re.DOTALL)
        if fence:
            return fence.group(1).strip()

        return stripped

    @classmethod
    def parse_json(cls, text: str) -> dict | list:
        """
        Parse model output into a Python object after stripping fences.

        If the response has leading/trailing prose around a JSON body, fall back
        to extracting the outermost {...} or [...] block. Raises LLMError if no
        valid JSON can be recovered.
        """
        cleaned = cls.strip_code_fences(text)
        try:
            return json.loads(cleaned)
        except (TypeError, ValueError):
            pass

        # Fallback: grab the first balanced object/array block.
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except (TypeError, ValueError) as exc:
                raise LLMError(f"Provider returned unparseable JSON: {exc}") from exc

        raise LLMError("Provider response contained no JSON.")
