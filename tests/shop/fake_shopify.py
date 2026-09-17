"""A stand-in Shopify Admin API, so the autopilot can be tested end to end.

It is not a GraphQL engine: it dispatches on the operation name and keeps a
small in-memory store. That is enough to exercise everything the autopilot
does -- creating products, repricing, setting stock, retiring, tagging orders,
pagination, user errors and throttling.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


TOKEN = "shpat_test"


class FakeShopify:
    def __init__(self):
        self.products: dict[str, dict] = {}
        self.orders: list[dict] = []
        self.media: dict[str, list] = {}
        self.inventory: dict[tuple[str, str], int] = {}
        self.activated: set[tuple[str, str]] = set()
        self.calls: list[str] = []
        self.documents: list[dict] = []
        # Test hooks.
        self.throttle_next = 0      # Return THROTTLED this many times.
        self.http_error_next = 0    # Return 500 this many times.
        self.available = 1000.0
        self._next_id = 1

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    # -- helpers ---------------------------------------------------------

    def gid(self, kind: str) -> str:
        self._next_id += 1
        return f"gid://shopify/{kind}/{self._next_id}"

    @property
    def endpoint(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/admin/api/2026-01/graphql.json"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def seed_product(self, sku, title="Existing", price="10.00", quantity=5,
                     status="ACTIVE", tags=("autopilot",)):
        product_id = self.gid("Product")
        variant_id = self.gid("ProductVariant")
        item_id = self.gid("InventoryItem")
        self.products[product_id] = {
            "id": product_id,
            "handle": re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
            "title": title,
            "status": status,
            "tags": list(tags),
            "descriptionHtml": "",
            "variants": {
                "edges": [
                    {
                        "node": {
                            "id": variant_id,
                            "sku": sku,
                            "price": price,
                            "compareAtPrice": None,
                            "inventoryQuantity": quantity,
                            "inventoryItem": {"id": item_id, "tracked": True},
                        }
                    }
                ]
            },
        }
        return product_id

    def seed_order(self, name, total="61.99", sku="A-1", status="PAID", tags=()):
        self.orders.append(
            {
                "id": self.gid("Order"),
                "name": name,
                "createdAt": "2026-09-01T10:00:00Z",
                "displayFulfillmentStatus": "UNFULFILLED",
                "displayFinancialStatus": status,
                "tags": list(tags),
                "totalPriceSet": {
                    "shopMoney": {"amount": total, "currencyCode": "GBP"}
                },
                "customer": {"displayName": "A Customer"},
                "lineItems": {
                    "edges": [{"node": {"id": self.gid("LineItem"),
                                        "title": "Thing", "quantity": 1, "sku": sku}}]
                },
            }
        )

    def variant_of(self, sku: str) -> dict | None:
        for product in self.products.values():
            for edge in product["variants"]["edges"]:
                if edge["node"]["sku"] == sku:
                    return edge["node"]
        return None

    def product_of(self, sku: str) -> dict | None:
        for product in self.products.values():
            for edge in product["variants"]["edges"]:
                if edge["node"]["sku"] == sku:
                    return product
        return None

    # -- the API ---------------------------------------------------------

    def _dispatch(self, document: str, variables: dict) -> dict:
        name = _operation(document)
        self.calls.append(name)
        self.documents.append({"operation": name, "variables": variables})
        handler = getattr(self, f"_op_{name}", None)
        if handler is None:
            return {"errors": [{"message": f"unknown operation {name}"}]}
        return {"data": handler(variables)}

    def _op_shop(self, _variables):
        return {
            "shop": {
                "name": "Test Shop",
                "myshopifyDomain": "test.myshopify.com",
                "currencyCode": "GBP",
                "ianaTimezone": "Europe/London",
            }
        }

    def _op_locations(self, _variables):
        return {
            "locations": {
                "edges": [
                    {
                        "node": {
                            "id": "gid://shopify/Location/1",
                            "name": "Main",
                            "isActive": True,
                            "shipsInventory": True,
                        }
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }

    def _op_managedProducts(self, variables):
        wanted_tag = ""
        query = variables.get("query") or ""
        if query.startswith("tag:"):
            wanted_tag = query[4:]
        nodes = [
            p for p in self.products.values()
            if not wanted_tag or wanted_tag in p.get("tags", [])
        ]
        return {"products": _page(nodes, variables)}

    def _op_orders(self, variables):
        query = variables.get("query") or ""
        excluded = re.findall(r"-tag:(\S+)", query)
        nodes = [
            o for o in self.orders
            if not any(tag in o.get("tags", []) for tag in excluded)
        ]
        return {"orders": _page(nodes, variables)}

    def _op_productCreate(self, variables):
        data = variables["input"]
        handle = data.get("handle", "")
        if any(p["handle"] == handle for p in self.products.values()):
            return {
                "productCreate": {
                    "product": None,
                    "userErrors": [
                        {"field": ["handle"], "message": "Handle is already in use"}
                    ],
                }
            }
        product_id = self.gid("Product")
        variant_id = self.gid("ProductVariant")
        item_id = self.gid("InventoryItem")
        self.products[product_id] = {
            "id": product_id,
            "handle": handle,
            "title": data.get("title", ""),
            "status": data.get("status", "DRAFT"),
            "tags": data.get("tags", []),
            "descriptionHtml": data.get("descriptionHtml", ""),
            "variants": {
                "edges": [
                    {
                        "node": {
                            "id": variant_id,
                            "sku": "",
                            "price": "0.00",
                            "compareAtPrice": None,
                            "inventoryQuantity": 0,
                            "inventoryItem": {"id": item_id, "tracked": False},
                        }
                    }
                ]
            },
        }
        return {
            "productCreate": {
                "product": {
                    "id": product_id,
                    "handle": handle,
                    "status": self.products[product_id]["status"],
                    "variants": {
                        "edges": [
                            {"node": {"id": variant_id, "sku": "",
                                      "inventoryItem": {"id": item_id}}}
                        ]
                    },
                },
                "userErrors": [],
            }
        }

    def _op_productUpdate(self, variables):
        data = variables["input"]
        product = self.products.get(data.get("id"))
        if not product:
            return {"productUpdate": {"product": None, "userErrors": [
                {"field": ["id"], "message": "Product not found"}]}}
        product.update({k: v for k, v in data.items() if k != "id"})
        return {"productUpdate": {"product": {
            "id": product["id"], "handle": product["handle"],
            "status": product["status"]}, "userErrors": []}}

    def _op_productVariantsBulkUpdate(self, variables):
        product = self.products.get(variables["productId"])
        if not product:
            return {"productVariantsBulkUpdate": {"productVariants": [],
                    "userErrors": [{"message": "Product not found"}]}}
        updated = []
        for wanted in variables["variants"]:
            for edge in product["variants"]["edges"]:
                node = edge["node"]
                if node["id"] != wanted["id"]:
                    continue
                if "price" in wanted:
                    node["price"] = wanted["price"]
                if "compareAtPrice" in wanted:
                    node["compareAtPrice"] = wanted["compareAtPrice"]
                inventory = wanted.get("inventoryItem") or {}
                if inventory.get("sku"):
                    node["sku"] = inventory["sku"]
                if "tracked" in inventory:
                    node["inventoryItem"]["tracked"] = inventory["tracked"]
                updated.append({"id": node["id"], "sku": node["sku"],
                                "price": node["price"],
                                "compareAtPrice": node["compareAtPrice"]})
        return {"productVariantsBulkUpdate": {"productVariants": updated,
                                              "userErrors": []}}

    def _op_productCreateMedia(self, variables):
        self.media.setdefault(variables["productId"], []).extend(variables["media"])
        return {"productCreateMedia": {
            "media": [{"alt": m.get("alt", ""), "status": "READY"}
                      for m in variables["media"]],
            "mediaUserErrors": []}}

    def _op_inventoryActivate(self, variables):
        key = (variables["inventoryItemId"], variables["locationId"])
        if key in self.activated:
            return {"inventoryActivate": {"inventoryLevel": None, "userErrors": [
                {"message": "Inventory item is already stocked at this location"}]}}
        self.activated.add(key)
        return {"inventoryActivate": {"inventoryLevel": {
            "id": "gid://shopify/InventoryLevel/1",
            "quantities": [{"name": "available", "quantity": 0}]}, "userErrors": []}}

    def _op_inventorySetQuantities(self, variables):
        for entry in variables["input"]["quantities"]:
            key = (entry["inventoryItemId"], entry["locationId"])
            self.inventory[key] = entry["quantity"]
            for product in self.products.values():
                for edge in product["variants"]["edges"]:
                    node = edge["node"]
                    if node["inventoryItem"]["id"] == entry["inventoryItemId"]:
                        node["inventoryQuantity"] = entry["quantity"]
        return {"inventorySetQuantities": {
            "inventoryAdjustmentGroup": {"createdAt": "2026-09-17T00:00:00Z",
                                         "reason": "correction"},
            "userErrors": []}}

    def _op_tagsAdd(self, variables):
        for order in self.orders:
            if order["id"] == variables["id"]:
                order["tags"] = sorted(set(order["tags"]) | set(variables["tags"]))
                return {"tagsAdd": {"node": {"id": order["id"]}, "userErrors": []}}
        return {"tagsAdd": {"node": None,
                            "userErrors": [{"message": "Not found"}]}}

    # -- HTTP ------------------------------------------------------------

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.headers.get("X-Shopify-Access-Token") != TOKEN:
                    return self._send(401, {"errors": "Invalid API key or token"})
                if api.http_error_next > 0:
                    api.http_error_next -= 1
                    return self._send(500, {"errors": "Internal error"})

                length = int(self.headers.get("Content-Length", 0) or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")

                if api.throttle_next > 0:
                    api.throttle_next -= 1
                    return self._send(200, {
                        "errors": [{"message": "Throttled",
                                    "extensions": {"code": "THROTTLED"}}],
                        "extensions": {"cost": {"throttleStatus": {
                            "currentlyAvailable": 0, "restoreRate": 50}}},
                    })

                body = api._dispatch(payload.get("query", ""),
                                     payload.get("variables") or {})
                api.available = max(0.0, api.available - 10)
                body["extensions"] = {"cost": {"requestedQueryCost": 10,
                    "throttleStatus": {"maximumAvailable": 1000,
                                       "currentlyAvailable": api.available,
                                       "restoreRate": 50}}}
                self._send(200, body)

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler


def _page(nodes, variables):
    first = variables.get("first") or 50
    after = variables.get("after")
    start = 0
    if after:
        start = int(after)
    window = nodes[start : start + first]
    end = start + len(window)
    return {
        "edges": [{"node": node} for node in window],
        "pageInfo": {"hasNextPage": end < len(nodes), "endCursor": str(end)},
    }


def _operation(document: str) -> str:
    """Name a document: its operation name, or its first selected field."""
    match = re.search(r"\b(?:query|mutation)\s+([A-Za-z_][A-Za-z0-9_]*)", document)
    if match:
        return match.group(1)
    # Anonymous, e.g. "query { shop { ... } }" -- use the first field.
    inner = re.search(r"\{\s*([A-Za-z_][A-Za-z0-9_]*)", document)
    return inner.group(1) if inner else "unknown"
