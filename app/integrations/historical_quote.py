"""Cotización local calibrada con cotizaciones históricas almacenadas."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

# Los IDs y conceptos se obtienen de la auditoría histórica. El precio por km
# y el fijo se vuelven a leer de la base para no depender de MAGIIS.
DEFAULT_RATE_ID = 6243
SERVICE_NAMES = {
    # En la respuesta real de MAGIIS, el tipo 1 tiene la tarifa más alta
    # y el tipo 2 la más baja. La identidad del servicio debe viajar junto
    # con ese ID; no se corrige posteriormente en la interfaz.
    "1": "Livre Premium",
    "2": "Livre Ahorro",
}


def _money(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def commercial_fare(original_fare: Decimal | int | str, discount_percentage: Decimal = Decimal("15")) -> dict[str, Decimal]:
    """Calcula el descuento comercial una sola vez, con precisión de centavos."""
    original = Decimal(str(original_fare)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    percentage = Decimal(str(discount_percentage)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if original < 0 or percentage < 0 or percentage > 100:
        raise ValueError("importe o porcentaje de descuento inválido")
    discount = (original * percentage / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "original_fare": original,
        "discount_percentage": percentage,
        "discount_amount": discount,
        "final_fare": original - discount,
    }


def apply_commercial_discount(quote: dict[str, Any]) -> dict[str, Any]:
    """Agrega el descuento comercial a cualquier cotización nueva.

    Es idempotente para evitar aplicar el descuento dos veces si se re procesa
    una cotización que ya tiene el snapshot comercial.
    """
    if quote.get("fareSource") == "commercial" and quote.get("finalFare") is not None:
        return dict(quote)
    fare = commercial_fare(quote["pricePointToPoint"])
    return {
        **quote,
        "price": float(fare["final_fare"]),
        "pricePointToPoint": float(fare["final_fare"]),
        "totalCostPrice": float(fare["final_fare"]),
        "originalFare": float(fare["original_fare"]),
        "discountPercentage": float(fare["discount_percentage"]),
        "discountAmount": float(fare["discount_amount"]),
        "finalFare": float(fare["final_fare"]),
        "fareSource": "commercial",
    }


# Compatibilidad para callers antiguos; la regla ya no depende del canal.
apply_whatsapp_discount = apply_commercial_discount


def calculate_quote(
    *, distance_km: Decimal, price_per_km: Decimal, fixed_cost: Decimal,
    rate_id: int = DEFAULT_RATE_ID, transport_type_id: str = "2",
) -> dict[str, Any]:
    if distance_km <= 0:
        raise ValueError("distance debe ser mayor que cero")
    if price_per_km <= 0:
        raise ValueError("price_per_km debe ser mayor que cero")
    price = _money(distance_km * price_per_km + fixed_cost)
    return {
        "price": price,
        "pricePointToPoint": price,
        "totalCostPrice": price,
        "distance": float(distance_km.quantize(Decimal("0.01"))),
        "routeDistance": float(distance_km),
        "pricePerKm": float(price_per_km),
        "rateId": rate_id,
        "serviceStatus": "AVAILABLE",
        "transportTypeId": transport_type_id,
        "serviceName": SERVICE_NAMES.get(transport_type_id, transport_type_id),
        "productName": SERVICE_NAMES.get(transport_type_id, transport_type_id),
        "source": "historical_quotes",
    }


def load_historical_profiles() -> list[dict[str, Any]]:
    """Obtiene perfiles por tipo de servicio desde las cotizaciones guardadas."""
    from app.core.database import get_cursor

    with get_cursor() as cur:
        cur.execute(
            """
            SELECT
                rate_id,
                transport_type_id,
                (quote_body->>'pricePerKm')::numeric AS price_per_km,
                AVG(total_cost_price::numeric - distance::numeric *
                    (quote_body->>'pricePerKm')::numeric) AS fixed_cost,
                COUNT(*) AS samples
            FROM magiis_quotes
            WHERE rate_id = %s
              AND distance IS NOT NULL
              AND total_cost_price IS NOT NULL
              AND quote_body->>'pricePerKm' IS NOT NULL
            GROUP BY rate_id, transport_type_id, quote_body->>'pricePerKm'
            HAVING COUNT(*) > 0
            ORDER BY transport_type_id, samples DESC
            """,
            (DEFAULT_RATE_ID,),
        )
        rows = cur.fetchall()
    if not rows:
        raise RuntimeError("no hay perfiles históricos disponibles para cotizar")
    return [dict(row) for row in rows]


def historical_options(distance_km: Decimal, profiles: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    profiles = profiles if profiles is not None else load_historical_profiles()
    return [
        calculate_quote(
            distance_km=distance_km,
            price_per_km=Decimal(str(profile["price_per_km"])),
            fixed_cost=Decimal(str(profile["fixed_cost"])),
            rate_id=int(profile["rate_id"]),
            transport_type_id=str(profile["transport_type_id"]),
        )
        for profile in profiles
    ]
