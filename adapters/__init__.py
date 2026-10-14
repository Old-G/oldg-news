from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RawItem:
    """What every adapter returns; the rest of the pipeline is source-agnostic."""

    source_key: str
    external_id: str
    url: str | None
    title: str | None
    text: str | None
    published_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)
