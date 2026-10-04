from App.service.AI.context.context_cache import BoundedContextCache


def test_cache_is_scoped_by_user_and_project_and_invalidatable():
    cache = BoundedContextCache(max_entries=4, ttl_seconds=60)
    calls = []

    first = cache.get_or_create((1, 11, "v1"), lambda: calls.append("owner-one"))
    isolated = cache.get_or_create((2, 11, "v1"), lambda: "owner-two")
    cached = cache.get_or_create((1, 11, "v1"), lambda: "unexpected")

    assert first is None
    assert isolated == "owner-two"
    assert cached is None
    assert calls == ["owner-one"]

    cache.get_or_create((1, 22, "v1"), lambda: "other-project")
    cache.invalidate_project(11)
    assert cache.get_or_create((1, 22, "v1"), lambda: "unexpected") == (
        "other-project"
    )
    assert cache.get_or_create((1, 11, "v1"), lambda: "refreshed") == "refreshed"


def test_cache_has_a_bounded_entry_count():
    cache = BoundedContextCache(max_entries=2, ttl_seconds=60)
    for project_id in (1, 2, 3):
        cache.get_or_create((1, project_id), lambda: project_id)

    assert len(cache._values) == 2
