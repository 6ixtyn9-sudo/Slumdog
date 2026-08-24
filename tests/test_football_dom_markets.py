from slumdog.detail_facets import parse_detail


def _detail(markup: str):
    return parse_detail(f"<html><body>{markup}</body></html>".encode(), "football").sport_specific


def test_dom_double_chance_standard_selected_pick_and_price():
    fields = _detail('''
      <div id="dbc_table"><div class="rcnt">
        <div class="fprc"><span class="fpr">76%</span></div>
        <div class="predict"><span class="forepr">12</span></div>
        <div class="prmod"><span class="lscrsp">-435</span></div>
      </div></div>''')
    assert fields["doublechance_prob"] == 76
    assert fields["doublechance_pick_raw"] == "12"
    assert fields["doublechance_pick"] == "12"
    assert fields["doublechance_pick_price_am"] == -435


def test_dom_double_chance_keeps_21_raw_and_dash_price_missing():
    fields = _detail('''
      <div id="dbc_table"><div class="rcnt">
        <div class="fprc"><span class="fpr">70%</span></div>
        <div class="predict"><span class="forepr">21</span></div>
        <div class="prmod"><span class="lscrsp">-</span></div>
      </div></div>''')
    assert fields["doublechance_pick_raw"] == "21"
    assert "doublechance_pick" not in fields
    assert "doublechance_pick_price_am" not in fields


def test_dom_goalscorers_pair_three_aligned_names_and_probabilities():
    fields = _detail('''
      <div id="gscr_table" class="schema tbgscr"><div class="rcnt">
        <div class="fprc"><div class="playerPred">19%</div><div class="playerPred">18%</div><div class="playerPred">18%</div></div>
        <div class="predict"><span class="forepr"><span><div class="playerPred">Tulio</div><div class="playerPred">Dulay</div><div class="playerPred">Tesar</div></span></span></div>
        <div class="prmod"><span class="lscrsp">-</span><span class="lscrsp">-</span><span class="lscrsp">-</span></div>
      </div></div>''')
    assert [(fields[f"goalscorer_{n}_name"], fields[f"goalscorer_{n}_prob"]) for n in range(1, 4)] == [
        ("Tulio", 19), ("Dulay", 18), ("Tesar", 18)
    ]
    assert not any(key.endswith("_price_am") for key in fields)


def test_dom_empty_goalscorer_row_emits_nothing():
    fields = _detail('<div id="gscr_table"><div class="rcnt"><div class="predict">1 - 2</div></div></div>')
    assert not any(key.startswith("goalscorer_") for key in fields)


def test_dom_mismatched_goalscorer_counts_emit_no_partial_fields():
    fields = _detail('''
      <div id="gscr_table"><div class="rcnt">
        <div class="fprc"><div class="playerPred">18%</div><div class="playerPred">12%</div></div>
        <div class="predict"><span class="forepr"><div class="playerPred">Bačić</div></span></div>
      </div></div>''')
    assert not any(key.startswith("goalscorer_") for key in fields)


def test_dom_market_selectors_ignore_lookalikes_and_scorer_percentages():
    fields = _detail('''
      <div class="fprc"><span class="fpr">99%</span><div class="playerPred">18%</div></div>
      <div class="predict"><span class="forepr">12</span><div class="playerPred">Outside</div></div>
      <div class="prmod"><span class="lscrsp">-111</span></div>''')
    assert "doublechance_prob" not in fields
    assert "doublechance_pick" not in fields
    assert not any(key.startswith("goalscorer_") for key in fields)
