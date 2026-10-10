from app.crm.router import live_vehicle_owners


class FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))

    def fetchone(self):
        return {"total": 2}

    def fetchall(self):
        return [
            {"magiis_id": 20, "raw_payload": {"firstName": "Ana"}},
            {"magiis_id": 10, "raw_payload": {"firstName": "Luis"}},
        ]


def test_vehicle_owners_reads_local_snapshot_and_preserves_dashboard_contract():
    cur = FakeCursor()

    result = live_vehicle_owners(page=2, size=2, q="Ana", user={"id": "crm"}, cur=cur)

    assert set(result) == {"items", "total", "page", "size"}
    assert result == {
        "items": [
            {"magiis_id": 20, "raw_payload": {"firstName": "Ana"}},
            {"magiis_id": 10, "raw_payload": {"firstName": "Luis"}},
        ],
        "total": 2,
        "page": 2,
        "size": 2,
    }
    assert all("magiis_crm_vehicle_owners" in query for query, _ in cur.calls)
    assert all("magiis_crm_vehicle_owners" not in query.lower().replace("magiis_crm_vehicle_owners", "") for query, _ in cur.calls)
    assert cur.calls[0][1] == ["%Ana%"]