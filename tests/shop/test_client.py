"""The Admin API client: throttling, retries, dry runs and user errors."""

from __future__ import annotations

import pytest
from fake_shopify import TOKEN

from shop import queries
from shop.client import ShopifyClient, ShopifyError, UserError


def client(api, **kwargs) -> ShopifyClient:
    return ShopifyClient(api.endpoint, TOKEN, dry_run=False, **kwargs)


def test_a_query_returns_data(api):
    assert client(api).check()["name"] == "Test Shop"


def test_a_bad_token_says_so_plainly(api):
    with pytest.raises(ShopifyError, match="token was rejected"):
        ShopifyClient(api.endpoint, "wrong", dry_run=False).check()


def test_throttling_is_retried_then_succeeds(api):
    api.throttle_next = 2

    assert client(api).check()["name"] == "Test Shop"
    assert api.calls.count("shop") == 1  # the two throttles never reached dispatch


def test_server_errors_are_retried(api):
    api.http_error_next = 1

    assert client(api).check()["name"] == "Test Shop"


def test_user_errors_become_exceptions(api):
    api.seed_product("A-1", title="Taken")
    taken = api.products[next(iter(api.products))]["handle"]

    with pytest.raises(UserError, match="Handle is already in use"):
        client(api).mutate(
            queries.CREATE_PRODUCT,
            {"input": {"title": "Another", "handle": taken}},
            field_name="productCreate",
        )


def test_dry_run_records_mutations_instead_of_sending_them(api):
    dry = ShopifyClient(api.endpoint, TOKEN, dry_run=True)

    result = dry.mutate(
        queries.CREATE_PRODUCT,
        {"input": {"title": "Nothing"}},
        field_name="productCreate",
    )

    assert result == {}
    assert api.products == {}
    assert [call.operation for call in dry.planned] == ["productCreate"]


def test_dry_run_still_reads(api):
    api.seed_product("A-1")
    dry = ShopifyClient(api.endpoint, TOKEN, dry_run=True)

    assert dry.check()["name"] == "Test Shop"


def test_pagination_follows_cursors(api):
    for index in range(12):
        api.seed_product(f"SKU-{index}", title=f"Product {index}")
    found = list(
        client(api).paginate(
            queries.MANAGED_PRODUCTS, {"query": ""}, path=["products"], page_size=5
        )
    )

    assert len(found) == 12
    assert len({p["id"] for p in found}) == 12


def test_the_cost_bucket_is_tracked(api):
    live = client(api)

    live.check()

    assert live._available < 1000  # read from the response, not guessed
    assert live._restore_rate == 50
