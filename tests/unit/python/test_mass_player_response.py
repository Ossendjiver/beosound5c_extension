import pytest
from sources.mass.service import MassSource

@pytest.mark.parametrize("payload", [None, 0, {"items": 0}, {"items": 3}, {"items": None}])
def test_counts_and_empty_results_are_not_players(payload):
    assert MassSource._player_list(payload) == []

@pytest.mark.parametrize("payload", [[{"player_id": "link"}], {"items": [{"player_id": "link"}]}, {"items": 1, "players": [{"player_id": "link"}]}, {"link": {"player_id": "link"}}])
def test_real_player_records_are_preserved(payload):
    assert MassSource._player_list(payload) == [{"player_id": "link"}]
