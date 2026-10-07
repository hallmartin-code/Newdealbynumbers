from datetime import date

import pytest
from conftest import make, metric

from onepager.validation import ERROR, evaluate_expression, parse_amount, parse_percent, validate


@pytest.mark.parametrize("text,value,currency", [
    ("$4.2M", 4.2e6, "USD"),
    ("€1.5bn", 1.5e9, "EUR"),
    ("USD 850K", 850e3, "USD"),
    ("1,200,000", 1.2e6, None),
    ("$9.4B", 9.4e9, "USD"),
    ("£3 million", 3e6, "GBP"),
    ("~$0.6M", 0.6e6, "USD"),
])
def test_parse_amount(text, value, currency):
    v, c = parse_amount(text)
    assert v == pytest.approx(value)
    assert c == currency


def test_ranges_are_not_collapsed_to_one_number():
    assert parse_amount("$1-2M")[0] is None
    assert parse_percent("4-6%") is None
    assert parse_percent("38%") == 38


def test_expression_evaluator_is_safe():
    assert evaluate_expression("a / b", {"a": 1.2e6, "b": 14}) == pytest.approx(85714.2857)
    with pytest.raises(ValueError):
        evaluate_expression("__import__('os')", {})


def _errors(analysis, today=date(2026, 10, 1)):
    return [i for i in validate(analysis, today=today) if i.severity == ERROR]


def test_demo_has_no_errors(demo):
    assert _errors(demo) == []


def test_use_of_funds_percentages_must_total_100(demo_dict):
    demo_dict["sections"]["use_of_funds"]["allocations"][0]["percent"] = 50
    msgs = [i.message for i in _errors(make(demo_dict))]
    assert any("sum to 110%" in m for m in msgs)


def test_use_of_funds_amounts_must_match_raise(demo_dict):
    demo_dict["sections"]["use_of_funds"]["allocations"][0]["amount"] = "$2.6M"
    demo_dict["sections"]["use_of_funds"]["allocations"][0]["amount_value"] = 2.6e6
    assert any("Allocation amounts total" in i.message for i in _errors(make(demo_dict)))


def test_calculated_figure_arithmetic_checked(demo_dict):
    m = metric(demo_dict["sections"]["fundraise"], "raise_remaining")
    m["value"], m["numeric_value"] = "$3.9M", 3.9e6
    assert any("Arithmetic check failed" in i.message for i in _errors(make(demo_dict)))


def test_target_minus_cash_must_equal_remaining_when_all_reported(demo_dict):
    m = metric(demo_dict["sections"]["fundraise"], "raise_remaining")
    m.update(evidence="reported", value="$3.0M", numeric_value=3e6, expression=None)
    assert any("does not equal remaining" in i.message for i in _errors(make(demo_dict)))


def test_pre_plus_raise_equals_post(demo_dict):
    sec = demo_dict["sections"]["fundraise"]
    sec["metrics"] += [
        {"id": "pre", "label": "pre-money", "value": "$10M", "numeric_value": 1e7, "kind": "pre_money_valuation",
         "evidence": "reported", "source_refs": ["Slide 8"]},
        {"id": "post", "label": "post-money", "value": "$15M", "numeric_value": 1.5e7, "kind": "post_money_valuation",
         "evidence": "reported", "source_refs": ["Slide 8"]},
    ]
    assert any("Pre-money" in i.message for i in _errors(make(demo_dict)))


def test_market_ladder_tam_sam_som(demo_dict):
    m = metric(demo_dict["sections"]["market_size"], "sam")
    m["value"], m["numeric_value"] = "$12B", 12e9
    assert any("SAM ($12B) is larger than TAM" in i.message for i in _errors(make(demo_dict)))


def test_pilots_counted_as_customers_flagged(demo_dict):
    m = metric(demo_dict["sections"]["traction"], "pilots")
    m["kind"] = "paying_customers"
    assert any("Pilots" in i.message for i in _errors(make(demo_dict)))


def test_arr_labelled_as_revenue_flagged(demo_dict):
    demo_dict["sections"]["traction"]["metrics"].append(
        {"id": "rev", "label": "ARR", "value": "$1.2M", "numeric_value": 1.2e6, "kind": "revenue",
         "period": "June 2026", "evidence": "reported", "source_refs": ["Slide 4"]})
    assert any("labelled as revenue" in i.message for i in _errors(make(demo_dict)))


def test_commitments_are_not_cash(demo_dict):
    m = metric(demo_dict["sections"]["fundraise"], "soft_circled")
    m["kind"] = "cash_received"
    assert any("Commitments or interest are counted as cash" in i.message for i in _errors(make(demo_dict)))


def test_forecast_marked_actual_flagged(demo_dict):
    demo_dict["sections"]["traction"]["metrics"].append(
        {"id": "rev27", "label": "Projected revenue", "value": "$3.5M", "numeric_value": 3.5e6, "kind": "revenue",
         "period": "2027E", "timeframe": "actual", "evidence": "reported", "source_refs": ["Slide 10"]})
    assert any("Projected figure is marked as an actual" in i.message for i in _errors(make(demo_dict)))


def test_display_value_and_units_must_agree(demo_dict):
    m = metric(demo_dict["sections"]["fundraise"], "raise_target")
    m["numeric_value"] = 4000  # thousands slipped in as base units
    assert any("does not match numeric value" in i.message for i in _errors(make(demo_dict)))


def test_gross_margin_over_100_is_an_error(demo_dict):
    demo_dict["sections"]["traction"]["metrics"].append(
        {"id": "gm", "label": "gross margin", "value": "140%", "numeric_value": 140, "unit": "%",
         "kind": "gross_margin", "evidence": "reported", "source_refs": ["Slide 10"]})
    assert any("outside 0-100%" in i.message for i in _errors(make(demo_dict)))


def test_mixed_currencies_flagged_not_converted(demo_dict):
    m = metric(demo_dict["sections"]["market_size"], "tam")
    m["value"], m["unit"] = "€9.4B", "EUR"
    issues = validate(make(demo_dict))
    assert any("Mixed currencies" in i.message for i in issues)
    assert any("different currencies" in i.message for i in issues)


def test_prohibited_estimate_is_an_error(demo_dict):
    m = metric(demo_dict["sections"]["traction"], "paying_customers")
    m["evidence"] = "estimated"
    assert any("must not be estimated" in i.message for i in _errors(make(demo_dict)))
