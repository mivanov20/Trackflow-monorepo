"""Telemetry event envelope (docs/telemetry/telemetry-plan.md, section 4.2).

`TelemetryEvent` is the contract shared by the stub receiver of Phase 2 and the persistent
collector of Phase 3, so it validates the envelope completely from the start. The `properties`
payload is only checked for shape here; its per-event allowlist lives in
docs/telemetry/event-schemas.json and is enforced by the producers today and by the collector later.
"""

from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

# Same limits as the plan's transport contract (section 9.2).
MAX_EVENTS_PER_BATCH = 100
MAX_PROPERTIES_PER_EVENT = 40

EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$"
SEMVER_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"


class TelemetryEvent(BaseModel):
    """One event with the standard envelope. Field names match the JSON contract exactly."""

    model_config = ConfigDict(extra="forbid")

    eventId: UUID
    timestamp: AwareDatetime
    sessionId: UUID | None
    userId: UUID | None
    event_type: str = Field(pattern=EVENT_TYPE_PATTERN, max_length=64)
    schemaVersion: str = Field(pattern=SEMVER_PATTERN, max_length=20)
    requestId: UUID
    properties: dict[str, Any]

    @field_validator("properties")
    @classmethod
    def limit_properties(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Rejects absurdly large payloads; the per-event allowlist is checked by the real collector."""
        if len(value) > MAX_PROPERTIES_PER_EVENT:
            raise ValueError(f"properties can have at most {MAX_PROPERTIES_PER_EVENT} keys")
        return value


class TelemetryBatch(BaseModel):
    """Body of `POST /telemetry/events`: the events accumulated by the browser since the last flush."""

    model_config = ConfigDict(extra="forbid")

    events: list[TelemetryEvent] = Field(max_length=MAX_EVENTS_PER_BATCH)


class TelemetryReceipt(BaseModel):
    """Answer of the receiver: how many events the batch carried."""

    received: int
