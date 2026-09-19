"""Every GraphQL document the storefront agent sends, in one reviewable place.

Kept deliberately separate from ``src/shop/queries.py``: that module serves a
supplier-feed catalogue, this one serves a hand-authored digital catalogue.
They share a client, not a data model.

Shopify ships an API version quarterly and supports each for a year. When a
version falls out of support these are the documents to check.
"""

from __future__ import annotations

# Everything the audit needs to judge whether a product is safe to sell.
# `requiresShipping` is the field that decides whether checkout asks a buyer
# for a postal address they do not have a use for.
CATALOG = """
query catalog($first: Int!, $after: String, $query: String) {
  products(first: $first, after: $after, query: $query) {
    edges {
      cursor
      node {
        id
        title
        handle
        status
        tags
        publishedAt
        totalInventory
        featuredMedia { id }
        media(first: 5) {
          nodes {
            status
            mediaErrors { message }
          }
        }
        variants(first: 5) {
          nodes {
            id
            sku
            price
            inventoryItem { requiresShipping }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# Orders, with enough of each line item to tell whether the buyer can actually
# get what they paid for.
ORDERS = """
query orders($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT, reverse: true) {
    edges {
      cursor
      node {
        id
        name
        createdAt
        displayFinancialStatus
        displayFulfillmentStatus
        email
        tags
        currentTotalPriceSet { shopMoney { amount currencyCode } }
        customer { id displayName numberOfOrders }
        lineItems(first: 25) {
          nodes {
            title
            quantity
            sku
            requiresShipping
            variant { id }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

ABANDONED = """
query abandoned($first: Int!, $after: String) {
  abandonedCheckouts(first: $first, after: $after) {
    edges {
      cursor
      node {
        id
        createdAt
        abandonedCheckoutUrl
        customer { displayName }
        totalPriceSet { shopMoney { amount currencyCode } }
        lineItems(first: 10) { nodes { title quantity } }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

ORDER_TAGS_ADD = """
mutation tagOrder($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    userErrors { field message }
  }
}
"""

DISCOUNTS = """
query discounts($first: Int!, $after: String) {
  discountNodes(first: $first, after: $after) {
    edges {
      cursor
      node {
        id
        discount {
          ... on DiscountCodeBasic {
            title
            status
            startsAt
            endsAt
            codes(first: 5) { nodes { code } }
            customerGets {
              value {
                ... on DiscountPercentage { percentage }
              }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

SHOP = """
query shop {
  shop {
    name
    myshopifyDomain
    currencyCode
    ianaTimezone
  }
}
"""
