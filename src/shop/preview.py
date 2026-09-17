"""A local rendering of the whole store.

Not a Shopify theme -- a plain static site built from the same store definition
and feed that `shop build` and `shop apply` use. It exists so you can see what
the shop will contain, and read every page and policy, before the store exists
at all.
"""

from __future__ import annotations

import html
import shutil
from pathlib import Path

from .feed import read as read_feed
from .pricing import price_for
from .storefront import markdown_to_html

STYLE = """\
:root {
  --accent: %(accent)s;
  --ink: #1b1d1c;
  --muted: #6b716e;
  --line: #e4e6e5;
  --bg: #fbfaf8;
  --card: #ffffff;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ink: #eceeed; --muted: #9aa3a0; --line: #2a2f2d;
    --bg: #111413; --card: #181c1b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.6 ui-serif, Georgia, "Times New Roman", serif;
}
a { color: inherit; }
img { max-width: 100%%; display: block; }
.wrap { max-width: 1040px; margin: 0 auto; padding: 0 20px; }
header.site { border-bottom: 1px solid var(--line); background: var(--card); }
header.site .wrap { display: flex; align-items: baseline; gap: 22px;
  padding-top: 18px; padding-bottom: 18px; flex-wrap: wrap; }
.brand { font-size: 21px; font-weight: 600; letter-spacing: .01em;
  text-decoration: none; margin-right: auto; }
.brand small { display: block; font-size: 12px; font-weight: 400;
  color: var(--muted); letter-spacing: .04em; text-transform: uppercase;
  font-family: ui-sans-serif, system-ui, sans-serif; }
nav a { color: var(--muted); text-decoration: none; font-size: 14px;
  font-family: ui-sans-serif, system-ui, sans-serif; }
nav a:hover { color: var(--accent); }
nav { display: flex; gap: 18px; flex-wrap: wrap; }
.hero { padding: 54px 0 34px; border-bottom: 1px solid var(--line); }
.hero h1 { font-size: clamp(30px, 5vw, 46px); margin: 0 0 12px; line-height: 1.15; }
.hero p { color: var(--muted); max-width: 60ch; margin: 0; font-size: 18px; }
h2.section { font-size: 14px; text-transform: uppercase; letter-spacing: .12em;
  color: var(--muted); font-family: ui-sans-serif, system-ui, sans-serif;
  margin: 46px 0 18px; font-weight: 600; }
.grid { display: grid; gap: 26px;
  grid-template-columns: repeat(auto-fill, minmax(215px, 1fr)); }
.card { background: var(--card); border: 1px solid var(--line);
  border-radius: 3px; overflow: hidden; text-decoration: none;
  display: flex; flex-direction: column; }
.card .thumb { aspect-ratio: 1; background: var(--bg) center/cover no-repeat;
  border-bottom: 1px solid var(--line); }
.card .body { padding: 13px 15px 16px; }
.card h3 { margin: 0 0 5px; font-size: 16px; font-weight: 500; }
.card .price { font-family: ui-sans-serif, system-ui, sans-serif;
  font-size: 14px; color: var(--muted); }
.card .was { text-decoration: line-through; opacity: .6; margin-left: 6px; }
.collections { display: grid; gap: 18px;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }
.collections a { background: var(--card); border: 1px solid var(--line);
  border-radius: 3px; padding: 22px; text-decoration: none; display: block; }
.collections h3 { margin: 0 0 6px; font-size: 19px; }
.collections p { margin: 0; color: var(--muted); font-size: 14px; }
.product { display: grid; gap: 38px; grid-template-columns: 1fr 1fr;
  padding: 40px 0; align-items: start; }
@media (max-width: 720px) { .product { grid-template-columns: 1fr; } }
.product .shot { border: 1px solid var(--line); background: var(--card);
  aspect-ratio: 1; background-size: cover; background-position: center; }
.product h1 { margin: 0 0 8px; font-size: 30px; }
.product .price { font-size: 21px; font-family: ui-sans-serif, system-ui, sans-serif; }
.meta { font-family: ui-sans-serif, system-ui, sans-serif; font-size: 13px;
  color: var(--muted); margin-top: 22px; border-top: 1px solid var(--line);
  padding-top: 14px; }
.meta div { margin: 4px 0; }
.buy { display: inline-block; margin-top: 20px; background: var(--accent);
  color: #fff; padding: 12px 26px; border-radius: 2px; text-decoration: none;
  font-family: ui-sans-serif, system-ui, sans-serif; font-size: 15px; }
.prose { max-width: 68ch; padding: 34px 0 10px; }
.prose h1 { font-size: 32px; margin: 0 0 18px; }
.prose h2 { font-size: 21px; margin: 30px 0 10px; }
.prose table { border-collapse: collapse; width: 100%%; margin: 16px 0;
  font-size: 15px; }
.prose th, .prose td { border: 1px solid var(--line); padding: 8px 11px;
  text-align: left; }
footer.site { border-top: 1px solid var(--line); margin-top: 60px;
  padding: 30px 0 46px; color: var(--muted); font-size: 14px;
  font-family: ui-sans-serif, system-ui, sans-serif; background: var(--card); }
footer.site nav { margin-bottom: 14px; }
.notice { background: #fff6e0; border: 1px solid #e8d08a; color: #5a4413;
  padding: 11px 16px; font-family: ui-sans-serif, system-ui, sans-serif;
  font-size: 13.5px; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .notice {
    background: #2a2412; border-color: #5c4b1d; color: #e8d6a8; }
}
"""


