"""The GraphQL documents the autopilot uses.

Kept in one place so the API surface this depends on can be reviewed -- and
adjusted -- without hunting through the logic.
"""

SHOP = "query { shop { name myshopifyDomain currencyCode ianaTimezone } }"

LOCATIONS = """
query locations($first: Int!, $after: String) {
  locations(first: $first, after: $after) {
    edges { node { id name isActive shipsInventory } }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# Products the autopilot manages, indexed by SKU on the client side.
MANAGED_PRODUCTS = """
query managedProducts($first: Int!, $after: String, $query: String) {
  products(first: $first, after: $after, query: $query) {
    edges {
      node {
        id handle title status tags descriptionHtml
        variants(first: 10) {
          edges {
            node {
              id sku price compareAtPrice
              inventoryQuantity
              inventoryItem { id tracked }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

CREATE_PRODUCT = """
mutation productCreate($input: ProductInput!) {
  productCreate(input: $input) {
    product {
      id handle status
      variants(first: 1) {
        edges { node { id sku inventoryItem { id } } }
      }
    }
    userErrors { field message }
  }
}
"""

UPDATE_PRODUCT = """
mutation productUpdate($input: ProductInput!) {
  productUpdate(input: $input) {
    product { id handle status }
    userErrors { field message }
  }
}
"""

UPDATE_VARIANTS = """
mutation productVariantsBulkUpdate(
  $productId: ID!, $variants: [ProductVariantsBulkInput!]!
) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id sku price compareAtPrice }
    userErrors { field message }
  }
}
"""

ADD_MEDIA = """
mutation productCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
  productCreateMedia(productId: $productId, media: $media) {
    media { alt status }
    mediaUserErrors { field message }
  }
}
"""

# Stocking an item at a location before its quantity can be set.
ACTIVATE_INVENTORY = """
mutation inventoryActivate($inventoryItemId: ID!, $locationId: ID!) {
  inventoryActivate(inventoryItemId: $inventoryItemId, locationId: $locationId) {
    inventoryLevel { id quantities(names: ["available"]) { name quantity } }
    userErrors { field message }
  }
}
"""

SET_INVENTORY = """
mutation inventorySetQuantities($input: InventorySetQuantitiesInput!) {
  inventorySetQuantities(input: $input) {
    inventoryAdjustmentGroup { createdAt reason }
    userErrors { field message }
  }
}
"""

RECENT_ORDERS = """
query orders($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT) {
    edges {
      node {
        id name createdAt displayFulfillmentStatus displayFinancialStatus
        tags
        totalPriceSet { shopMoney { amount currencyCode } }
        customer { displayName }
        lineItems(first: 20) {
          edges { node { id title quantity sku } }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

TAG_ORDER = """
mutation tagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""
