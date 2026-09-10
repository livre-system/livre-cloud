"""Persistencia de cotizaciones Web de MAGIIS.

La identidad se obtiene de livi_trips.whatsapp_user. No usa livre_users.id.
"""

from __future__ import annotations

from typing import Any

from psycopg2.extras import Json

from app.core.database import get_cursor


def persist_quote_response(
    *,
    livi_trip_id: int,
    http_status: int,
    request_payload: dict[str, Any],
    response_body: Any,
) -> int:
    """Guarda un lote MAGIIS y sus opciones; devuelve el ID del lote.

    El whatsapp_user se lee desde la solicitud LIVI para evitar mezclarlo con
    cualquier identidad o ID de usuario de MAGIIS.
    """
    if not isinstance(response_body, list):
        quotes: list[dict[str, Any]] = []
    else:
        quotes = [item for item in response_body if isinstance(item, dict)]

    with get_cursor() as cur:
        cur.execute(
            """
            SELECT whatsapp_user
            FROM livi_trips
            WHERE id = %s
            FOR UPDATE;
            """,
            (livi_trip_id,),
        )
        trip = cur.fetchone()
        if trip is None:
            raise ValueError(f"livi_trip_id inexistente: {livi_trip_id}")
        whatsapp_user = (trip["whatsapp_user"] or "").strip()
        if not whatsapp_user:
            raise ValueError(
                f"livi_trip_id={livi_trip_id} no tiene whatsapp_user"
            )

        cur.execute(
            """
            INSERT INTO magiis_quote_requests
                (livi_trip_id, whatsapp_user, http_status, request_payload, response_body)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                livi_trip_id,
                whatsapp_user,
                http_status,
                Json(request_payload),
                Json(response_body),
            ),
        )
        quote_request_id = cur.fetchone()["id"]

        preferences = request_payload.get("travelPreferences") or {}
        service_type_id = request_payload.get("serviceTypeId")
        vehicle_type_id = preferences.get("vehicleTypeId")
        for quote in quotes:
            cur.execute(
                """
                INSERT INTO magiis_quotes
                    (quote_request_id, transport_type_id, vehicle_type_id,
                     service_type_id, service_status, price_point_to_point,
                     total_cost_price, distance, duration, rate_id, quote_body)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    quote_request_id,
                    quote.get("transportTypeId"),
                    vehicle_type_id,
                    service_type_id,
                    quote.get("serviceStatus"),
                    quote.get("pricePointToPoint"),
                    quote.get("totalCostPrice"),
                    quote.get("distance"),
                    quote.get("duration"),
                    quote.get("rateId"),
                    Json(quote),
                ),
            )

    return quote_request_id
