/**
 * The judgement layer, as ordinary code.
 *
 * Every alert in this file is a rule you can read, predict and argue with.
 * Nothing is sent anywhere, nothing costs money, and the same facts always
 * produce the same alerts -- which is what makes the dedup downstream work:
 * a condition that has not changed produces a byte-identical fingerprint and
 * does not re-alert.
 *
 * The thresholds are the owner's, from config. The rules decide severity.
 */

/** Currency-aware formatting, since a shop is not necessarily in dollars. */
const SYMBOLS = { USD: '$', EUR: '€', GBP: '£', CAD: 'CA$', AUD: 'A$', JPY: '¥' };
const money = (amount, currency = 'USD') => {
  const n = Number(amount ?? 0);
  const shown = n.toFixed(n % 1 === 0 ? 0 : 2);
  return SYMBOLS[currency] ? `${SYMBOLS[currency]}${shown}` : `${shown} ${currency}`;
};
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The identity of a variant in an alert: a SKU if it has one, else its name. */
const label = (v) => v.sku || v.title;

export class RulesAgent {
  constructor({ config } = {}) {
    this.config = config ?? {};
  }

  /**
   * Assess a page of facts. Returns { summary, alerts }.
   *
   * Async only so it matches the shape the agent loop expects; it does no I/O
   * and never rejects.
   */
  async assess(facts) {
    const alerts = [];
    const currency = facts.shop?.currency ?? 'USD';
    const t = facts.thresholds ?? {};

    // ---- Catalog --------------------------------------------------------
    if (facts.catalog.products === 0) {
      alerts.push({
        severity: 'critical', category: 'catalog', subject: 'store',
        title: 'The store has no products',
        detail: 'Shopify returned an empty catalog. Nothing can be sold.',
        recommendation: 'Check that the access token belongs to the right store.',
      });
    } else if (facts.catalog.active === 0) {
      alerts.push({
        severity: 'critical', category: 'catalog', subject: 'store',
        title: 'No products are published',
        detail: `All ${plural(facts.catalog.products, 'product')} are draft or `
              + 'archived, so customers see an empty store.',
        recommendation: 'Set the products you intend to sell to Active.',
      });
    }

    // ---- Inventory ------------------------------------------------------
    // One alert per variant, so each clears on its own when restocked.
    for (const v of facts.inventory.low) {
      const out = v.available <= 0;

      // Falling faster than the checking interval can keep up with: it will
      // be gone before anyone looks again.
      const emptiesBeforeNextPass =
        !out && v.dropped != null && v.dropped >= v.available;

      alerts.push({
        severity: out ? 'critical' : emptiesBeforeNextPass ? 'critical' : 'warning',
        category: 'inventory',
        subject: v.title,
        title: out ? 'Out of stock' : 'Low stock',
        detail: [
          out
            ? `${label(v)} is out of stock and cannot be bought.`
            : `${label(v)} has ${plural(v.available, 'unit')} left `
              + `(your threshold is ${t.lowStock}).`,
          v.dropped > 0 ? `Down ${v.dropped} since the last check.` : null,
          emptiesBeforeNextPass
            ? 'At that rate it runs out before the next check.'
            : null,
        ].filter(Boolean).join(' '),
        recommendation: out
          ? `Restock ${label(v)} or hide it so customers stop finding it.`
          : `Reorder ${label(v)} now.`,
      });
    }

    // ---- Refunds --------------------------------------------------------
    const rate = facts.refunds.rate7d;
    if (facts.sales.revenue7d > 0 && rate >= t.refundRatePercent) {
      alerts.push({
        severity: rate >= t.refundRatePercent * 2 ? 'critical' : 'warning',
        category: 'refunds', subject: 'store',
        title: 'Refund rate is above your threshold',
        detail: `${rate}% of the last 7 days' revenue has been refunded `
              + `(${money(facts.refunds.amount7d, currency)} across `
              + `${plural(facts.refunds.orders, 'order')}). `
              + `Your threshold is ${t.refundRatePercent}%.`,
        recommendation: 'Read the refunded orders for a common cause — a '
                      + 'broken download, a misleading description, a duplicate charge.',
      });
    }

    // ---- Sales ----------------------------------------------------------
    const change = facts.sales.changeVsPriorDaily;
    if (change !== null && change <= -t.salesDropPercent) {
      alerts.push({
        severity: 'warning', category: 'sales', subject: 'store',
        title: 'Sales are down against the past week',
        detail: `The last 24 hours brought ${money(facts.sales.revenue24h, currency)} `
              + `against a daily average of `
              + `${money(facts.sales.priorDailyAverage, currency)} over the `
              + `previous 6 days — down ${Math.abs(change)}%.`,
        recommendation: 'Check that checkout works and that nothing has been '
                      + 'unpublished before reading anything into it.',
      });
    }

    if (facts.attention.largest24h >= t.highValueOrder) {
      alerts.push({
        severity: 'info', category: 'sales', subject: 'store',
        title: 'A high-value order came in',
        detail: `The largest order in the last 24 hours was `
              + `${money(facts.attention.largest24h, currency)}, at or above `
              + `your ${money(t.highValueOrder, currency)} mark.`,
        recommendation: 'Worth checking it is genuine before it ships.',
      });
    }

    // ---- Orders needing a human ----------------------------------------
    for (const order of facts.attention.unpaid) {
      alerts.push({
        severity: 'warning', category: 'orders', subject: order.name,
        title: 'Order has not been paid',
        detail: `${order.name} is ${String(order.status).toLowerCase().replace(/_/g, ' ')} `
              + `for ${money(order.total, currency)}.`,
        recommendation: `Collect payment for ${order.name} or cancel it.`,
      });
    }

    if (facts.attention.unfulfilled > 0) {
      alerts.push({
        severity: facts.attention.unfulfilled >= 5 ? 'warning' : 'info',
        category: 'orders', subject: 'store',
        // The count lives in the detail, not the title: the title is part of
        // the fingerprint, so a changing count must not read as a new alert.
        title: 'Orders are waiting to be fulfilled',
        detail: `${plural(facts.attention.unfulfilled, 'order')} in the last `
              + `${this.config.orderLookbackDays ?? 7} days `
              + `${facts.attention.unfulfilled === 1 ? 'has' : 'have'} not been fulfilled.`,
        recommendation: 'Fulfil them, or check that automatic fulfilment is on '
                      + 'for digital products.',
      });
    }

    // ---- The daily summary ---------------------------------------------
    // Dated, so there is exactly one per day: tomorrow's has a different
    // fingerprint, which creates it and resolves today's.
    if (this.config.dailySummary !== false) {
      const day = new Date().toISOString().slice(0, 10);
      alerts.push({
        severity: 'info', category: 'sales', subject: day,
        title: 'Daily summary',
        detail: `${plural(facts.sales.orders24h, 'order')} worth `
              + `${money(facts.sales.revenue24h, currency)} in the last 24 hours`
              + (facts.sales.orders24h
                  ? `, averaging ${money(facts.sales.averageOrder24h, currency)}.`
                  : '.')
              + ` Past 7 days: ${plural(facts.sales.orders7d, 'order')} worth `
              + `${money(facts.sales.revenue7d, currency)}.`,
        recommendation: 'Nothing to do — this is the day in one line.',
      });
    }

    return { summary: summarise(alerts, facts, currency), alerts };
  }

