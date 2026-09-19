"""Order triage: the concerns that matter on a store selling files."""

from __future__ import annotations

from storefront import orders, report


def order(**over):
    base = {
        "id": "gid://shopify/Order/1",
        "name": "#1001",
        "createdAt": "2026-09-18T10:00:00Z",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "FULFILLED",
        "email": "buyer@example.com",
        "tags": [],
        "currentTotalPriceSet": {"shopMoney": {"amount": "39.00", "currencyCode": "USD"}},
        "customer": {"id": "gid://shopify/Customer/1", "displayName": "Dana",
                     "numberOfOrders": 1},
        "lineItems": {"nodes": [{"title": "Contract Pack", "quantity": 1,
                                 "sku": "SS-CONTRACT-PACK", "requiresShipping": False,
                                 "variant": {"id": "gid://shopify/ProductVariant/1"}}]},
    }
    base.update(over)
    return base


KNOWN = {"SS-CONTRACT-PACK"}


def test_a_clean_paid_order_has_no_concerns():
    view = orders.review([order()], known_skus=KNOWN)[0]
    assert not view.needs_review
    assert view.total == 39.0


def test_unpaid_order_is_flagged_because_the_file_may_not_have_gone():
    view = orders.review([order(displayFinancialStatus="PENDING")], known_skus=KNOWN)[0]
    assert any("payment is pending" in c for c in view.concerns)


def test_unknown_sku_is_flagged():
    view = orders.review([order()], known_skus=set())[0]
    assert any("no file recorded" in c for c in view.concerns)


def test_line_item_requiring_shipping_is_flagged():
    o = order()
    o["lineItems"]["nodes"][0]["requiresShipping"] = True
    view = orders.review([o], known_skus=KNOWN)[0]
    assert any("requires shipping" in c for c in view.concerns)


def test_large_order_is_flagged_as_possible_card_testing():
    o = order(currentTotalPriceSet={"shopMoney": {"amount": "900.00",
                                                  "currencyCode": "USD"}})
    view = orders.review([o], known_skus=KNOWN, review_above=250)[0]
    assert any("at or above" in c for c in view.concerns)


def test_partially_refunded_still_counts_as_paid():
    view = orders.review([order(displayFinancialStatus="PARTIALLY_REFUNDED")],
                         known_skus=KNOWN)[0]
    assert not any("payment is" in c for c in view.concerns)


def test_seen_orders_are_recognised():
    view = orders.review([order(tags=[orders.SEEN_TAG])], known_skus=KNOWN)[0]
    assert view.seen


def test_summary_totals_and_ranking():
    nodes = [order(), order(id="gid://shopify/Order/2", name="#1002",
                            currentTotalPriceSet={"shopMoney": {"amount": "149.00",
                                                                "currencyCode": "USD"}})]
    views = orders.review(nodes, known_skus=KNOWN)
    s = report.summarise(nodes, views)
    assert s.orders == 2
    assert s.revenue == 188.0
    assert s.average_order == 94.0
    assert s.by_product["Contract Pack"] == 2


def test_report_renders_without_orders():
    s = report.summarise([], [])
    assert "no orders" in report.render(s, period="Last 7 day(s)")
