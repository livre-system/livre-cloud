from app.crm.router import _travel_history_page


class FakeCursor:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))

    def fetchone(self):
        return next(self.results)

    def fetchall(self):
        return next(self.results)


def test_travel_history_filters_driver_and_exposes_premium_commission_contract():
    cur = FakeCursor([
        {"total": 1},
        [{
            "id": 10,
            "service_type_id": "1",
            "gross_amount": 10000,
            "commission_amount": 1500,
            "driver_amount": 8500,
            "commission_percentage": 15.0,
        }],
        {"gross_amount": 10000, "commission_amount": 1500, "driver_amount": 8500},
        [{"id": "driver-a", "name": "Chofer A"}],
    ])

    result = _travel_history_page(cur, 1, 50, driver_id="driver-a", state="completed")

    assert result["total"] == 1
    assert result["summary"]["commission_amount"] == 1500
    assert result["commission_rule"].startswith("Sólo Premium")
    assert result["drivers"] == [{"id": "driver-a", "name": "Chofer A"}]
    assert all("t.assigned_driver_user_id = %s" in query for query, _ in cur.calls[:3])
    assert all("t.service_type_id = '1'" in query for query, _ in cur.calls[1:3])
