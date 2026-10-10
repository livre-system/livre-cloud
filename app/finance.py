import logging
from decimal import Decimal, ROUND_HALF_UP


log = logging.getLogger(__name__)
LIVRE_COMMISSION_RATE = Decimal("0.15")
PROVIDER_COMPLETED_STATUSES = frozenset({"completed", "complete", "done", "finished", "finalized"})
CENT = Decimal("0.01")


def calculate_financials(gross_fare: Decimal | int | str) -> dict[str, Decimal | bool]:
    gross = Decimal(str(gross_fare)).quantize(CENT, rounding=ROUND_HALF_UP)
    if gross < 0:
        raise ValueError("gross fare cannot be negative")
    commission = (gross * LIVRE_COMMISSION_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
    return {
        "gross_fare": gross,
        "livre_commission": commission,
        "driver_net": gross - commission,
        "card_payment_confirmed": False,
    }


def finalize_trip_financials(cur, trip_id: int) -> dict | None:
    """Persist one split, or leave the completed trip audibly pending if fare is absent."""
    cur.execute(
        """
        UPDATE livi_trips
        SET gross_fare = COALESCE(gross_fare, fare),
            livre_commission = CASE WHEN COALESCE(gross_fare, fare) IS NULL THEN NULL
                                     ELSE ROUND(COALESCE(gross_fare, fare) * 0.15, 2) END,
            driver_net = CASE WHEN COALESCE(gross_fare, fare) IS NULL THEN NULL
                              ELSE COALESCE(gross_fare, fare) - ROUND(COALESCE(gross_fare, fare) * 0.15, 2) END,
            card_payment_confirmed = FALSE,
            financial_status = CASE WHEN COALESCE(gross_fare, fare) IS NULL
                                     THEN 'pending_settlement' ELSE 'finalized' END,
            financial_error = CASE WHEN COALESCE(gross_fare, fare) IS NULL
                                   THEN 'gross fare missing at trip finalization' ELSE NULL END,
            financial_finalized_at = CASE WHEN COALESCE(gross_fare, fare) IS NULL
                                          THEN NULL ELSE COALESCE(financial_finalized_at, NOW()) END,
            updated_at = NOW()
        WHERE id = %s
        RETURNING gross_fare, livre_commission, driver_net, card_payment_confirmed,
                  financial_status, financial_finalized_at
        """,
        (trip_id,),
    )
    row = cur.fetchone()
    if not row or row.get("financial_status") != "finalized":
        log.error("[FINANCE] trip_id=%s pending settlement: gross fare missing", trip_id)
        return None
    return row
