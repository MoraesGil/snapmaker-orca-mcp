"""Bounded G-code extraction independent of artifact transport and storage."""
from collections.abc import Iterable
from itertools import islice

from ...domain.types.values import LineRange
from ..contracts.artifacts import GcodeLines


def bounded_lines(stream: Iterable[str], job_id: str, plate: int,
                  start: int = 1, count: int = 200) -> GcodeLines:
    if type(start) is not int or type(count) is not int or start < 1 or not 1 <= count <= 1000:
        raise ValueError('Line range must start at 1 or later and contain 1..1000 lines')
    span = LineRange(start, start + count - 1)
    return {'job_id': job_id, 'plate': plate, 'start_line': span.first,
            'lines': [line.rstrip('\r\n') for line in islice(stream, span.first - 1, span.last)]}
