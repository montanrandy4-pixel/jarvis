"""The workspace server: what it serves, and what it must never serve."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from storefront import agent as agent_mod, audit, server
from storefront.alerts import AlertState, Dispatcher
from storefront.config import Config


@pytest.fixture
def workspace(tmp_path):
    config = Config(store_domain="shop.myshopify.com", state_dir=tmp_path)
    dispatcher = Dispatcher(state=AlertState.load(tmp_path / "a.json"), console=False)
    return server.Workspace(config=config, dispatcher=dispatcher)


@pytest.fixture
def running(workspace):
    httpd = server.build(workspace, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield workspace, f"http://127.0.0.1:{port}"
    httpd.shutdown()


def get(url, path):
    with urllib.request.urlopen(url + path, timeout=5) as response:
        return response.status, response.read()


def test_serves_the_page(running):
    _, url = running
    status, body = get(url, "/")
    assert status == 200
    assert b"Shop Workspace" in body


def test_serves_static_assets(running):
    _, url = running
    for name in ("styles.css", "workspace.js", "hud.js"):
        status, body = get(url, f"/static/{name}")
        assert status == 200 and body


def test_state_endpoint_is_json(running):
    _, url = running
    status, body = get(url, "/api/state")
    assert status == 200
    payload = json.loads(body)
    assert payload["shop"]["domain"] == "shop.myshopify.com"
    assert "products" in payload and "alerts" in payload


def test_traversal_outside_the_web_root_is_refused(running):
    """A served path must never escape the web directory."""
    _, url = running
    for attempt in ("/static/../server.py", "/static/../../pyproject.toml"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(url, attempt)
        assert exc.value.code == 404


def test_unknown_routes_404(running):
    _, url = running
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(url, "/secrets")
    assert exc.value.code == 404


def test_the_token_is_never_exposed(running, monkeypatch):
    """The browser gets findings. It must never get a credential."""
    workspace, url = running
    monkeypatch.setenv("SHOPIFY_ADMIN_TOKEN", "shpat_supersecret")
    _, body = get(url, "/api/state")
    assert b"shpat_" not in body
    _, page = get(url, "/")
    assert b"shpat_" not in page


def test_listeners_are_capped(workspace):
    queues = [workspace.listen() for _ in range(server.MAX_LISTENERS)]
    assert all(q is not None for q in queues)
    assert workspace.listen() is None          # the next one is refused
    workspace.drop(queues[0])
    assert workspace.listen() is not None      # a freed slot is reusable


def test_a_slow_listener_is_dropped_not_allowed_to_block(workspace):
    q = workspace.listen()
    for _ in range(200):                       # far beyond the queue's size
        workspace.emit("noise")
    assert workspace.listen() is not None      # the loop never blocked


def test_products_include_healthy_ones_not_just_broken_ones(workspace):
    """A clean catalogue must still render, or the workspace looks empty."""
    result = agent_mod.PassResult(
        audit_report=audit.AuditReport(products_checked=2, findings=[
            audit.Finding(audit.BLOCKER, "Broken One", "no file", "attach it"),
        ]),
        catalog=[
            {"title": "Broken One", "sku": "A", "price": 39.0, "handle": "a", "status": "ACTIVE"},
            {"title": "Healthy One", "sku": "B", "price": 19.0, "handle": "b", "status": "ACTIVE"},
        ],
    )
    workspace._absorb_catalog(result)
    states = {p["title"]: p["state"] for p in workspace.products}
    assert states == {"Broken One": "blocker", "Healthy One": "ok"}


def test_price_reaches_the_view_so_height_means_something(workspace):
    result = agent_mod.PassResult(
        audit_report=audit.AuditReport(products_checked=1),
        catalog=[{"title": "T", "sku": "S", "price": 149.0, "handle": "h", "status": "ACTIVE"}],
    )
    workspace._absorb_catalog(result)
    assert workspace.products[0]["price"] == 149.0


def test_worst_severity_wins_when_a_product_has_several_problems(workspace):
    result = agent_mod.PassResult(
        audit_report=audit.AuditReport(products_checked=1, findings=[
            audit.Finding(audit.NOTE, "P", "no tags", "tag it"),
            audit.Finding(audit.BLOCKER, "P", "no file", "attach it"),
            audit.Finding(audit.WARNING, "P", "no image", "add one"),
        ]),
        catalog=[{"title": "P", "sku": "S", "price": 10.0, "handle": "h", "status": "ACTIVE"}],
    )
    workspace._absorb_catalog(result)
    assert workspace.products[0]["state"] == "blocker"
    assert len(workspace.products[0]["problems"]) == 3


def test_describe_unescapes_titles_and_survives_a_bad_price():
    out = agent_mod._describe({
        "title": "Invoice &amp; Payment", "handle": "h", "status": "ACTIVE",
        "variants": {"nodes": [{"sku": "X", "price": "not-a-number"}]},
    })
    assert out["title"] == "Invoice & Payment"
    assert out["price"] == 0.0


def test_describe_handles_a_product_with_no_variants():
    out = agent_mod._describe({"title": "T", "handle": "h", "status": "DRAFT"})
    assert out["sku"] == "" and out["price"] == 0.0
