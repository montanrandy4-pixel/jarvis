"""Fixtures for the shop tests."""

from __future__ import annotations

import pytest
from fake_shopify import FakeShopify


@pytest.fixture
def api():
    server = FakeShopify()
    yield server
    server.stop()
