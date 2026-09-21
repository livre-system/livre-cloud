from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TripStatus = Literal[
    "pending",
    "searching",
    "assigned",
    "arriving",
    "in_progress",
    "completed",
    "cancelled",
]


class LocationUpdate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class StatusUpdate(BaseModel):
    status: TripStatus


class PublicTrip(BaseModel):
    id: int
    origin: str
    destination: str
    status: str
    driver_name: str | None = None
    plate: str | None = None
    vehicle_model: str | None = None
    vehicle_color: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_updated_at: datetime | None = None


class DriverTrip(PublicTrip):
    """Viaje asignado al conductor autenticado."""
