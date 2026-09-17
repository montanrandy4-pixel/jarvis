"""Building the store's structure: collections, pages, policies, navigation.

Same discipline as the catalogue: work out the difference first, then write it.
Everything is matched by handle, so running build twice edits rather than
duplicates.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import queries
from .client import ShopifyClient, UserError
from .store import StoreSpec

log = logging.getLogger("shop.storefront")

# Shopify's own handles for the policy pages.
POLICY_TYPES = {
    "refund": "REFUND_POLICY",
    "privacy": "PRIVACY_POLICY",
    "terms": "TERMS_OF_SERVICE",
    "shipping": "SHIPPING_POLICY",
    "contact": "CONTACT_INFORMATION",
}


@dataclass
class Step:
    kind: str       # collection | page | policy | menu
    action: str     # create | update | unchanged
    handle: str
    title: str = ""
    payload: dict = field(default_factory=dict)

    def describe(self) -> str:
        return f"{self.action:<9} {self.kind:<11} {self.title or self.handle}"


@dataclass
class BuildPlan:
    steps: list[Step] = field(default_factory=list)

    @property
    def writes(self) -> list[Step]:
        return [s for s in self.steps if s.action != "unchanged"]

    def summary(self) -> str:
        created = sum(1 for s in self.steps if s.action == "create")
        updated = sum(1 for s in self.steps if s.action == "update")
        same = sum(1 for s in self.steps if s.action == "unchanged")
        return f"{created} to create, {updated} to update, {same} already correct"


def markdown_to_html(text: str) -> str:
    """Enough Markdown for policy and page copy: headings, tables, paragraphs."""
    lines = [line.rstrip() for line in (text or "").strip().splitlines()]
    html: list[str] = []
    paragraph: list[str] = []
    table: list[str] = []

    def flush_paragraph():
        if paragraph:
            html.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_table():
        if not table:
            return
        rows = [
            [cell.strip() for cell in row.strip().strip("|").split("|")]
            for row in table
            if not re.match(r"^\s*\|?[\s:|-]+\|?\s*$", row)
        ]
        table.clear()
        if not rows:
            return
        head, *body = rows
        out = ["<table><thead><tr>"]
        out += [f"<th>{_inline(c)}</th>" for c in head]
        out.append("</tr></thead><tbody>")
        for row in body:
            out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
        out.append("</tbody></table>")
        html.append("".join(out))

    for line in lines:
        if line.startswith("|"):
            flush_paragraph()
            table.append(line)
            continue
        flush_table()
        if not line:
            flush_paragraph()
        elif line.startswith("###"):
            flush_paragraph()
            html.append(f"<h3>{_inline(line.lstrip('# '))}</h3>")
        elif line.startswith("##"):
            flush_paragraph()
            html.append(f"<h2>{_inline(line.lstrip('# '))}</h2>")
        elif line.startswith("#"):
            flush_paragraph()
            html.append(f"<h1>{_inline(line.lstrip('# '))}</h1>")
        else:
            paragraph.append(line)
    flush_table()
    flush_paragraph()
    return "".join(html)


def _inline(text: str) -> str:
    import html as html_module

    escaped = html_module.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)


def plan(spec: StoreSpec, client: ShopifyClient) -> BuildPlan:
    """Compare the store definition with the store."""
    result = BuildPlan()
    existing_collections = _by_handle(
        client.paginate(queries.COLLECTIONS, {}, path=["collections"])
    )
    for collection in spec.collections:
        found = existing_collections.get(collection.handle)
        payload: dict = {
            "title": collection.title,
            "handle": collection.handle,
            "descriptionHtml": markdown_to_html(collection.description),
        }
        if collection.tag or collection.product_type:
            rules = []
            if collection.tag:
                rules.append(
                    {"column": "TAG", "relation": "EQUALS", "condition": collection.tag}
                )
            if collection.product_type:
                rules.append(
                    {
                        "column": "TYPE",
                        "relation": "EQUALS",
                        "condition": collection.product_type,
                    }
                )
            payload["ruleSet"] = {"appliedDisjunctively": False, "rules": rules}
        if found:
            payload["id"] = found["id"]
            action = (
                "unchanged"
                if found.get("descriptionHtml") == payload["descriptionHtml"]
                and found.get("title") == collection.title
                else "update"
            )
        else:
            action = "create"
        result.steps.append(
            Step("collection", action, collection.handle, collection.title, payload)
        )

    existing_pages = _by_handle(client.paginate(queries.PAGES, {}, path=["pages"]))
    for page in spec.pages:
        found = existing_pages.get(page.handle)
        payload = {
            "title": page.title,
            "handle": page.handle,
            "body": markdown_to_html(page.body),
        }
        if found:
            payload["id"] = found["id"]
            # Only rewrite a page whose content actually differs.
            action = (
                "unchanged"
                if found.get("title") == page.title
                and found.get("body") == payload["body"]
                else "update"
            )
        else:
            action = "create"
        result.steps.append(
            Step("page", action, page.handle, page.title, payload)
        )

    current_policies = {}
    data = client.execute(queries.SHOP_POLICIES)
    for policy in ((data.get("shop") or {}).get("shopPolicies") or []):
        current_policies[policy.get("type")] = policy
    for policy in spec.policies:
        policy_type = POLICY_TYPES.get(policy.kind)
        if not policy_type:
            continue
        body = markdown_to_html(policy.body)
        found = current_policies.get(policy_type)
        action = "unchanged" if found and found.get("body") == body else (
            "update" if found else "create"
        )
        result.steps.append(
            Step("policy", action, policy.kind, f"{policy.kind} policy",
                 {"type": policy_type, "body": body})
        )

    existing_menus = _by_handle(client.paginate(queries.MENUS, {}, path=["menus"]))
    for handle, title, items in (
        ("main-menu", "Main menu", spec.main_menu),
        ("footer", "Footer menu", spec.footer_menu),
    ):
        if not items:
            continue
        found = existing_menus.get(handle)
        payload = {
            "handle": handle,
            "title": title,
            "items": [
                {"title": item.title, "type": "HTTP", "url": item.target}
                for item in items
            ],
        }
        if found:
            payload["id"] = found["id"]
            current = [
                (entry.get("title"), entry.get("url"))
                for entry in (found.get("items") or [])
            ]
            wanted = [(item.title, item.target) for item in items]
            action = "unchanged" if current == wanted else "update"
        else:
            action = "create"
        result.steps.append(Step("menu", action, handle, title, payload))
    return result


def apply(build: BuildPlan, client: ShopifyClient) -> dict:
    """Carry out a build plan. One failure does not stop the rest."""
    done = {"created": 0, "updated": 0, "failed": 0}
    for step in build.writes:
        try:
            _run(step, client)
            done["created" if step.action == "create" else "updated"] += 1
        except UserError as exc:
            log.error("%s %s: %s", step.kind, step.handle, exc)
            done["failed"] += 1
        except Exception:
            log.exception("%s %s failed", step.kind, step.handle)
            done["failed"] += 1
    return done


def _run(step: Step, client: ShopifyClient) -> None:
    payload = dict(step.payload)
    if step.kind == "collection":
        document = (
            queries.CREATE_COLLECTION if step.action == "create"
            else queries.UPDATE_COLLECTION
        )
        client.mutate(document, {"input": payload},
                      field_name=f"collection{step.action.capitalize()}")
    elif step.kind == "page":
        if step.action == "create":
            client.mutate(queries.CREATE_PAGE, {"page": payload},
                          field_name="pageCreate")
        else:
            page_id = payload.pop("id")
            client.mutate(queries.UPDATE_PAGE, {"id": page_id, "page": payload},
                          field_name="pageUpdate")
    elif step.kind == "policy":
        client.mutate(queries.UPDATE_POLICY, {"shopPolicy": payload},
                      field_name="shopPolicyUpdate")
    elif step.kind == "menu":
        if step.action == "create":
            client.mutate(
                queries.CREATE_MENU,
                {"title": payload["title"], "handle": payload["handle"],
                 "items": payload["items"]},
                field_name="menuCreate",
            )
        else:
            client.mutate(
                queries.UPDATE_MENU,
                {"id": payload["id"], "title": payload["title"],
                 "handle": payload["handle"], "items": payload["items"]},
                field_name="menuUpdate",
            )


def _by_handle(nodes) -> dict[str, dict]:
    return {node.get("handle", ""): node for node in nodes if node.get("handle")}
