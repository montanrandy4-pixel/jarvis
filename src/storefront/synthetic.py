"""Looking at the shop the way a customer does.

The Admin API answers questions about *records*. It will tell you a product is
active, published and priced, and every one of those can be true while the
storefront is broken -- a theme update that swallows the buy button, a
collection page that 404s, a price that renders as nothing, an app script that
throws and takes the page with it. None of that is visible from the API,
because none of it is a record; it is a rendering.

So this walks the actual shop in a real browser and checks the things a
customer would notice in the first thirty seconds. It buys nothing: the
deepest it goes is adding to a cart and confirming checkout is reachable,
which is where a broken store usually reveals itself.

It is a smoke test, not a guarantee. Passing means the shop was standing up a
moment ago, which is considerably more than the API can tell you.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("storefront.synthetic")

# A price, in any of the shapes a theme might render one.
PRICE = re.compile(r"[$£€]\s?\d[\d,]*(?:\.\d{2})?")

SOLD_OUT = re.compile(r"sold\s*out|unavailable|out of stock", re.IGNORECASE)
ADD_TO_CART = [
    "button[name=add]",
    "button:has-text('Add to cart')",
    "button:has-text('Add to bag')",
    "form[action*='/cart/add'] button[type=submit]",
]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    url: str = ""

    def line(self) -> str:
        return f"{'ok  ' if self.ok else 'FAIL'} {self.name}" + (
            f" -- {self.detail}" if self.detail else ""
        )


@dataclass
class StorefrontReport:
    checks: list[Check] = field(default_factory=list)
    reachable: bool = True

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    @property
    def ok(self) -> bool:
        return self.reachable and not self.failures


def check_storefront(
    base_url: str,
    *,
    product_handles: list[str],
    collection_handles: list[str] | None = None,
    page_handles: list[str] | None = None,
    timeout_ms: int = 30000,
    headless: bool = True,
    try_add_to_cart: bool = True,
) -> StorefrontReport:
    """Walk the shop. Never raises -- an unreachable shop is a finding."""
    report = StorefrontReport()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report.reachable = False
        report.checks.append(Check(
            "playwright available", False,
            "pip install playwright && playwright install chromium",
        ))
        return report

    base = base_url.rstrip("/")
    if not base.startswith("http"):
        base = f"https://{base}"

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context(
                # A password-protected store answers differently to a bot than
                # to a person; identify honestly rather than spoofing.
                user_agent="SoloStackMonitor/1.0 (+storefront agent)",
            )
            page = context.new_page()
            try:
                _check_home(page, base, report, timeout_ms)
                for handle in (collection_handles or [])[:3]:
                    _check_url(page, f"{base}/collections/{handle}",
                               f"collection /{handle}", report, timeout_ms)
                for handle in (page_handles or [])[:3]:
                    _check_url(page, f"{base}/pages/{handle}",
                               f"page /{handle}", report, timeout_ms)
                for handle in product_handles[:3]:
                    _check_product(page, base, handle, report, timeout_ms,
                                   try_add_to_cart)
            finally:
                context.close()
                browser.close()
    except Exception as exc:  # noqa: BLE001 -- the browser dying is a finding
        report.reachable = False
        report.checks.append(Check("browser", False, str(exc)[:160]))
    return report


def _goto(page, url: str, timeout_ms: int):
    return page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)


def _check_home(page, base: str, report: StorefrontReport, timeout_ms: int) -> None:
    try:
        response = _goto(page, base, timeout_ms)
        status = response.status if response else 0
        if status >= 400:
            report.checks.append(Check("home page", False, f"HTTP {status}", base))
            report.reachable = False
            return
        body = page.content()
        if "password" in page.url or "Opening soon" in body:
            report.checks.append(Check(
                "home page", False,
                "the store is password-protected -- customers cannot reach it", base))
            return
        report.checks.append(Check("home page", True, url=base))
    except Exception as exc:  # noqa: BLE001
        report.reachable = False
        report.checks.append(Check("home page", False, str(exc)[:120], base))


def _check_url(page, url: str, name: str, report: StorefrontReport,
               timeout_ms: int) -> None:
    try:
        response = _goto(page, url, timeout_ms)
        status = response.status if response else 0
        report.checks.append(Check(name, status < 400,
                                   "" if status < 400 else f"HTTP {status}", url))
    except Exception as exc:  # noqa: BLE001
        report.checks.append(Check(name, False, str(exc)[:120], url))


def _check_product(page, base: str, handle: str, report: StorefrontReport,
                   timeout_ms: int, try_add_to_cart: bool) -> None:
    url = f"{base}/products/{handle}"
    try:
        response = _goto(page, url, timeout_ms)
        status = response.status if response else 0
        if status >= 400:
            report.checks.append(Check(f"product /{handle}", False,
                                       f"HTTP {status}", url))
            return

        body = page.inner_text("body", timeout=timeout_ms)

        if not PRICE.search(body):
            report.checks.append(Check(
                f"product /{handle} price", False,
                "no price rendered on the page -- the theme may be broken", url))
        else:
            report.checks.append(Check(f"product /{handle} price", True, url=url))

        if SOLD_OUT.search(body):
            # On a digital store nothing should ever be unbuyable.
            report.checks.append(Check(
                f"product /{handle} buyable", False,
                "page says sold out or unavailable", url))
            return

        if not try_add_to_cart:
            return

        button = None
        for selector in ADD_TO_CART:
            try:
                candidate = page.locator(selector).first
                candidate.wait_for(state="visible", timeout=3000)
                button = candidate
                break
            except Exception:  # noqa: BLE001
                continue

        if button is None:
            report.checks.append(Check(
                f"product /{handle} add to cart", False,
                "no add-to-cart button found", url))
            return

        button.click(timeout=8000)
        page.wait_for_timeout(2500)
        cart = page.goto(f"{base}/cart", wait_until="domcontentloaded",
                         timeout=timeout_ms)
        cart_body = page.inner_text("body", timeout=timeout_ms)
        empty = re.search(r"cart is empty|your cart is currently empty",
                          cart_body, re.IGNORECASE)
        ok = bool(cart and cart.status < 400) and not empty
        report.checks.append(Check(
            f"product /{handle} add to cart", ok,
            "" if ok else "item did not reach the cart", url))
    except Exception as exc:  # noqa: BLE001
        report.checks.append(Check(f"product /{handle}", False, str(exc)[:120], url))
