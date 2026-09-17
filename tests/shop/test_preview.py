"""The local preview of the store."""

from __future__ import annotations

import tomllib

import pytest

from shop import preview
from shop.config import Config, PricingRules
from shop.scaffold import FEED_CSV, STORE_TOML
from shop.store import StoreSpec


@pytest.fixture
def spec():
    return StoreSpec.from_dict(tomllib.loads(STORE_TOML))


@pytest.fixture
def config(tmp_path):
    feed = tmp_path / "feed.csv"
    feed.write_text(FEED_CSV)
    return Config.load(
        feed_path=str(feed),
        state_dir=tmp_path / "state",
        pricing=PricingRules(markup=2.5, charm_ending="0.99"),
    )


def test_every_part_of_the_store_gets_a_page(spec, config, tmp_path):
    index = preview.render(spec, config, tmp_path / "out")
    out = index.parent

    assert index.exists()
    assert len(list((out / "products").glob("*.html"))) == 12
    assert len(list((out / "collections").glob("*.html"))) == 4
    assert len(list((out / "pages").glob("*.html"))) == 3
    assert len(list((out / "policies").glob("*.html"))) == 4


def test_the_home_page_shows_the_brand_and_the_range(spec, config, tmp_path):
    index = preview.render(spec, config, tmp_path / "out")
    html = index.read_text()

    assert "Fenwick &amp; Co" in html or "Fenwick & Co" in html
    assert "Honest homewares, made to last" in html
    assert "Brass Desk Lamp" in html


def test_prices_come_from_the_pricing_rules(spec, config, tmp_path):
    out = preview.render(spec, config, tmp_path / "out").parent

    page = (out / "products" / "FL-100.html").read_text()

    assert "£61.99" in page  # 24.50 x 2.5, charmed


def test_a_collection_only_lists_its_own_products(spec, config, tmp_path):
    out = preview.render(spec, config, tmp_path / "out").parent

    lighting = (out / "collections" / "lighting.html").read_text()

    assert "Brass Desk Lamp" in lighting
    assert "Stoneware Mug" not in lighting


def test_policies_are_rendered_as_readable_pages(spec, config, tmp_path):
    out = preview.render(spec, config, tmp_path / "out").parent

    refund = (out / "policies" / "refund.html").read_text()

    assert "<h2>Returns and refunds</h2>" in refund
    assert "30 days" in refund


def test_the_preview_says_it_is_not_a_real_shop(spec, config, tmp_path):
    index = preview.render(spec, config, tmp_path / "out")

    assert "has not been created yet" in index.read_text()


def test_navigation_links_point_at_files_that_exist(spec, config, tmp_path):
    import re

    out = preview.render(spec, config, tmp_path / "out").parent
    html = (out / "index.html").read_text()

    for href in re.findall(r'href="([^"#]+)"', html):
        if href.startswith("http"):
            continue
        assert (out / href).exists(), f"broken link: {href}"


def test_product_titles_are_escaped(spec, config, tmp_path):
    from shop.feed import FeedItem

    original = preview._catalogue
    preview._catalogue = lambda c: (
        [FeedItem(sku="X", title="<script>alert(1)</script>", cost=1.0)],
        {"X": __import__("shop.pricing", fromlist=["price_for"]).price_for(
            1.0, c.pricing)},
    )
    try:
        out = preview.render(spec, config, tmp_path / "out").parent
        assert "<script>alert(1)</script>" not in (out / "index.html").read_text()
    finally:
        preview._catalogue = original


def test_a_missing_feed_still_renders_the_store(spec, tmp_path):
    config = Config.load(feed_path=str(tmp_path / "nope.csv"),
                         state_dir=tmp_path / "state")

    index = preview.render(spec, config, tmp_path / "out")

    assert index.exists()  # pages and policies are still worth reading
