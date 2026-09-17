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


# --- Store structure -------------------------------------------------------
# These are the newer Admin API mutations (pages and menus arrived in the
# 2024-10 cycle). If you run an older api_version, `shop build` will report
# them as unknown fields rather than failing silently.

COLLECTIONS = """
query collections($first: Int!, $after: String) {
  collections(first: $first, after: $after) {
    edges { node { id handle title descriptionHtml } }
    pageInfo { hasNextPage endCursor }
  }
}
"""

CREATE_COLLECTION = """
mutation collectionCreate($input: CollectionInput!) {
  collectionCreate(input: $input) {
    collection { id handle title }
    userErrors { field message }
  }
}
"""

UPDATE_COLLECTION = """
mutation collectionUpdate($input: CollectionInput!) {
  collectionUpdate(input: $input) {
    collection { id handle title }
    userErrors { field message }
  }
}
"""

PAGES = """
query pages($first: Int!, $after: String) {
  pages(first: $first, after: $after) {
    edges { node { id handle title body } }
    pageInfo { hasNextPage endCursor }
  }
}
"""

CREATE_PAGE = """
mutation pageCreate($page: PageCreateInput!) {
  pageCreate(page: $page) {
    page { id handle title }
    userErrors { field message }
  }
}
"""

UPDATE_PAGE = """
mutation pageUpdate($id: ID!, $page: PageUpdateInput!) {
  pageUpdate(id: $id, page: $page) {
    page { id handle title }
    userErrors { field message }
  }
}
"""

SHOP_POLICIES = """
query shopPolicies { shop { shopPolicies { id type body } } }
"""

UPDATE_POLICY = """
mutation shopPolicyUpdate($shopPolicy: ShopPolicyInput!) {
  shopPolicyUpdate(shopPolicy: $shopPolicy) {
    shopPolicy { id type }
    userErrors { field message }
  }
}
"""

MENUS = """
query menus($first: Int!, $after: String) {
  menus(first: $first, after: $after) {
    edges {
      node { id handle title items { id title url } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

CREATE_MENU = """
mutation menuCreate($title: String!, $handle: String!, $items: [MenuItemCreateInput!]!) {
  menuCreate(title: $title, handle: $handle, items: $items) {
    menu { id handle }
    userErrors { field message }
  }
}
"""

UPDATE_MENU = """
mutation menuUpdate(
  $id: ID!, $title: String!, $handle: String!, $items: [MenuItemUpdateInput!]!
) {
  menuUpdate(id: $id, title: $title, handle: $handle, items: $items) {
    menu { id handle }
    userErrors { field message }
  }
}
"""