def render(spec, config, out_dir) -> Path:
    """Write the whole store as static pages. Returns the index path."""
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "products").mkdir(parents=True)
    (out / "collections").mkdir(parents=True)
    (out / "pages").mkdir(parents=True)
    (out / "policies").mkdir(parents=True)

    (out / "style.css").write_text(STYLE % {"accent": spec.brand.accent})

    items, prices = _catalogue(config)
    symbol = _symbol(spec.brand.currency)

    for item in items:
        (out / "products" / f"{item.sku}.html").write_text(
            _product_page(spec, item, prices[item.sku], symbol)
        )
    for collection in spec.collections:
        members = [
            i for i in items
            if not collection.tag or collection.tag in [t.lower() for t in i.tags]
        ]
        (out / "collections" / f"{collection.handle}.html").write_text(
            _collection_page(spec, collection, members, prices, symbol)
        )
    for page in spec.pages:
        (out / "pages" / f"{page.handle}.html").write_text(
            _prose_page(spec, page.title, markdown_to_html(page.body))
        )
    for policy in spec.policies:
        (out / "policies" / f"{policy.kind}.html").write_text(
            _prose_page(spec, f"{policy.kind.title()} policy",
                        markdown_to_html(policy.body))
        )

    index = out / "index.html"
    index.write_text(_home(spec, items, prices, symbol))
    return index


def _catalogue(config):
    try:
        report = read_feed(config.feed_path) if config.feed_path else None
    except (ValueError, OSError):
        report = None
    items = report.items if report else []
    prices = {
        item.sku: price_for(item.cost, config.pricing, suggested=item.price)
        for item in items
    }
    return items, prices


def _symbol(currency: str) -> str:
    return {"GBP": "£", "USD": "$", "EUR": "€"}.get(currency.upper(), currency + " ")