  /**
   * Answer a question about the store.
   *
   * Without a model this understands a fixed set of questions rather than
   * anything you can type, so the important behaviour is the failure: when it
   * does not recognise a question it says so and lists what it does know,
   * instead of guessing and sounding confident.
   */
  async ask(question, facts) {
    const q = String(question).toLowerCase();
    const currency = facts.shop?.currency ?? 'USD';
    const has = (...words) => words.some((w) => q.includes(w));

    if (has('help', 'what can you', 'what do you', 'commands')) {
      return { answer: helpText() };
    }

    if (has('stock', 'inventory', 'low', 'out of', 'running out', 'restock')) {
      const low = facts.inventory.low;
      if (!low.length) {
        return {
          answer: facts.catalog.trackedVariants === 0
            ? 'Nothing in this store tracks inventory, so nothing can run low. '
              + 'That is normal for digital products.'
            : `Nothing is at or below your threshold of ${facts.inventory.threshold}. `
              + `${plural(facts.catalog.trackedVariants, 'variant')} tracked.`,
        };
      }
      const lines = low.map(
        (v) => `  ${label(v)}: ${v.available}`
             + (v.dropped > 0 ? ` (down ${v.dropped} since last check)` : '')
      );
      return {
        answer: `${plural(low.length, 'item')} at or below `
              + `${facts.inventory.threshold}:\n${lines.join('\n')}`,
      };
    }

    if (has('refund', 'return', 'chargeback')) {
      if (!facts.refunds.orders) {
        return { answer: 'No refunds in the last 7 days.' };
      }
      return {
        answer: `${money(facts.refunds.amount7d, currency)} refunded across `
              + `${plural(facts.refunds.orders, 'order')} in the last 7 days — `
              + `${facts.refunds.rate7d}% of revenue, against your threshold of `
              + `${facts.thresholds.refundRatePercent}%.`,
      };
    }

    if (has('sale', 'revenue', 'money', 'made', 'earn', 'sell', 'sold', 'today', 'order')) {
      const change = facts.sales.changeVsPriorDaily;
      return {
        answer: `Last 24 hours: ${plural(facts.sales.orders24h, 'order')} worth `
              + `${money(facts.sales.revenue24h, currency)}`
              + (facts.sales.orders24h
                  ? `, averaging ${money(facts.sales.averageOrder24h, currency)}.`
                  : '.')
              + `\nLast 7 days: ${plural(facts.sales.orders7d, 'order')} worth `
              + `${money(facts.sales.revenue7d, currency)}.`
              + (change === null ? ''
                  : `\nAgainst the prior daily average, that is `
                    + `${change >= 0 ? 'up' : 'down'} ${Math.abs(change)}%.`),
      };
    }

    if (has('product', 'catalog', 'how many', 'listing', 'publish')) {
      return {
        answer: `${plural(facts.catalog.products, 'product')} `
              + `(${facts.catalog.active} active) across `
              + `${plural(facts.catalog.variants, 'variant')}. `
              + `${facts.catalog.trackedVariants} track inventory, `
              + `${facts.catalog.outOfStock} of those are out of stock.`,
      };
    }

    if (has('attention', 'problem', 'wrong', 'issue', 'status', 'alert', 'unpaid', 'unfulfilled')) {
      const bits = [];
      if (facts.attention.unpaid.length) {
        bits.push(`${plural(facts.attention.unpaid.length, 'unpaid order')}: `
                + facts.attention.unpaid.map((o) => o.name).join(', '));
      }
      if (facts.attention.unfulfilled) {
        bits.push(`${plural(facts.attention.unfulfilled, 'order')} unfulfilled`);
      }
      if (facts.inventory.low.length) {
        bits.push(`${plural(facts.inventory.low.length, 'item')} low on stock`);
      }
      if (facts.refunds.rate7d >= facts.thresholds.refundRatePercent) {
        bits.push(`refund rate at ${facts.refunds.rate7d}%`);
      }
      return {
        answer: bits.length
          ? `Needs a look:\n${bits.map((b) => `  ${b}`).join('\n')}`
          : 'Nothing needs attention right now.',
      };
    }

    return {
      answer: `I do not understand that one. Without an AI model behind it I `
            + `only know a fixed set of questions.\n\n${helpText()}`,
    };
  }
}

