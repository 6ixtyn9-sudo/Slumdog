from slumdog.facets import facet_catalogue
from slumdog.sports import HISTORY_STARTS, SPORTS


def test_all_forebet_sports_are_registered():
    assert set(SPORTS) == {
        "football", "basketball", "tennis", "hockey", "baseball",
        "american_football", "rugby", "handball", "volleyball",
        "cricket", "mma", "esoccer", "afl",
    }


def test_every_sport_has_unique_path_and_facets():
    assert len({spec.path for spec in SPORTS.values()}) == len(SPORTS)
    catalog = facet_catalogue()
    for sport, spec in SPORTS.items():
        assert spec.known_facets
        assert "result" in catalog[sport]
        assert "probability_1" in catalog[sport]


def test_every_sport_has_depth_contract():
    assert set(HISTORY_STARTS) == set(SPORTS)
    assert HISTORY_STARTS["hockey"] == "2022-01-01"
    assert HISTORY_STARTS["esoccer"] is None