def _shell(spec, title: str, body: str, depth: int = 0) -> str:
    up = "../" * depth
    nav = "".join(
        f'<a href="{up}{_local(item.target)}">{html.escape(item.title)}</a>'
        for item in spec.main_menu
    )
    footer_nav = "".join(
        f'<a href="{up}{_local(item.target)}">{html.escape(item.title)}</a>'
        for item in spec.footer_menu
    )
    tagline = (
        f"<small>{html.escape(spec.brand.tagline)}</small>"
        if spec.brand.tagline else ""
    )
    # The home page is already the brand; do not say its name twice.
    page_title = (
        html.escape(spec.brand.name)
        if title == spec.brand.name
        else f"{html.escape(title)} &middot; {html.escape(spec.brand.name)}"
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{page_title}</title>
<link rel="stylesheet" href="{up}style.css">
</head><body>
<header class="site"><div class="wrap">
  <a class="brand" href="{up}index.html">{html.escape(spec.brand.name)}{tagline}</a>
  <nav>{nav}</nav>
</div></header>
{body}
<footer class="site"><div class="wrap">
  <nav>{footer_nav}</nav>
  <div>{html.escape(spec.brand.name)}
    {(" &middot; " + html.escape(spec.brand.email)) if spec.brand.email else ""}</div>
  <div style="margin-top:10px" class="notice">Local preview. This store has not been created yet &mdash; nothing here can be bought.</div>
</div></footer>
</body></html>"""


def _local(target: str) -> str:
    """Turn a Shopify path into a path in this preview."""
    target = target.strip()
    for prefix, folder in (
        ("/collections/", "collections/"),
        ("/pages/", "pages/"),
    ):
        if target.startswith(prefix):
            return f"{folder}{target[len(prefix):]}.html"
    if target.startswith("/policies/"):
        slug = target[len("/policies/"):]
        kind = slug.replace("-policy", "").replace("terms-of-service", "terms")
        return f"policies/{kind}.html"
    return target or "index.html"


def _card(item, price, symbol: str, depth: int) -> str:
    up = "../" * depth
    image = item.images[0] if item.images else ""
    amount, compare_at = price.as_strings()
    was = f'<span class="was">{symbol}{compare_at}</span>' if compare_at else ""
    return f"""<a class="card" href="{up}products/{html.escape(item.sku)}.html">
  <div class="thumb" style="background-image:url('{html.escape(image)}')"></div>
  <div class="body"><h3>{html.escape(item.title)}</h3>
  <div class="price">{symbol}{amount}{was}</div></div></a>"""


def _home(spec, items, prices, symbol) -> str:
    featured = [c for c in spec.collections if c.featured] or spec.collections[:3]
    collections = "".join(
        f"""<a href="collections/{c.handle}.html"><h3>{html.escape(c.title)}</h3>
        <p>{html.escape(c.description.strip().splitlines()[0] if c.description.strip() else "")}</p></a>"""
        for c in featured
    )
    cards = "".join(_card(i, prices[i.sku], symbol, 0) for i in items[:12])
    about = html.escape((spec.brand.about or "").strip())
    body = f"""<section class="hero"><div class="wrap">
  <h1>{html.escape(spec.brand.tagline or spec.brand.name)}</h1>
  <p>{about}</p>
</div></section>
<div class="wrap">
  <h2 class="section">Shop by category</h2>
  <div class="collections">{collections}</div>
  <h2 class="section">The range</h2>
  <div class="grid">{cards}</div>
</div>"""
    return _shell(spec, spec.brand.name, body, 0)


def _collection_page(spec, collection, items, prices, symbol) -> str:
    cards = "".join(_card(i, prices[i.sku], symbol, 1) for i in items)
    if not cards:
        cards = '<p style="color:var(--muted)">Nothing in this collection yet.</p>'
    body = f"""<div class="wrap">
  <div class="prose"><h1>{html.escape(collection.title)}</h1>
  {markdown_to_html(collection.description)}</div>
  <div class="grid">{cards}</div>
</div>"""
    return _shell(spec, collection.title, body, 1)


def _product_page(spec, item, price, symbol) -> str:
    image = item.images[0] if item.images else ""
    amount, compare_at = price.as_strings()
    was = f'<span class="was">{symbol}{compare_at}</span>' if compare_at else ""
    stock = (
        f"In stock ({item.quantity})" if item.quantity > 0 else "Out of stock"
    )
    held = (
        f'<div class="notice" style="margin-top:14px">Held for review: {html.escape(price.hold)}</div>'
        if price.held else ""
    )
    body = f"""<div class="wrap"><div class="product">
  <div class="shot" style="background-image:url('{html.escape(image)}')"></div>
  <div>
    <h1>{html.escape(item.title)}</h1>
    <div class="price">{symbol}{amount}{was}</div>
    {held}
    <div style="margin-top:18px">{markdown_to_html(item.description)}</div>
    <a class="buy" href="#">Add to basket</a>
    <div class="meta">
      <div>SKU: {html.escape(item.sku)}</div>
      <div>{html.escape(stock)}</div>
      {f"<div>Brand: {html.escape(item.vendor)}</div>" if item.vendor else ""}
    </div>
  </div>
</div></div>"""
    return _shell(spec, item.title, body, 1)


def _prose_page(spec, title: str, body_html: str) -> str:
    body = f"""<div class="wrap"><div class="prose">
  <h1>{html.escape(title)}</h1>
  {body_html}
</div></div>"""
    return _shell(spec, title, body, 1)
