"""Read contracts shared by the MCP and HTTP adapters."""
from typing import TypedDict


class GcodeLines(TypedDict):
    job_id: str
    plate: int
    start_line: int
    lines: list[str]
