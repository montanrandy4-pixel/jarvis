"""The parts of the storefront check that do not need a browser."""

from __future__ import annotations

from storefront import synthetic


def test_report_is_ok_only_when_reachable_and_clean():
    ok = synthetic.StorefrontReport(checks=[synthetic.Check("home", True)])
    assert ok.ok
    bad = synthetic.StorefrontReport(checks=[synthetic.Check("home", False, "500")])
    assert not bad.ok
    unreachable = synthetic.StorefrontReport(reachable=False)
    assert not unreachable.ok


def test_price_pattern_matches_what_themes_render():
    for text in ["$39.00", "£29", "€149.00", "Price: $19.00 USD", "$1,299.00"]:
        assert synthetic.PRICE.search(text), text


def test_price_pattern_does_not_match_prose_without_a_price():
    assert not synthetic.PRICE.search("Instant download. Unlimited use.")


def test_sold_out_detection_is_case_insensitive():
    for text in ["Sold out", "SOLD OUT", "Currently unavailable", "out of stock"]:
        assert synthetic.SOLD_OUT.search(text), text


def test_missing_playwright_is_a_finding_not_a_crash(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("playwright"):
            raise ImportError("no playwright here")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    report = synthetic.check_storefront("shop.myshopify.com", product_handles=["x"])
    assert not report.ok
    assert "pip install playwright" in report.checks[0].detail
