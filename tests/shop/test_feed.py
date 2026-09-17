"""Reading whatever the supplier sends."""

from __future__ import annotations

import json

import pytest

from shop.feed import (FeedItem, map_columns, parse_money, parse_quantity, read,
                       slugify, split_list)


class TestParsingValues:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("12.99", 12.99), ("£12.99", 12.99), ("$1,299.00", 1299.0),
            ("12,99", 12.99), ("12.99 EUR", 12.99), (5, 5.0), (5.5, 5.5),
            ("", None), (None, None), ("call for price", None), (True, None),
        ],
    )
    def test_money_survives_currency_symbols_and_separators(self, raw, expected):
        assert parse_money(raw) == expected

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("7", 7), (3, 3), (3.9, 3), ("in stock", 10), ("Out of Stock", 0),
            ("discontinued", 0), ("", 0), (None, 0), ("-5", 0), (True, 10),
        ],
    )
    def test_stock_handles_numbers_and_words(self, raw, expected):
        assert parse_quantity(raw) == expected

    def test_lists_split_on_whatever_separator_was_used(self):
        assert split_list("a|b, c;d") == ["a", "b", "c", "d"]
        assert split_list(["x", " y "]) == ["x", "y"]
        assert split_list(None) == []

    def test_handles_are_stable_and_url_safe(self):
        item = FeedItem(sku="A/1", title="Brass Lamp (large)", cost=1.0)
        assert item.handle == "brass-lamp-large-a-1"
        assert slugify("  Hello -- World  ") == "hello-world"


class TestColumnMapping:
    def test_common_aliases_are_recognised(self):
        mapping = map_columns(
            ["Item Number", "Product Name", "Wholesale Price", "QTY", "Image URL"]
        )

        assert mapping["sku"] == "Item Number"
        assert mapping["title"] == "Product Name"
        assert mapping["cost"] == "Wholesale Price"
        assert mapping["quantity"] == "QTY"
        assert mapping["images"] == "Image URL"

    def test_an_explicit_override_wins(self):
        mapping = map_columns(["sku", "price", "cost"], {"cost": "price"})

        assert mapping["cost"] == "price"


class TestReading:
    def test_a_comma_csv(self, tmp_path):
        path = tmp_path / "f.csv"
        path.write_text("sku,title,cost,quantity\nA-1,Lamp,10,3\n")

        report = read(str(path))

        assert [(i.sku, i.cost, i.quantity) for i in report.items] == [("A-1", 10.0, 3)]

    def test_a_semicolon_csv_with_european_decimals(self, tmp_path):
        path = tmp_path / "f.csv"
        path.write_text("sku;title;cost\nA-1;Lamp;24,50\n")

        assert read(str(path)).items[0].cost == 24.5

    def test_a_tab_separated_feed(self, tmp_path):
        path = tmp_path / "f.tsv"
        path.write_text("sku\ttitle\tcost\nA-1\tLamp\t10\n")

        assert read(str(path)).items[0].sku == "A-1"

    def test_a_json_feed(self, tmp_path):
        path = tmp_path / "f.json"
        path.write_text(json.dumps(
            {"products": [{"sku": "A-1", "name": "Lamp", "wholesale": 10,
                           "images": ["http://x/1.jpg"]}]}))

        item = read(str(path)).items[0]

        assert item.title == "Lamp" and item.images == ["http://x/1.jpg"]

    def test_rows_without_a_sku_or_cost_are_rejected_with_a_reason(self, tmp_path):
        path = tmp_path / "f.csv"
        path.write_text("sku,title,cost\n,No SKU,5\nA-2,No cost,\nA-3,Fine,5\n")

        report = read(str(path))

        assert [i.sku for i in report.items] == ["A-3"]
        assert "no sku" in report.rejected[0][1]
        assert "no usable cost" in report.rejected[1][1]

    def test_duplicate_skus_are_rejected_not_merged(self, tmp_path):
        path = tmp_path / "f.csv"
        path.write_text("sku,title,cost\nA-1,First,5\nA-1,Second,6\n")

        report = read(str(path))

        assert len(report.items) == 1
        assert "duplicate" in report.rejected[0][1]

    def test_a_feed_with_no_usable_columns_says_which_are_missing(self, tmp_path):
        path = tmp_path / "f.csv"
        path.write_text("colour,size\nred,large\n")

        with pytest.raises(ValueError, match="no column for"):
            read(str(path))

    def test_a_retail_only_feed_falls_back_to_the_retail_price_as_cost(self, tmp_path):
        path = tmp_path / "f.csv"
        path.write_text("sku,title,retail price\nA-1,Lamp,20\n")

        assert read(str(path)).items[0].cost == 20.0

    def test_a_missing_file_is_reported_clearly(self, tmp_path):
        with pytest.raises(ValueError, match="no feed file"):
            read(str(tmp_path / "nope.csv"))

    def test_a_byte_order_mark_does_not_break_the_first_column(self, tmp_path):
        path = tmp_path / "f.csv"
        path.write_bytes("sku,title,cost\nA-1,Lamp,10\n".encode("utf-8-sig"))

        assert read(str(path)).items[0].sku == "A-1"
