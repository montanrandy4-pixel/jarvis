"""The long-term memory store."""

from __future__ import annotations

from jarvis.memory import MemoryStore


def test_memories_survive_a_restart(tmp_path):
    path = tmp_path / "memory.json"
    MemoryStore(path).add("The user's dog is called Bruno.")

    assert MemoryStore(path).texts() == ["The user's dog is called Bruno."]


def test_saying_the_same_thing_twice_stores_it_once(tmp_path):
    store = MemoryStore(tmp_path / "m.json")

    first = store.add("Prefers dark mode.")
    again = store.add("prefers DARK mode.")

    assert first.id == again.id
    assert len(store.all()) == 1


def test_search_ranks_by_how_many_terms_match(tmp_path):
    store = MemoryStore(tmp_path / "m.json")
    store.add("Works at a bakery in Leeds.")
    store.add("Drives to Leeds on Fridays.", ["travel"])

    hits = store.search("leeds bakery")

    assert hits[0].text.startswith("Works at a bakery")
    assert len(hits) == 2


def test_search_matches_tags_too(tmp_path):
    store = MemoryStore(tmp_path / "m.json")
    store.add("Flight lands at nine.", ["travel"])

    assert store.search("travel")[0].text == "Flight lands at nine."


def test_forgetting_removes_it_from_disk(tmp_path):
    path = tmp_path / "m.json"
    store = MemoryStore(path)
    item = store.add("Temporary note.")

    assert store.forget(item.id)
    assert not store.forget(item.id)
    assert MemoryStore(path).all() == []


def test_a_corrupt_file_is_set_aside_rather_than_crashing(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{not json at all")

    store = MemoryStore(path)

    assert store.all() == []
    assert path.with_suffix(".corrupt").exists()
    store.add("Recovered.")  # Still usable afterwards.
    assert MemoryStore(path).texts() == ["Recovered."]


def test_malformed_entries_are_skipped_but_good_ones_kept(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(
        '{"version": 1, "memories": ['
        '{"nope": true}, '
        '{"id": "abc", "text": "Good one.", "created_at": 1.0, "tags": []}]}'
    )

    assert MemoryStore(path).texts() == ["Good one."]
