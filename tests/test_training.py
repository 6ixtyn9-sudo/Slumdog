from slumdog.contracts import SettledEvent
from slumdog.training import build_training_rows


def _settled(disposition: str, winner: int = 1) -> SettledEvent:
    return SettledEvent(
        event_id="x", sport="football", event_date="2026-08-19",
        participant_1="Home", participant_2="Away", winner_index=winner,
        score_1=1.0, score_2=0.0, probability_1=0.4, probability_2=0.4,
        draw_probability=0.2, forebet_pick=2, disposition=disposition,
    )


def test_void_rows_never_become_training_rows():
    rows = build_training_rows([_settled("SETTLED"), _settled("VOID")])
    assert len(rows) == 1
    assert rows[0].underdog_won in (0, 1)


def test_settled_draw_stays_in_training_as_non_win():
    rows = build_training_rows([_settled("SETTLED_DRAW", winner=0)])
    assert len(rows) == 1
    assert rows[0].underdog_won == 0
