from conftest import make, metric

from onepager.evidence import is_prohibited_estimate, pdf_eligible, pending_estimates, unresolved_conflicts
from onepager.models import Evidence, Metric, MetricKind, Point


def _m(**kw) -> Metric:
    base = dict(id="x", label="x", value="$1M", evidence="reported", source_refs=["Slide 1"])
    return Metric(**{**base, **kw})


def test_reported_and_calculated_are_eligible():
    assert pdf_eligible("traction", _m())
    assert pdf_eligible("traction", _m(evidence="calculated", formula="a/b"))


def test_estimates_need_approval():
    est = _m(evidence="estimated", kind="som", assumptions=["3% of SAM"])
    assert not pdf_eligible("market_size", est)
    est.approved = True
    assert pdf_eligible("market_size", est)


def test_prohibited_estimates_never_eligible_even_if_approved():
    for kind in (MetricKind.REVENUE, MetricKind.PAYING_CUSTOMERS, MetricKind.COMMITMENT_SIGNED,
                 MetricKind.VALUATION_CAP, MetricKind.REGULATORY):
        est = _m(evidence="estimated", kind=kind, approved=True)
        assert is_prohibited_estimate("traction", est)
        assert not pdf_eligible("traction", est)
    team_claim = Point(text="Deep sales expertise", evidence="estimated", approved=True)
    assert is_prohibited_estimate("team", team_claim)
    assert not pdf_eligible("team", team_claim)


def test_conflicting_and_not_provided_excluded():
    assert not pdf_eligible("traction", _m(evidence="conflicting", value=None))
    assert not pdf_eligible("traction", _m(evidence="not_provided", value=None))


def test_exit_scenarios_only_when_requested():
    ev = _m(evidence="estimated", kind="exit_value", approved=True, assumptions=["5x revenue"])
    assert not pdf_eligible("exit", ev, exit_scenarios=False)
    assert pdf_eligible("exit", ev, exit_scenarios=True)


def test_demo_has_each_label_and_pending_items(demo):
    labels = {m.evidence for _, s in [(k, getattr(demo.sections, k)) for k in type(demo.sections).model_fields]
              for m in s.metrics}
    assert {Evidence.REPORTED, Evidence.CALCULATED, Evidence.ESTIMATED, Evidence.CONFLICTING} <= labels
    assert len(pending_estimates(demo)) == 3
    assert len(unresolved_conflicts(demo)) == 1


def test_model_accepts_missing_value_for_not_provided(demo_dict):
    sec = demo_dict["sections"]["traction"]
    sec["metrics"].append({"id": "gm", "label": "gross margin", "value": None, "evidence": "not_provided"})
    a = make(demo_dict)
    assert metric(a.model_dump(mode="json")["sections"]["traction"], "gm")["value"] is None