function helpText() {
  return 'Try asking about:\n'
       + '  stock      — what is running low or out\n'
       + '  sales      — revenue and orders, 24 hours and 7 days\n'
       + '  refunds    — refund total and rate\n'
       + '  products   — how many, how many active\n'
       + '  attention  — everything currently worth a look';
}

/** One line for the dashboard and the activity log. */
function summarise(alerts, facts, currency) {
  const worth = alerts.filter((a) => a.severity !== 'info');
  const sales = `${plural(facts.sales.orders24h, 'order')} / `
              + `${money(facts.sales.revenue24h, currency)} in 24h`;
  if (!worth.length) return `Nothing needs attention. ${sales}.`;
  const critical = worth.filter((a) => a.severity === 'critical').length;
  return `${plural(worth.length, 'thing')} to look at`
       + (critical ? ` (${critical} critical)` : '')
       + `. ${sales}.`;
}

/**
 * A stable identity for an alert, so the same condition does not re-alert
 * every pass. Excludes the free-text detail, which carries the numbers: stock
 * falling from 4 to 3 is the same alert, not a new one.
 */
export function fingerprint(alert) {
  return `${alert.category}:${alert.subject}:${alert.title}`
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .slice(0, 180);
}

/** Describe an error without leaking a token into a log or the dashboard. */
export function describeError(error) {
  const message = error?.message ?? String(error);
  if (error?.status === 401 || error?.status === 403) {
    return 'Shopify rejected the access token — check SHOPIFY_ACCESS_TOKEN '
         + 'and that the app has read_products, read_inventory and read_orders.';
  }
  if (error?.status === 404) {
    return 'Shopify returned 404 — check SHOP_NAME.';
  }
  if (error?.status === 429) {
    return 'Rate limited by Shopify; the next pass will retry.';
  }
  if (error?.code === 'ENOTFOUND' || error?.code === 'ECONNREFUSED'
      || /fetch failed/i.test(message)) {
    return 'Could not reach Shopify — check the network connection.';
  }
  // Never echo a header or token back out, however the error was built.
  return message.replace(/shpat_[A-Za-z0-9_-]+/g, 'shpat_***');
}
