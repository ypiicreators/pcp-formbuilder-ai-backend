"""
Shared validation result types.

Both validation levels (L1 structural, L2 business) emit the SAME kind of
error object, so the repair step and the API can treat them uniformly. Each
error carries a machine-usable `code`, a human `message`, and the `path` to the
offending node so the repair prompt can point the model at exactly what to fix
(plan §4.5 / v3 §2.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Level(str, Enum):
    """Which validation layer produced an error."""

    L1_STRUCTURAL = "L1_structural"
    L2_BUSINESS = "L2_business"


class Severity(str, Enum):
    """
    How serious a finding is.

    ERROR   -> blocks finalize; feeds the repair loop / 422.
    WARNING -> non-blocking note surfaced to the human reviewer (e.g. a
               reference to a dynamically-created field we can't verify statically).
    """

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationError:
    """A single, located validation finding (error or warning)."""

    level: Level
    code: str          # stable identifier, e.g. "invalid_field_type"
    message: str       # human-readable explanation
    path: str = ""     # where it happened, e.g. "Applicant Details > mobile"
    field_id: str = "" # the offending field/column id, when applicable
    severity: Severity = Severity.ERROR

    def __str__(self) -> str:
        loc = f" [{self.path}]" if self.path else ""
        tag = "" if self.severity is Severity.ERROR else "warning: "
        return f"{tag}{self.level.value}: {self.message}{loc}"


@dataclass
class ValidationResult:
    """
    The outcome of validating a form (or an applied change-set).

    Findings holds both errors and warnings. `ok` reflects ERRORS only —
    warnings never block finalize, they ride along for the human reviewer.
    """

    findings: list[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when there are no ERROR-severity findings (warnings allowed)."""
        return not self.errors

    @property
    def errors(self) -> list[ValidationError]:
        """Only the blocking findings."""
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationError]:
        """Only the non-blocking findings."""
        return [f for f in self.findings if f.severity is Severity.WARNING]

    def add(self, finding: ValidationError) -> None:
        self.findings.append(finding)

    def extend(self, findings: list[ValidationError]) -> None:
        self.findings.extend(findings)

    def error_messages(self) -> list[str]:
        """Blocking messages (for the 422 response / repair prompt)."""
        return [str(e) for e in self.errors]

    def warning_messages(self) -> list[str]:
        """Non-blocking notes (for the proposal `warnings[]`)."""
        return [str(w) for w in self.warnings]
