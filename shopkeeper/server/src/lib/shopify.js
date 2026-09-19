/**
 * The Shopify Admin API, over GraphQL.
 *
 * Hand-rolled over fetch rather than the Shopify SDK: this needs four queries,
 * and the SDK brings a dependency tree and an auth model built for embedded
 * apps. What it does need is Shopify's cost-based rate limiting, which the
 * SDK would not give us anyway.
 */

const API_VERSION = '2026-01';
// Leave this much of the leaky bucket spare before firing another query.
const COST_HEADROOM = 100;

export class ShopifyError extends Error {}

export class Shopify {
  constructor({ shopName, accessToken, apiVersion = API_VERSION, fetchImpl = fetch }) {
    if (!shopName) throw new ShopifyError('SHOP_NAME is not set');
    if (!accessToken) throw new ShopifyError('SHOPIFY_ACCESS_TOKEN is not set');
    // Accept either "my-shop" or "my-shop.myshopify.com".
    this.domain = shopName.includes('.') ? shopName : `${shopName}.myshopify.com`;
    this.accessToken = accessToken;
    this.endpoint = `https://${this.domain}/admin/api/${apiVersion}/graphql.json`;
    this.fetch = fetchImpl;
    this.available = null;
  }

  async query(document, variables = {}, { retries = 3 } = {}) {
    for (let attempt = 0; ; attempt++) {
      // Shopify reports the bucket on every response; wait only when it is
      // genuinely low rather than blindly sleeping between calls.
      if (this.available !== null && this.available < COST_HEADROOM) {
        await sleep(1000);
      }
      let response;
      try {
        response = await this.fetch(this.endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Shopify-Access-Token': this.accessToken,
          },
          body: JSON.stringify({ query: document, variables }),
        });
      } catch (cause) {
        if (attempt >= retries) throw new ShopifyError(`network error: ${cause.message}`);
        await sleep(backoff(attempt));
        continue;
      }

      if (response.status === 429 || response.status >= 500) {
        if (attempt >= retries) {
          throw new ShopifyError(`shopify returned ${response.status}`);
        }
        const retryAfter = Number(response.headers.get('retry-after')) || 0;
        await sleep(retryAfter * 1000 || backoff(attempt));
        continue;
      }
      if (!response.ok) {
        const body = await response.text().catch(() => '');
        throw new ShopifyError(
          `shopify returned ${response.status}: ${body.slice(0, 200)}`
        );
      }

      const payload = await response.json();
      const throttle = payload.extensions?.cost?.throttleStatus;
      if (throttle) this.available = throttle.currentlyAvailable;

      if (payload.errors?.length) {
        const message = payload.errors.map((e) => e.message).join('; ');
        // A throttle reported as an error is worth one more attempt.
        if (/throttl/i.test(message) && attempt < retries) {
          await sleep(backoff(attempt) + 1000);
          continue;
        }
        throw new ShopifyError(message);
      }
      return payload.data;
    }
  }

  async shopInfo() {
    const data = await this.query(`
      query { shop { name myshopifyDomain currencyCode ianaTimezone } }
    `);
    return data.shop;
  }

  /** Products with their variants, prices and stock. */
  async products(limit = 100) {
    const out = [];
    let cursor = null;
    while (out.length < limit) {
      const data = await this.query(`
        query products($first: Int!, $after: String) {
          products(first: $first, after: $after) {
            edges {
              cursor
              node {
                id title handle status totalInventory
                variants(first: 10) {
                  nodes {
                    id sku price inventoryQuantity
                    inventoryItem { tracked requiresShipping }
                  }
                }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      `, { first: Math.min(50, limit - out.length), after: cursor });

      const { edges, pageInfo } = data.products;
      out.push(...edges.map((e) => e.node));
      if (!pageInfo.hasNextPage) break;
      cursor = pageInfo.endCursor;
    }
    return out;
  }

  /** Orders since an ISO date, with enough to spot refunds and outliers. */
  async orders(sinceIso, limit = 100) {
    const out = [];
    let cursor = null;
    while (out.length < limit) {
      const data = await this.query(`
        query orders($first: Int!, $after: String, $query: String) {
          orders(first: $first, after: $after, query: $query,
                 sortKey: CREATED_AT, reverse: true) {
            edges {
              cursor
              node {
                id name createdAt
                displayFinancialStatus displayFulfillmentStatus
                currentTotalPriceSet { shopMoney { amount currencyCode } }
                totalRefundedSet { shopMoney { amount } }
                customer { numberOfOrders }
                lineItems(first: 20) { nodes { title quantity sku } }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      `, {
        first: Math.min(50, limit - out.length),
        after: cursor,
        query: sinceIso ? `created_at:>='${sinceIso}'` : '',
      });

      const { edges, pageInfo } = data.orders;
      out.push(...edges.map((e) => e.node));
      if (!pageInfo.hasNextPage) break;
      cursor = pageInfo.endCursor;
    }
    return out;
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const backoff = (attempt) => Math.min(8000, 500 * 2 ** attempt) + Math.random() * 300;
