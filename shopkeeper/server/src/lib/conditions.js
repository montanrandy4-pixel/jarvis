/**
 * Turning a shop into a page of facts.
 *
 * The model is expensive per token and the Shopify payload is large and mostly
 * irrelevant, so nothing raw is ever sent. This computes the handful of numbers
 * a shopkeeper would actually look at, and the model reasons over those.
 *
 * It also decides what is *worth* sending: if the facts are identical to last
 * pass, there is nothing new to reason about and the call is skipped entirely.
 * That is the single biggest cost lever in the whole app.
 */
import crypto from 'node:crypto';

export function gather({ shop, products, orders, previousInventory = {}, config }) {
  const now = Date.now();
  const dayAgo = now - 86_400_000;
  const weekAgo = now - 7 * 86_400_000;

  const variants = [];
  for (const product of products) {
    for (const variant of product.variants?.nodes ?? []) {
      variants.push({
        productId: product.id,
        variantId: variant.id,
        title: product.title,
        sku: variant.sku ?? '',
        status: product.status,
        price: Number(variant.price ?? 0),
        tracked: variant.inventoryItem?.tracked ?? false,
        available: variant.inventoryQuantity ?? null,
      });
    }
  }

  // Only tracked variants can be "low": an untracked digital product is not
  // running out of anything.
  const tracked = variants.filter((v) => v.tracked && v.available !== null);
  const lowStock = tracked
    .filter((v) => v.available <= config.lowStockThreshold)
    .map((v) => ({
      ...pick(v, ['title', 'sku', 'available', 'price']),
      previous: previousInventory[v.variantId] ?? null,
      dropped: previousInventory[v.variantId] != null
        ? previousInventory[v.variantId] - v.available
        : null,
    }))
    .sort((a, b) => a.available - b.available)
    .slice(0, 25);

  const inWindow = (o, from) => new Date(o.createdAt).getTime() >= from;
  const amount = (o) => Number(o.currentTotalPriceSet?.shopMoney?.amount ?? 0);
  const refunded = (o) => Number(o.totalRefundedSet?.shopMoney?.amount ?? 0);

  const last24 = orders.filter((o) => inWindow(o, dayAgo));
  const last7d = orders.filter((o) => inWindow(o, weekAgo));

  const revenue24 = sum(last24.map(amount));
  const revenue7d = sum(last7d.map(amount));
  const refunds7d = sum(last7d.map(refunded));

  // Compare the last day against the daily average of the preceding week, so
  // "sales are down" means something rather than reflecting a quiet Tuesday.
  const priorDaily = last7d.length > last24.length
    ? (revenue7d - revenue24) / 6
    : null;

  const facts = {
    shop: { name: shop.name, currency: shop.currencyCode },
    catalog: {
      products: products.length,
      variants: variants.length,
      active: products.filter((p) => p.status === 'ACTIVE').length,
      trackedVariants: tracked.length,
      outOfStock: tracked.filter((v) => v.available <= 0).length,
    },
    inventory: { threshold: config.lowStockThreshold, low: lowStock },
    sales: {
      orders24h: last24.length,
      revenue24h: round(revenue24),
      orders7d: last7d.length,
      revenue7d: round(revenue7d),
      averageOrder24h: last24.length ? round(revenue24 / last24.length) : 0,
      priorDailyAverage: priorDaily === null ? null : round(priorDaily),
      changeVsPriorDaily: priorDaily ? round(((revenue24 - priorDaily) / priorDaily) * 100) : null,
    },
    refunds: {
      amount7d: round(refunds7d),
      rate7d: revenue7d > 0 ? round((refunds7d / revenue7d) * 100) : 0,
      orders: last7d.filter((o) => refunded(o) > 0).length,
    },
    attention: {
      unpaid: orders.filter(
        (o) => !['PAID', 'PARTIALLY_REFUNDED', 'REFUNDED'].includes(o.displayFinancialStatus)
      ).slice(0, 10).map((o) => ({
        name: o.name, status: o.displayFinancialStatus, total: amount(o),
      })),
      unfulfilled: orders.filter(
        (o) => o.displayFulfillmentStatus === 'UNFULFILLED'
      ).length,
      largest24h: last24.length ? round(Math.max(...last24.map(amount))) : 0,
    },
    thresholds: {
      lowStock: config.lowStockThreshold,
      highValueOrder: config.highValueOrder,
      refundRatePercent: config.refundRateThreshold,
      salesDropPercent: config.salesDropThreshold,
    },
  };

  return { facts, variants, hash: hashFacts(facts) };
}

/**
 * A fingerprint of the things worth reasoning about.
 *
 * Deliberately excludes timestamps and anything that drifts every pass -- if
 * this changed constantly the skip-when-unchanged optimisation would never
 * fire and every pass would cost a model call.
 */
export function hashFacts(facts) {
  const stable = {
    catalog: facts.catalog,
    low: facts.inventory.low.map((v) => [v.sku || v.title, v.available]),
    orders24h: facts.sales.orders24h,
    revenueBand: Math.round(facts.sales.revenue24h / 10),
    refundRate: Math.round(facts.refunds.rate7d),
    unpaid: facts.attention.unpaid.map((o) => o.name),
    unfulfilled: facts.attention.unfulfilled,
  };
  return crypto.createHash('sha256')
    .update(JSON.stringify(stable))
    .digest('hex')
    .slice(0, 16);
}

const sum = (xs) => xs.reduce((a, b) => a + b, 0);
const round = (n) => Math.round(n * 100) / 100;
const pick = (obj, keys) => Object.fromEntries(keys.map((k) => [k, obj[k]]));
