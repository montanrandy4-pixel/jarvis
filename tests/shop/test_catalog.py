"""Reconciling a feed with the store: planning, applying, and doing neither twice."""

from __future__ import annotations

import pytest
from fake_shopify import TOKEN

from shop import catalog
from shop.catalog import CREATE, HOLD, REPRICE, RESTOCK, RETIRE
from shop.client import ShopifyClient
from shop.config import Config, PricingRules
from shop.feed import read
from shop.ledger import Ledger

FEED = """sku,title,cost,quantity,description,image
A-1,Brass Desk Lamp,24.50,7,A small brass lamp.,http://img/1.jpg
A-2,Oak Shelf,60.00,0,Solid oak.,
"""


@pytest.fixture
def config(tmp_path):
    feed_path = tmp_path / "feed.csv"
    feed_path.write_text(FEED)
    return Config.load(
        store_domain="test.myshopify.com",
        feed_path=str(feed_path),
        state_dir=tmp_path / "state",
        dry_run=False,
        pricing=PricingRules(markup=2.5, charm_ending="0.99"),
    )


@pytest.fixture
def live(api):
    return ShopifyClient(api.endpoint, TOKEN, dry_run=False)


def sync(config, client, api):
    """Plan and apply one pass, the way the autopilot does."""
    report = read(config.feed_path)
    ledger = Ledger.load(config.ledger_path)
    store = catalog.fetch_store_products(client, config)
    plan = catalog.plan(report, store, ledger, config)
    location = catalog.first_location(client, config.location)
    applied = catalog.apply(plan, client, ledger, config, location)
    return plan, applied


class TestPlanning:
    def test_an_empty_store_plans_a_creation_for_every_product(self, config, live, api):
        report = read(config.feed_path)
        plan = catalog.plan(report, {}, Ledger.load(config.ledger_path), config)

        assert len(plan.of(CREATE)) == 2
        assert {a.sku for a in plan.of(CREATE)} == {"A-1", "A-2"}
        assert plan.of(CREATE)[0].price == "61.99"  # 24.50 x 2.5, charmed

    def test_a_price_move_plans_a_reprice(self, config, live, api):
        api.seed_product("A-1", price="50.00", quantity=7)
        api.seed_product("A-2", price="150.99", quantity=0)
        report = read(config.feed_path)
        store = catalog.fetch_store_products(live, config)

        plan = catalog.plan(report, store, Ledger.load(config.ledger_path), config)

        repriced = plan.of(REPRICE)
        assert [a.sku for a in repriced] == ["A-1"]
        assert repriced[0].reason == "50.00 -> 61.99"

    def test_a_stock_move_plans_a_restock(self, config, live, api):
        api.seed_product("A-1", price="61.99", quantity=2)
        api.seed_product("A-2", price="150.99", quantity=0)
        store = catalog.fetch_store_products(live, config)

        plan = catalog.plan(read(config.feed_path), store,
                            Ledger.load(config.ledger_path), config)

        assert [(a.sku, a.quantity) for a in plan.of(RESTOCK)] == [("A-1", 7)]

    def test_products_no_longer_in_the_feed_are_retired(self, config, live, api):
        api.seed_product("A-1", price="61.99", quantity=7)
        api.seed_product("A-2", price="150.99", quantity=0)
        api.seed_product("GONE-9", title="Discontinued", price="5.00")
        store = catalog.fetch_store_products(live, config)

        plan = catalog.plan(read(config.feed_path), store,
                            Ledger.load(config.ledger_path), config)

        assert [a.sku for a in plan.of(RETIRE)] == ["GONE-9"]

    def test_an_unprofitable_price_is_held_not_published(self, config, live, api):
        config.pricing = PricingRules(markup=0.5, charm_ending="0.99")

        plan = catalog.plan(read(config.feed_path), {},
                            Ledger.load(config.ledger_path), config)

        assert len(plan.of(HOLD)) == 2
        assert "would not cover the cost" in plan.of(HOLD)[0].reason
        assert plan.writes == []  # held items are never written

    def test_a_price_above_the_ceiling_is_held(self, config, live, api):
        config.pricing = PricingRules(markup=2.5, ceiling=100.0)

        plan = catalog.plan(read(config.feed_path), {},
                            Ledger.load(config.ledger_path), config)

        held = {a.sku for a in plan.of(HOLD)}
        assert held == {"A-2"}  # 150.99 is over the ceiling, 61.99 is not


