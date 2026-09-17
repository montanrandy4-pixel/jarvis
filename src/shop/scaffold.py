"""A complete store, ready to edit.

`shop scaffold` writes a store.toml and a starter feed that together describe a
whole shop -- brand, collections, pages, policies, navigation and a catalogue.
Change the brand and the feed and it is your shop; the structure is the part
that is tedious to get right and is the same for everyone.

The policy texts are drafts with {{PLACEHOLDERS}} in them. They are a starting
point so the store is not missing its legally required pages on day one, not
legal advice -- have them read before you sell anything.
"""

from __future__ import annotations

STORE_TOML = '''\
# Your whole store, as a file. Edit this, then run: shop build --live
# Everything here is created or updated in place, so it is safe to re-run.

[brand]
name = "Fenwick & Co"
tagline = "Honest homewares, made to last"
about = """
We sell a small, considered range of homewares: lighting, shelving and
stoneware. Everything we stock is something we would keep ourselves, chosen for
how it is made rather than how it photographs.
"""
email = "hello@example.com"          # <- your real address
phone = ""
address = "{{YOUR BUSINESS ADDRESS}}"
currency = "GBP"
accent = "#1f6f5c"                    # used in the local preview

# --- Collections -----------------------------------------------------------
# A collection with a `tag` builds itself: any product carrying that tag joins
# it automatically, including products added by a future feed sync.

[[collections]]
title = "Lighting"
tag = "lighting"
description = "Lamps and shades that put light where you actually need it."
featured = true

[[collections]]
title = "Shelving & Storage"
tag = "storage"
description = "Solid wood shelving, brackets and boxes."
featured = true

[[collections]]
title = "Kitchen & Table"
tag = "kitchen"
description = "Stoneware, boards and the things that live on a table."
featured = true

[[collections]]
title = "Everything"
description = "The full range."

# --- Pages -----------------------------------------------------------------

[[pages]]
title = "About"
body = """
Fenwick & Co began because good homewares had become hard to find between the
disposable and the absurd.

We keep the range small on purpose. Every piece is chosen for how it is made:
materials that age well, joints that hold, glazes that survive a dishwasher. If
something does not last, we stop stocking it.

Orders are packed by hand and sent within two working days.
"""

[[pages]]
title = "Delivery"
body = """
Orders placed before 2pm on a working day are packed the same day and sent the
next. You will get a tracking link as soon as the parcel leaves us.

Standard delivery is {{DELIVERY PRICE}} and takes two to four working days.
Orders over {{FREE DELIVERY THRESHOLD}} are sent free.

We currently ship to {{COUNTRIES}}.
"""

[[pages]]
title = "Contact"
body = """
Email {{EMAIL}} and a person will reply, usually the same day and always within
one working day.

{{BUSINESS NAME}}
{{BUSINESS ADDRESS}}
"""

# --- Policies --------------------------------------------------------------
# Drafts, so the store is not missing them on day one. Read them before you
# open, and replace every {{PLACEHOLDER}}.

[policies]
refund = """
## Returns and refunds

You can return anything within 30 days of delivery for a full refund, for any
reason. It needs to be unused and in its original packaging.

To start a return, email {{EMAIL}} with your order number. We will send return
instructions. Return postage is paid by you unless the item arrived damaged or
was not what you ordered, in which case we cover it.

Refunds are issued to the original payment method within five working days of
the item reaching us.

Nothing here affects your statutory rights, including your right to cancel an
online order within 14 days of receiving it.
"""

privacy = """
## Privacy

**What we collect.** When you order, we collect your name, email, delivery
address and the details of what you bought. Payment is handled by Shopify
Payments and {{PAYMENT PROVIDERS}}; we never see or store your card number.

**Why.** To take payment, send your order, and answer you if you get in touch.
If you opt in, to send you occasional email about new stock. Nothing else.

**Who else sees it.** Our payment processor, our delivery carrier, and Shopify,
which hosts this shop. We do not sell your data to anyone, ever.

**How long.** Order records for {{RETENTION PERIOD}}, as our tax obligations
require. Marketing consent until you withdraw it.

**Your rights.** You can ask for a copy of what we hold, ask us to correct or
delete it, or object to us using it. Email {{EMAIL}} and we will act within one
month.

Controller: {{BUSINESS NAME}}, {{BUSINESS ADDRESS}}.
"""

terms = """
## Terms of service

By ordering from {{BUSINESS NAME}} you agree to these terms.

**Orders.** A contract exists once we email you to confirm dispatch, not when
you place the order. We may decline an order -- if we do, you are not charged.

**Prices.** Shown in {{CURRENCY}} and include VAT where it applies. If a price
is obviously wrong, we will contact you before dispatching rather than charge
it.

**Delivery.** Estimated, not guaranteed. Risk passes to you on delivery.

**Faults.** If something is faulty, tell us within a reasonable time and we
will repair, replace or refund it.

**Liability.** We do not limit our liability for death, personal injury or
fraud. Otherwise our liability is limited to what you paid for the order.

**Law.** These terms are governed by the law of {{JURISDICTION}}.
"""

shipping = """
## Shipping

We pack orders by hand and dispatch within two working days.

| Where | Cost | Time |
|---|---|---|
| {{HOME COUNTRY}} standard | {{PRICE}} | 2-4 working days |
| {{HOME COUNTRY}} tracked | {{PRICE}} | 1-2 working days |
| {{OTHER REGION}} | {{PRICE}} | 5-10 working days |

Orders over {{THRESHOLD}} are sent free.

Anything fragile travels double-boxed. If a parcel arrives damaged, photograph
it before unpacking further and email {{EMAIL}}; we will replace it.
"""

# --- Navigation ------------------------------------------------------------

[navigation]
main = [
  { title = "Lighting", target = "/collections/lighting" },
  { title = "Shelving & Storage", target = "/collections/shelving-storage" },
  { title = "Kitchen & Table", target = "/collections/kitchen-table" },
  { title = "About", target = "/pages/about" },
]
footer = [
  { title = "Delivery", target = "/pages/delivery" },
  { title = "Contact", target = "/pages/contact" },
  { title = "Returns", target = "/policies/refund-policy" },
  { title = "Privacy", target = "/policies/privacy-policy" },
  { title = "Terms", target = "/policies/terms-of-service" },
]
'''

