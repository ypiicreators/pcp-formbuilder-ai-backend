"""doc_trace: id prefix, scope tags, stage timing/failure, summary."""

import asyncio
import logging

import pytest

from app.core import doc_trace

log = logging.getLogger("test.doc_trace")


@pytest.fixture
def records(caplog):
    caplog.set_level(logging.INFO, logger="test.doc_trace")
    flt = doc_trace.TraceFilter()
    caplog.handler.addFilter(flt)
    yield caplog
    caplog.handler.removeFilter(flt)


def test_prefix_scope_stage_and_summary(records):
    async def main():
        doc_trace.begin_trace(log, "unit", file="a.docx")

        async def sec(n):
            with doc_trace.scope(f"section {n}/2"), doc_trace.stage(log, "section") as st:
                await asyncio.sleep(0.01)
                st["fields"] = n

        await asyncio.gather(sec(1), sec(2))
        with pytest.raises(ValueError):
            with doc_trace.stage(log, "boom"):
                raise ValueError("bad")
        doc_trace.log_summary(log, "failed")

    asyncio.run(main())
    text = "\n".join(r.getMessage() for r in records.records)
    assert "[doc=" in text and "section 1/2" in text and "section 2/2" in text
    assert "STAGE section ok took=" in text and "fields=2" in text
    assert "STAGE boom FAILED" in text and "ValueError" in text
    assert "TRACE summary outcome=failed" in text and "slowest=" in text
    ids = {m.split("]")[0].split()[0] for m in text.splitlines() if m.startswith("[doc=")}
    assert len(ids) == 1  # one request, one id


def test_cancelled_stage_is_not_logged_as_an_error(records):
    async def main():
        doc_trace.begin_trace(log, "unit")

        async def slow():
            with doc_trace.stage(log, "slow"):
                await asyncio.sleep(5)

        task = asyncio.ensure_future(slow())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert any("cancelled" in r.getMessage() and r.levelno == logging.WARNING for r in records.records)
    assert not any(r.levelno >= logging.ERROR for r in records.records)