class TestApplying:
    def test_a_full_sync_creates_priced_stocked_products(self, config, live, api):
        plan, applied = sync(config, live, api)

        assert applied["created"] == 2 and applied["failed"] == 0
        lamp = api.variant_of("A-1")
        assert lamp["price"] == "61.99"
        assert lamp["sku"] == "A-1"
        assert lamp["inventoryQuantity"] == 7
        assert lamp["inventoryItem"]["tracked"] is True
        assert api.product_of("A-1")["title"] == "Brass Desk Lamp"

    def test_new_products_are_created_as_drafts(self, config, live, api):
        sync(config, live, api)

        # Publishing is a decision for a person, not a side effect of a sync.
        assert api.product_of("A-1")["status"] == "DRAFT"

    def test_images_are_attached(self, config, live, api):
        sync(config, live, api)

        product_id = api.product_of("A-1")["id"]
        assert api.media[product_id][0]["originalSource"] == "http://img/1.jpg"
        assert "A-2" not in str(api.media)  # no image in the feed, none attached

    def test_running_twice_changes_nothing_the_second_time(self, config, live, api):
        sync(config, live, api)
        before = len(api.products)

        second, applied = sync(config, live, api)

        assert len(api.products) == before  # no duplicate catalogue
        assert applied == {"created": 0, "repriced": 0, "restocked": 0,
                           "retired": 0, "failed": 0, "held": 0}
        assert second.unchanged == 2

    def test_a_changed_feed_only_writes_what_changed(self, config, live, api):
        sync(config, live, api)
        api.calls.clear()
        with open(config.feed_path, "w") as handle:
            handle.write(FEED.replace("24.50,7", "30.00,7"))

        plan, applied = sync(config, live, api)

        assert applied["repriced"] == 1 and applied["created"] == 0
        assert applied["restocked"] == 0  # stock did not move, so it was not written
        assert api.variant_of("A-1")["price"] == "75.99"  # 30.00 x 2.5, charmed up
        assert "productCreate" not in api.calls

    def test_a_published_product_dropped_from_the_feed_is_unpublished(
        self, config, live, api
    ):
        sync(config, live, api)
        # Someone reviewed the draft and published it.
        api.product_of("A-2")["status"] = "ACTIVE"
        header, first, *_ = FEED.split("\n")
        with open(config.feed_path, "w") as handle:
            handle.write(f"{header}\n{first}\n")

        _, applied = sync(config, live, api)

        assert applied["retired"] == 1
        # Unpublished, not deleted: the product and its history survive.
        assert api.product_of("A-2")["status"] == "DRAFT"

    def test_an_unpublished_draft_dropped_from_the_feed_is_left_alone(
        self, config, live, api
    ):
        sync(config, live, api)
        header, first, *_ = FEED.split("\n")
        with open(config.feed_path, "w") as handle:
            handle.write(f"{header}\n{first}\n")

        _, applied = sync(config, live, api)

        # It was never on sale, so there is nothing to undo and nothing to write.
        assert applied["retired"] == 0
        assert api.product_of("A-2")["status"] == "DRAFT"

    def test_the_ledger_remembers_what_was_created(self, config, live, api):
        sync(config, live, api)

        ledger = Ledger.load(config.ledger_path)

        assert ledger.live_skus == {"A-1", "A-2"}
        entry = ledger.get("A-1")
        assert entry.product_id and entry.variant_id and entry.inventory_item_id
        assert entry.price == "61.99"

    def test_one_failing_product_does_not_stop_the_others(self, config, live, api):
        # A handle collision makes the first creation fail.
        api.seed_product("OTHER", title="Brass Desk Lamp")
        api.products[next(iter(api.products))]["handle"] = "brass-desk-lamp-a-1"

        _, applied = sync(config, live, api)

        assert applied["failed"] == 1
        assert applied["created"] == 1
        assert api.variant_of("A-2") is not None


class TestDryRun:
    def test_a_dry_run_writes_nothing_but_still_plans(self, config, api):
        dry = ShopifyClient(api.endpoint, TOKEN, dry_run=True)
        config.dry_run = True

        report = read(config.feed_path)
        store = catalog.fetch_store_products(dry, config)
        plan = catalog.plan(report, store, Ledger.load(config.ledger_path), config)

        assert len(plan.of(CREATE)) == 2
        assert api.products == {}
