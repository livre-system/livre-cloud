from decimal import Decimal, ROUND_HALF_UP


LIVRE_COMMISSION_RATE = Decimal("0.15")
PROVIDER_COMPLETED_STATUSES = frozenset({"completed", "complete", "done", "finished", "finalized"})
CENT = Decimal("0.01")


def calculate_financials(gross_fare: Decimal | int | str) -> dict[str, Decimal | bool]:
    gross = Decimal(str(gross_fare)).quantize(CENT, rounding=ROUND_HALF_UP)
    if gross < 0:
        raise ValueError("gross_fare must be non-negative")
    commission = (gross * LIVRE_COMMISSION_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
    return {
        "gross_fare": gross,
        "livre_commission": commission,
        "driver_net": gross - commission,
        "card_payment_confirmed": False,
    }


def finalize_trip_financials(cur, trip_id: int) -> dict | None:
    """Persist the financial split once, without changing the passenger fare."""
    cur.execute(
        """
        UPDATE livi_trips
        SET gross_fare = COALESCE(gross_fare, fare),
            livre_commission = ROUND(COALESCE(gross_fare, fare) * 0.15, 2),
            driver_net = COALESCE(gross_fare, fare) - ROUND(COALESCE(gross_fare, fare) * 0.15, 2),
            card_payment_confirmed = FALSE,
            financial_finalized_at = COALESCE(financial_finalized_at, NOW()),
            updated_at = NOW()
        WHERE id = %s
          AND status = 'completed'
          AND COALESCE(gross_fare, fare) IS NOT NULL
        RETURNING gross_fare, livre_commission, driver_net, card_payment_confirmed,
                  financial_finalized_at
        """,
        (trip_id,),
    )
    return cur.fetchone()
