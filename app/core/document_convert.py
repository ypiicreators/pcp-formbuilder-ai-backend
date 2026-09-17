"""
DOCX text extraction for document-mode generation.

Claude reads PDF, PNG, JPEG, and plain text natively over the API, but it CANNOT
read raw Office formats (.docx) — those are ZIP-compressed XML that arrive as
binary garbage. So before a DOCX reaches the provider we extract its text HERE,
in pure Python, and send that text instead (via the text-fallback doc prompt).

Why extraction rather than DOCX->PDF conversion: converting needs a headless
LibreOffice binary on the host, which is a fragile deployment dependency. Our
form documents are digital Word files with a real text layer (not scans), and
they're defined by a TABLE of fields/labels/types — so walking the document in
order and rendering its tables as readable rows preserves exactly what the model
needs, with only a pip dependency (`python-docx`) and no system binary.

This module is deliberately self-contained: bytes in, text out. It knows nothing
about the LLM, the form schema, or FastAPI.
"""

from __future__ import annotations

import io
from pathlib import Path

#: MIME types we treat as "Word document -> extract text".
DOCX_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
}

#: Filename extensions that map to DOCX extraction (fallback when the browser
#: sends a generic content type like application/octet-stream).
DOCX_EXTENSIONS = {".docx"}


class DocumentConversionError(Exception):
    """Raised when a Word document cannot be read / has no extractable text."""


def is_docx(filename: str | None, content_type: str | None) -> bool:
    """True if this upload is a Word document we should extract text from."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in DOCX_CONTENT_TYPES:
        return True
    ext = Path(filename or "").suffix.lower()
    return ext in DOCX_EXTENSIONS


def extract_docx_text(data: bytes) -> str:
    """
    Extract readable text from DOCX bytes, preserving reading order and tables.

    Walks the document body in document order so paragraphs and tables interleave
    the way they appear on the page. Tables — which is where form fields usually
    live — are rendered as pipe-delimited rows so the label/type/validation
    columns stay aligned for the model.

    Raises DocumentConversionError if python-docx is unavailable, the file isn't a
    valid DOCX, or it contains no extractable text.
    """
    try:
        import docx  # python-docx
        from docx.document import Document as _Document
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table, _Cell
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise DocumentConversionError(
            "Word document support is not installed on the server "
            "(missing python-docx). Please upload a PDF instead, or ask an "
            "administrator to install the dependency."
        ) from exc

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # invalid/corrupt DOCX, wrong format, etc.
        raise DocumentConversionError(
            "Could not read the Word document — it may be corrupt or not a valid "
            ".docx file. Please upload a PDF instead."
        ) from exc

    def _iter_block_items(parent):
        """Yield Paragraph and Table children of `parent` in document order."""
        if isinstance(parent, _Document):
            parent_elm = parent.element.body
        elif isinstance(parent, _Cell):
            parent_elm = parent._tc
        else:  # pragma: no cover - defensive
            parent_elm = parent.element

        for child in parent_elm.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)

    def _render_table(table) -> str:
        rows_out: list[str] = []
        for row in table.rows:
            cells = [" ".join(c.text.split()) for c in row.cells]
            # Drop fully empty rows; keep partial rows (blank cells matter as gaps).
            if any(cells):
                rows_out.append(" | ".join(cells))
        return "\n".join(rows_out)

    lines: list[str] = []
    for block in _iter_block_items(document):
        if isinstance(block, Paragraph):
            text = " ".join(block.text.split())
            if text:
                lines.append(text)
        elif isinstance(block, Table):
            rendered = _render_table(block)
            if rendered:
                lines.append(rendered)

    result = "\n".join(lines).strip()
    if not result:
        raise DocumentConversionError(
            "The Word document appears to contain no readable text (it may be "
            "image-only). Please upload it as a PDF instead."
        )
    return result
