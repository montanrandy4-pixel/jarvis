"""Planning attachments. The browser half is not unit-testable; the matching is."""

from __future__ import annotations

from pathlib import Path

from storefront import attach


def products():
    return [{
        "id": "gid://shopify/Product/9044754038872",
        "title": "Rate Calculator &amp; Pricing Toolkit",
        "variants": {"nodes": [{"sku": "SS-RATE-CALC"}]},
    }]


def test_matches_a_file_to_a_product_by_sku(tmp_path):
    f = tmp_path / "toolkit.xlsx"
    f.write_bytes(b"x")
    plans, unmatched = attach.plan_from_mapping({"SS-RATE-CALC": f}, products())
    assert unmatched == []
    assert plans[0].product_id.endswith("9044754038872")
    assert plans[0].product_title == "Rate Calculator & Pricing Toolkit"


def test_an_unknown_sku_is_reported_not_silently_dropped(tmp_path):
    f = tmp_path / "x.zip"
    f.write_bytes(b"x")
    plans, unmatched = attach.plan_from_mapping({"SS-NOPE": f}, products())
    assert plans == []
    assert unmatched == ["SS-NOPE"]


def test_a_missing_file_is_reported_before_a_browser_opens(tmp_path):
    plans, unmatched = attach.plan_from_mapping(
        {"SS-RATE-CALC": tmp_path / "gone.zip"}, products())
    assert plans == []
    assert "file not found" in unmatched[0]


def test_dry_run_attaches_nothing(tmp_path):
    f = tmp_path / "toolkit.xlsx"
    f.write_bytes(b"x")
    plans, _ = attach.plan_from_mapping({"SS-RATE-CALC": f}, products())
    attached, failed = attach.attach_all(plans, "shop.myshopify.com", dry_run=True)
    assert attached == []
    assert failed and "dry run" in failed[0][1]
