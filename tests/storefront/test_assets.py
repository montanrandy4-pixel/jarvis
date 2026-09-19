"""The asset ledger: the agent's memory of what has a file behind it."""

from __future__ import annotations

from storefront.assets import Asset, AssetLedger


def test_records_and_reloads(tmp_path):
    f = tmp_path / "toolkit.xlsx"
    f.write_bytes(b"spreadsheet")
    path = tmp_path / "assets.json"

    ledger = AssetLedger.load(path)
    ledger.remember(Asset.from_file("SS-RATE-CALC", f))
    ledger.save()

    again = AssetLedger.load(path)
    assert again.skus == {"SS-RATE-CALC"}
    assert again.assets["SS-RATE-CALC"].bytes == len(b"spreadsheet")


def test_detects_a_file_that_changed_after_upload(tmp_path):
    f = tmp_path / "pack.zip"
    f.write_bytes(b"v1")
    asset = Asset.from_file("SS-CONTRACT-PACK", f)
    assert not asset.stale_against(f)
    f.write_bytes(b"v2 with a fix")
    assert asset.stale_against(f)


def test_stale_lists_changed_sources(tmp_path):
    f = tmp_path / "pack.zip"
    f.write_bytes(b"v1")
    ledger = AssetLedger(path=tmp_path / "a.json")
    ledger.remember(Asset.from_file("SKU", f))
    assert ledger.stale() == []
    f.write_bytes(b"changed")
    assert [a.sku for a in ledger.stale()] == ["SKU"]


def test_a_corrupt_ledger_does_not_stop_the_agent(tmp_path):
    path = tmp_path / "assets.json"
    path.write_text("{not json", "utf-8")
    assert AssetLedger.load(path).assets == {}


def test_forget(tmp_path):
    ledger = AssetLedger(path=tmp_path / "a.json")
    f = tmp_path / "x.zip"
    f.write_bytes(b"x")
    ledger.remember(Asset.from_file("SKU", f))
    assert ledger.forget("SKU")
    assert not ledger.forget("SKU")
