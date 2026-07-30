"""Persistence-agnostic types shared by immutable domain DTOs."""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

type JsonObject = dict[str, object]


def require_aware_datetime(value: datetime) -> datetime:
    """Reject timestamps that cannot identify an absolute point in time."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


type AwareDateTime = Annotated[datetime, AfterValidator(require_aware_datetime)]


class ImmutableDTO(BaseModel):
    """Base for validated, value-like DTOs with no persistence behavior."""

    model_config = ConfigDict(frozen=True, extra="forbid")