# A starter catalogue. Replace it with your supplier's feed, or keep editing
# this one -- the columns are the only part that matters.
FEED_CSV = '''\
sku,title,cost,quantity,description,images,vendor,product_type,tags
FL-100,Brass Desk Lamp,24.50,12,"An adjustable brass desk lamp with a linen shade. Weighted base, in-line switch, takes an E27 bulb.",https://placehold.co/900x900?text=Brass+Desk+Lamp,Fenwick & Co,Lighting,lighting
FL-101,Brass Wall Light,31.00,6,"A simple brass wall light with a pivoting arm. Hard-wired, plate covers a standard back box.",https://placehold.co/900x900?text=Brass+Wall+Light,Fenwick & Co,Lighting,lighting
FL-102,Linen Lamp Shade,12.75,20,"A 30cm drum shade in natural linen. Fits both bayonet and screw fittings.",https://placehold.co/900x900?text=Linen+Shade,Fenwick & Co,Lighting,lighting
FS-200,Oak Wall Shelf 60cm,28.00,9,"Solid oak shelf, 60cm by 20cm, 2cm thick. Supplied with concealed steel brackets.",https://placehold.co/900x900?text=Oak+Shelf+60,Fenwick & Co,Shelving,storage
FS-201,Oak Wall Shelf 90cm,39.50,7,"Solid oak shelf, 90cm by 20cm, 2cm thick. Supplied with concealed steel brackets.",https://placehold.co/900x900?text=Oak+Shelf+90,Fenwick & Co,Shelving,storage
FS-202,Cast Iron Bracket,7.20,40,"A plain cast iron shelf bracket, 18cm. Sold singly.",https://placehold.co/900x900?text=Iron+Bracket,Fenwick & Co,Shelving,storage
FS-203,Ash Storage Box,19.90,11,"A lidded ash box, 30cm by 20cm. Finger-jointed corners.",https://placehold.co/900x900?text=Ash+Box,Fenwick & Co,Storage,storage
FK-300,Stoneware Mug,4.20,60,"A 350ml stoneware mug with a speckled glaze. Dishwasher and microwave safe.",https://placehold.co/900x900?text=Stoneware+Mug,Fenwick & Co,Tableware,kitchen
FK-301,Stoneware Bowl,6.80,45,"An 18cm stoneware serving bowl in the same speckled glaze as the mug.",https://placehold.co/900x900?text=Stoneware+Bowl,Fenwick & Co,Tableware,kitchen
FK-302,Beech Chopping Board,14.00,18,"End-grain beech board, 35cm by 25cm, with a juice groove.",https://placehold.co/900x900?text=Chopping+Board,Fenwick & Co,Kitchen,kitchen
FK-303,Linen Tea Towel,5.50,50,"A heavyweight linen tea towel, 70cm by 45cm. Gets better with washing.",https://placehold.co/900x900?text=Tea+Towel,Fenwick & Co,Kitchen,kitchen
FK-304,Cast Iron Trivet,9.40,22,"A round cast iron trivet, 18cm, with felt feet.",https://placehold.co/900x900?text=Iron+Trivet,Fenwick & Co,Kitchen,kitchen
'''


def write(directory, *, force: bool = False) -> list:
    """Write store.toml, feed.csv and shop.toml. Returns what it wrote."""
    from pathlib import Path

    from .cli import STARTER_CONFIG

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "store.toml": STORE_TOML,
        "feed.csv": FEED_CSV,
        "shop.toml": STARTER_CONFIG,
    }
    written = []
    for name, content in files.items():
        path = directory / name
        if path.exists() and not force:
            continue
        path.write_text(content)
        written.append(path)
    return written
