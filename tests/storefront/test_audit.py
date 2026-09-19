"""The audit is the one thing here that must not be wrong.

Everything else in this package reports; the audit decides whether a store is
safe to take money. A false "all clear" means a customer pays and gets
nothing, so these tests lean on the blocker cases.
"""

from __future__ import annotations

import pytest

from storefront import audit


def product(**over):
    """A product that passes every check, so each test can break one thing."""
    base = {
        "id": "gid://shopify/Product/1",
        "title": "Rate Calculator",
        "status": "ACTIVE",
        "tags": ["money"],
        "publishedAt": "2026-09-17T00:00:00Z",
        "featuredMedia": {"id": "gid://shopify/MediaImage/1"},
        "media": {"nodes": [{"status": "READY", "mediaErrors": []}]},
        "variants": {"nodes": [{
            "id": "gid://shopify/ProductVariant/1",
            "sku": "SS-RATE-CALC",
            "price": "19.00",
            "inventoryItem": {"requiresShipping": False},
        }]},
    }
    base.update(over)
    return base


KNOWN = {"SS-RATE-CALC"}


def blockers(report):
    return [f.problem for f in report.blockers]


def test_a_complete_product_passes():
    report = audit.audit([product()], assets_known=KNOWN)
    assert report.ok
    assert report.findings == []
    assert report.products_checked == 1


def test_missing_file_is_a_blocker():
    report = audit.audit([product()], assets_known=set())
    assert not report.ok
    assert any("receive nothing" in p for p in blockers(report))


def test_requires_shipping_is_a_blocker():
    p = product()
    p["variants"]["nodes"][0]["inventoryItem"]["requiresShipping"] = True
    report = audit.audit([p], assets_known=KNOWN)
    assert any("shipping address" in b for b in blockers(report))


def test_unpublished_but_active_is_a_blocker():
    report = audit.audit([product(publishedAt=None)], assets_known=KNOWN)
    assert any("not published" in b for b in blockers(report))


@pytest.mark.parametrize("price", [None, "", "0.00", "0"])
def test_unpriced_is_a_blocker(price):
    p = product()
    p["variants"]["nodes"][0]["price"] = price
    report = audit.audit([p], assets_known=KNOWN)
    assert any("price is" in b for b in blockers(report))


def test_a_draft_product_raises_no_blockers():
    """A draft cannot disappoint anyone: it is a note, not an alarm."""
    report = audit.audit([product(status="DRAFT")], assets_known=set())
    assert report.ok
    assert [f.severity for f in report.findings] == [audit.NOTE]


def test_missing_image_warns_but_does_not_block():
    report = audit.audit([product(featuredMedia=None)], assets_known=KNOWN)
    assert report.ok
    assert any(f.severity == audit.WARNING for f in report.findings)


def test_failed_media_warns():
    p = product(media={"nodes": [{"status": "FAILED",
                                  "mediaErrors": [{"message": "too large"}]}]})
    report = audit.audit([p], assets_known=KNOWN)
    assert any("too large" in f.problem for f in report.findings)


def test_untagged_product_is_noted_because_collections_will_miss_it():
    report = audit.audit([product(tags=[])], assets_known=KNOWN)
    assert any("smart collections" in f.problem for f in report.findings)


def test_a_variant_with_no_sku_cannot_be_matched_to_a_file():
    p = product()
    p["variants"]["nodes"][0]["sku"] = ""
    report = audit.audit([p], assets_known=KNOWN)
    assert any("receive nothing" in b for b in blockers(report))


def test_expect_digital_off_skips_the_file_check():
    report = audit.audit([product()], assets_known=set(), expect_digital=False)
    assert report.ok


def test_findings_sort_blockers_first():
    p = product(publishedAt=None, featuredMedia=None, tags=[])
    report = audit.audit([p], assets_known=set())
    severities = [f.severity for f in report.sorted()]
    assert severities == sorted(severities, key=lambda s: audit.SEVERITY_ORDER[s])


def test_titles_are_unescaped_for_reading():
    report = audit.audit([product(title="Invoice &amp; Payment")], assets_known=set())
    assert report.findings[0].product == "Invoice & Payment"
