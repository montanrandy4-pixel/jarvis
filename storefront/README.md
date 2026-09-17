# Storefront assets — Solo Stack

Product cover images for the Shopify store `wwt2sq-wi.myshopify.com`.

Each cover is a 1540×1540 PNG built from a shared design system: a category eyebrow,
the product name, a one-line contents summary, and a wireframe motif that previews the
structure of what the buyer actually receives (document, spreadsheet, kanban board,
calendar, message list or bundle stack).

Accent colour encodes the category:

| Colour | Category |
|---|---|
| Terracotta | Contracts & legal, sales |
| Sage | Client systems, Notion |
| Blue | Money & pricing |
| Gold | Marketing & growth |
| Violet | Bundles (light background) |

## Regenerating

```
pip install Pillow
python3 storefront/generate_covers.py
```

Output is written to a `covers/` directory next to the script. Edit the `ITEMS` list at
the bottom of the script to change titles, subtitles, category or motif.

## How Shopify uses these

The files are served publicly from `raw.githubusercontent.com` and attached to products
by URL through the Shopify Admin API. Shopify copies the image into its own CDN on
attach, so a later change here does not alter images already live on the store — re-attach
to publish an update.
