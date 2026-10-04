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

DriverOperationalStatus = Literal["available", "unavailable", "out_of_service"]


class LocationUpdate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class StatusUpdate(BaseModel):
    status: TripStatus


class DriverOperationalStatusUpdate(BaseModel):
    status: DriverOperationalStatus


class DriverOperationalStatusResponse(BaseModel):
    status: DriverOperationalStatus


class TripRequest(BaseModel):
    origin: str = Field(min_length=3, max_length=240)
    destination: str = Field(min_length=3, max_length=240)
    passenger: str = Field(default="Pasajero de prueba", min_length=2, max_length=120)
    whatsapp_user: str = Field(default="web-test", min_length=2, max_length=120)


class TripRequestResponse(BaseModel):
    trip_id: int
    tracking_token: str
    tracking_url: str
    status: str
    message: str


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
    service_type_id: int | None = None
    service_type_name: str | None = None
    fare: float | None = None


class TripMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


class TripMessage(BaseModel):
    id: int
    trip_id: int
    channel: str
    sender_role: str
    sender_id: str | None = None
    body: str
    created_at: datetime


class HandieOpenRequest(BaseModel):
    trip_id: int | None = None


class HandieMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)
