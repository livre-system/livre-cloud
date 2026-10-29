from app.context import router


def test_semantic_memory_is_shared_across_authenticated_admins(monkeypatch):
    calls = []

    def fake_search(user_id, query, limit, min_similarity):
        calls.append((user_id, query, limit, min_similarity))
        return [{"name": "global", "data": {"message": "dato compartido"}, "similarity": 0.9}]

    monkeypatch.setattr(router, "search_memory_by_embedding", fake_search)

    first = router._search_memory("smoreno", "consulta operativa")
    second = router._search_memory("mgiagante", "consulta operativa")

    assert first == second
    assert [call[0] for call in calls] == [router.MEMORY_USER_ID, router.MEMORY_USER_ID]
