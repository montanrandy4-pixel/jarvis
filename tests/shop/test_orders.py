"""Order triage: what gets tagged, and what gets a human."""

from __future__ import annotations

import pytest
from fake_shopify import TOKEN

from shop import orders
from shop.client import ShopifyClient
from shop.config import Config
from shop.ledger import Entry, Ledger
from shop.orders import REVIEW_TAG, SEEN_TAG


@pytest.fixture
def config(tmp_path):
    config = Config.load(
        store_domain="test.myshopify.com",
        state_dir=tmp_path / "state",
        review_above=250.0,
        dry_run=False,
    )
    config._known_skus = {"A-1"}
    return config


@pytest.fixture
def live(api):
    return ShopifyClient(api.endpoint, TOKEN, dry_run=False)


def test_an_ordinary_order_is_marked_seen_and_left_alone(api, live, config):
    api.seed_order("#1001", total="61.99", sku="A-1")

    decided = orders.process(orders.fetch_orders(live), live, config)

    assert len(decided) == 1
    assert not decided[0].needs_review
    assert api.orders[0]["tags"] == [SEEN_TAG]


def test_a_large_order_is_flagged_for_review(api, live, config):
    api.seed_order("#1002", total="980.00", sku="A-1")

    decided = orders.process(orders.fetch_orders(live), live, config)

    assert decided[0].needs_review
    assert "980.00 is at or above 250.0" in decided[0].reasons[0]
    assert REVIEW_TAG in api.orders[0]["tags"]


def test_an_unpaid_order_is_flagged(api, live, config):
    api.seed_order("#1003", total="10.00", sku="A-1", status="PENDING")

    decided = orders.process(orders.fetch_orders(live), live, config)

    assert decided[0].needs_review
    assert "payment is pending" in decided[0].reasons


def test_an_order_for_something_we_do_not_manage_is_flagged(api, live, config):
    api.seed_order("#1004", total="10.00", sku="MYSTERY-9")

    decided = orders.process(orders.fetch_orders(live), live, config)

    assert decided[0].needs_review
    assert "do not manage" in decided[0].reasons[0]


def test_orders_already_seen_are_not_fetched_again(api, live, config):
    api.seed_order("#1005", total="10.00", sku="A-1")
    orders.process(orders.fetch_orders(live), live, config)

    assert orders.fetch_orders(live) == []


def test_a_dry_run_decides_without_tagging(api, config):
    api.seed_order("#1006", total="980.00", sku="A-1")
    dry = ShopifyClient(api.endpoint, TOKEN, dry_run=True)

    decided = orders.process(orders.fetch_orders(dry), dry, config)

    assert decided[0].needs_review
    assert api.orders[0]["tags"] == []  # nothing was written


def test_low_stock_lists_the_worst_first(tmp_path):
    ledger = Ledger(tmp_path / "l.json")
    ledger.remember(Entry(sku="A-1", quantity=0))
    ledger.remember(Entry(sku="A-2", quantity=2))
    ledger.remember(Entry(sku="A-3", quantity=50))
    ledger.remember(Entry(sku="OLD", quantity=0, retired=True))

    assert orders.low_stock(ledger, 3) == [("A-1", 0), ("A-2", 2)]
