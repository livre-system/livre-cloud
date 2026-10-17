from datetime import date, datetime
from decimal import Decimal
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
    device_id: str = Field(min_length=8, max_length=100)


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
    fare: Decimal = Field(gt=0)
    service_type_id: int | None = Field(default=None, ge=1)


class TripRequestResponse(BaseModel):
    trip_id: int
    tracking_token: str
    tracking_url: str
    status: str
    message: str
    fare: float | None = None
    currency: str = "ARS"


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
    fare: float | None = None
    original_fare: float | None = None
    discount_percentage: float | None = None
    discount_amount: float | None = None
    final_fare: float | None = None
    fare_source: str | None = None


class DriverTrip(PublicTrip):
    """Viaje asignado al conductor autenticado."""
    service_type_id: int | None = None
    service_type_name: str | None = None
    fare: float | None = None
    driver_fare: float | None = None
    original_fare: float | None = None
    discount_percentage: float | None = None
    discount_amount: float | None = None
    final_fare: float | None = None
    fare_source: str | None = None


class DriverHistoryItem(BaseModel):
    id: int
    origin: str
    destination: str
    status: str
    completed_at: datetime | None = None
    fare: float | None = None
    driver_fare: float | None = None
    original_fare: float | None = None
    discount_percentage: float | None = None
    discount_amount: float | None = None
    final_fare: float | None = None
    fare_source: str | None = None


class DriverHistoryResponse(BaseModel):
    total: int
    total_revenue: float | None = None
    driver_revenue: float | None = None
    commission: float | None = None
    items: list[DriverHistoryItem]


class DriverShiftClosureCreate(BaseModel):
    closure_date: date


class DriverShiftClosure(BaseModel):
    id: int
    closure_date: date
    trip_count: int
    total_revenue: float | None = None
    driver_revenue: float | None = None
    commission: float | None = None
    created_at: datetime | None = None


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
