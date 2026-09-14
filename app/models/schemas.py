"""
Request/response schemas for the PCP AI Form Builder API.

Mirrors the API contract (plan v3 §2.4, §7). The request is accepted either as
JSON (describe / edit modes) or multipart form-data (document mode, with a file).

IMPORTANT — output contract (grounded in `jsonGenerator.ts::generateJSON()`):
The form object the AI produces/edits is the SAME shape the Form Builder
exports and `loadFromJSON()` re-imports. That shape:
  - starts at `jurisdiction` / `sections` (optionally `verify_declaration`,
    `constants`, `crossFieldValidators`);
  - does NOT contain top-level metadata (`serviceCode`, `title`, `description`,
    `category`, `version`) — metadata is backend-owned and OUT of AI scope in v1
    (plan v3 §0 fact 3, §5);
  - OMITS `order` on sections/fields — the store re-derives it from array
    position (plan v3 §5).
"""

from enum import Enum
from typing import Any, Literal, Union

from pydantic import BaseModel, Field


class GenerationMode(str, Enum):
    """The three supported generation modes."""

    GENERATE_NL = "generate_nl"      # natural-language description -> full form
    GENERATE_DOC = "generate_doc"    # uploaded document -> full form
    EDIT_JSON = "edit_json"          # existing form + instruction -> change-set


class GenerateRequest(BaseModel):
    """
    Request body for POST /api/form-ai/generate (JSON variant).

    Document mode uses multipart form-data instead (mode + prompt + file), handled
    directly in the route; this model covers the JSON-body modes.
    """

    mode: GenerationMode = Field(..., description="Which generation mode to run.")
    prompt: str | None = Field(
        default=None,
        description=(
            "The instruction. REQUIRED for generate_nl and edit_json; "
            "OPTIONAL for generate_doc (server default applied if blank)."
        ),
    )
    base_json: dict[str, Any] | None = Field(
        default=None,
        description="Existing form JSON. REQUIRED for edit_json mode.",
    )


class EditRequest(BaseModel):
    """
    JSON body for POST /api/form-ai/edit (Edit mode).

    A clean JSON contract for the frontend: the current form object plus the
    admin's instruction. No multipart, no stringified JSON. The full form stays
    server-side; only compact/targeted context is sent to the LLM (plan v3 §7.9).
    """

    prompt: str = Field(..., min_length=1, description="The admin's edit instruction.")
    base_json: dict[str, Any] = Field(..., description="The current form JSON to edit.")


class GenerateNlRequest(BaseModel):
    """
    JSON body for POST /api/form-ai/generate-nl (Generate-from-description mode).

    The admin describes the form they want (fields, types, validations); the LLM
    produces a complete, sections-first form. No base form is needed.
    """

    prompt: str = Field(..., min_length=1, description="Natural-language description of the form.")


# --- Response outcome: proposal (success) -----------------------------------


class GenerateResponse(BaseModel):
    """
    Success outcome (HTTP 200): a reviewable proposal.

    `status` discriminates this from a clarification request so the frontend can
    branch on a single field. The AI never writes to the store; this is a
    proposal the admin reviews and applies via `loadFromJSON()`.
    """

    status: Literal["proposal"] = "proposal"
    form: dict[str, Any] = Field(
        ...,
        description=(
            "Flat, loadFromJSON-ready form object. Starts at jurisdiction/sections; "
            "no top-level metadata; no `order` keys."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal notes for the human reviewer.",
    )
    diff: list[dict[str, Any]] | None = Field(
        default=None,
        description="Edit mode only: the ID-anchored change-set that was applied.",
    )


# --- Response outcome: clarification (ambiguous target) ---------------------


class ClarificationOption(BaseModel):
    """One candidate target when a prompt matches multiple fields (plan v3 §7.5)."""

    field_id: str = Field(..., description="Unique field id of the candidate.")
    label: str = Field(..., description="Human-readable label (en) for display.")
    section_path: str = Field(
        ...,
        description="Where the field lives, e.g. 'Applicant Details' or "
        "'Applicant Details > Contact'. Disambiguates duplicate labels.",
    )


class ClarificationResponse(BaseModel):
    """
    Non-terminal outcome (HTTP 200): the backend could not uniquely resolve the
    target field and is asking the admin to choose (plan v3 §7.5, §7.6).

    No change-set is produced. The frontend renders `options` (plus an optional
    "all" choice) and re-submits with the disambiguated instruction.
    """

    status: Literal["needs_clarification"] = "needs_clarification"
    question: str = Field(..., description="Prompt to show the admin.")
    options: list[ClarificationOption] = Field(
        ..., description="Candidate target fields to choose from."
    )
    allow_all: bool = Field(
        default=False,
        description="Whether an 'apply to all matches' choice is offered.",
    )


#: A single-turn response is either a proposal or a clarification request.
GenerateOutcome = Union[GenerateResponse, ClarificationResponse]


# --- Errors & health --------------------------------------------------------


class ErrorResponse(BaseModel):
    """Failure response (HTTP 422) after the repair budget is exhausted."""

    errors: list[str] = Field(..., description="Unrecoverable validation errors.")


class HealthResponse(BaseModel):
    """Health-check response."""

    status: str = "ok"
    app: str
    version: str
    provider: str
