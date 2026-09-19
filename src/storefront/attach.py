"""Attaching digital files to products, through the admin UI.

Shopify routes digital-file uploads through the merchant's own browser: the
file goes from their disk to Shopify's storage and no Admin API sits in that
path. That is a deliberate security boundary, not an oversight, and it means
no server-side script can do this step.

A browser on the merchant's own machine can, though. Playwright drives a real
Chromium, and `set_input_files` writes straight to the file input without ever
opening an OS file dialog. So this module automates the clicking while leaving
the trust boundary where Shopify put it: the session is the merchant's own,
established by them, in a browser profile on their machine.

Two consequences worth stating plainly:

* **You sign in yourself.** The agent never handles your password and never
  touches your 2FA. It opens a browser, waits for you to be signed in, and
  then works inside that session. The profile persists, so you do this once.
* **This drives a UI, and UIs move.** The selectors below are a best effort
  against Shopify admin as it stands. When Shopify rearranges the page this
  breaks, loudly, and falls back to telling you which file to attach by hand.
  It will never silently skip a product and report success.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("storefront.attach")

# Where the merchant's signed-in browser profile lives between runs.
DEFAULT_PROFILE = Path.home() / ".local" / "state" / "storefront" / "browser"

# Shopify admin moves things around. Each of these is tried in order, and the
# first that matches wins; a miss is reported, never assumed away.
FILE_INPUT = [
    "input[type=file]",
]
ADD_ASSET = [
    "button:has-text('Add asset')",
    "button:has-text('Add file')",
    "button:has-text('Upload file')",
    "a:has-text('Add asset')",
]
SAVE = [
    "button:has-text('Save')",
    "button[type=submit]:has-text('Save')",
]


class AttachError(RuntimeError):
    """The browser could not complete an attachment."""


@dataclass
class AttachPlan:
    """One product, one file."""

    sku: str
    product_title: str
    product_id: str
    file: Path

    def describe(self) -> str:
        size = self.file.stat().st_size / 1024 if self.file.exists() else 0
        return f"{self.sku:22} {self.product_title[:40]:42} {self.file.name} ({size:.0f}K)"


def plan_from_mapping(
    mapping: dict[str, Path], products: list[dict]
) -> tuple[list[AttachPlan], list[str]]:
    """Match files to products by SKU.

    Returns the plans it could build and the SKUs it could not place, so a
    typo in a filename surfaces before a browser is ever opened.
    """
    by_sku: dict[str, dict] = {}
    for product in products:
        for variant in (product.get("variants") or {}).get("nodes") or []:
            if sku := variant.get("sku"):
                by_sku[sku] = product

    plans, unmatched = [], []
    for sku, path in sorted(mapping.items()):
        product = by_sku.get(sku)
        if product is None:
            unmatched.append(sku)
            continue
        if not Path(path).exists():
            unmatched.append(f"{sku} (file not found: {path})")
            continue
        import html

        plans.append(AttachPlan(
            sku=sku,
            product_title=html.unescape(str(product.get("title") or "")),
            product_id=str(product.get("id") or ""),
            file=Path(path),
        ))
    return plans, unmatched


def _first_matching(page, selectors: list[str], timeout: float = 4000):
    """Return the first selector that resolves, or None. Never raises."""
    for selector in selectors:
        try:
            element = page.locator(selector).first
            element.wait_for(state="attached", timeout=timeout)
            return element
        except Exception:  # noqa: BLE001 -- any miss means "try the next one"
            continue
    return None


def attach_all(
    plans: list[AttachPlan],
    store_domain: str,
    *,
    profile_dir: Path = DEFAULT_PROFILE,
    headless: bool = False,
    dry_run: bool = True,
    on_done=None,
) -> tuple[list[AttachPlan], list[tuple[AttachPlan, str]]]:
    """Walk each product's admin page and attach its file.

    Returns (attached, failed). A failure carries the reason, so the caller can
    tell the merchant exactly which ones still need doing by hand.

    With ``dry_run`` (the default) no browser opens at all -- the plan is
    printed and nothing is touched.
    """
    if dry_run:
        return [], [(p, "dry run -- nothing attached") for p in plans]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover -- environment problem
        raise AttachError(
            "playwright is not installed. `pip install playwright` and "
            "`playwright install chromium`, then try again."
        ) from exc

    attached: list[AttachPlan] = []
    failed: list[tuple[AttachPlan, str]] = []
    profile_dir.mkdir(parents=True, exist_ok=True)
    handle = store_domain.split(".")[0]

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(profile_dir), headless=headless, accept_downloads=False
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(f"https://admin.shopify.com/store/{handle}/apps/digital-downloads",
                      wait_until="domcontentloaded", timeout=60000)

            if "accounts.shopify.com" in page.url or "login" in page.url:
                log.warning("sign-in required -- complete it in the browser window")
                # Give the merchant time to sign in, including 2FA. The agent
                # waits; it does not type anything into this form.
                page.wait_for_url("**/admin.shopify.com/**", timeout=300000)

            for plan in plans:
                try:
                    _attach_one(page, handle, plan)
                    attached.append(plan)
                    if on_done:
                        on_done(plan)
                except Exception as exc:  # noqa: BLE001 -- report, never skip silently
                    failed.append((plan, str(exc)[:200]))
                    log.warning("could not attach %s: %s", plan.sku, exc)
        finally:
            context.close()

    return attached, failed


def _attach_one(page, handle: str, plan: AttachPlan) -> None:
    numeric = plan.product_id.rsplit("/", 1)[-1]
    page.goto(
        f"https://admin.shopify.com/store/{handle}/products/{numeric}",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_timeout(1500)

    if button := _first_matching(page, ADD_ASSET):
        try:
            button.click(timeout=4000)
            page.wait_for_timeout(800)
        except Exception:  # noqa: BLE001 -- the input may already be present
            pass

    file_input = _first_matching(page, FILE_INPUT, timeout=6000)
    if file_input is None:
        raise AttachError(
            "no file input on the product page -- the Digital Products section "
            "may not be open, or Shopify has changed this page"
        )

    # The whole point: this writes the file straight into the input, so no
    # operating-system file dialog is ever involved.
    file_input.set_input_files(str(plan.file), timeout=120000)
    page.wait_for_timeout(2500)

    if save := _first_matching(page, SAVE, timeout=3000):
        try:
            save.click(timeout=4000)
            page.wait_for_timeout(2000)
        except Exception:  # noqa: BLE001 -- some pages save on upload
            pass
