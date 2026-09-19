/** Configuration, from the environment, validated once at boot. */
import 'dotenv/config';
import path from 'node:path';

const num = (value, fallback) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};

export function loadConfig(env = process.env) {
  return {
    port: num(env.PORT, 3000),
    databaseFile: env.DATABASE_FILE
      ? path.resolve(env.DATABASE_FILE)
      : path.resolve('database/shopkeeper.db'),

    shopName: env.SHOP_NAME ?? '',
    shopifyAccessToken: env.SHOPIFY_ACCESS_TOKEN ?? '',
    shopifyApiVersion: env.SHOPIFY_API_VERSION ?? '2026-01',

    anthropicApiKey: env.ANTHROPIC_API_KEY ?? '',
    model: env.CLAUDE_MODEL ?? 'claude-opus-5',

    intervalMinutes: num(env.AGENT_INTERVAL_MINUTES, 30),
    runOnStart: env.AGENT_RUN_ON_START !== 'false',
    // Skip the model call when the facts are unchanged. The biggest cost
    // lever there is; turn it off only to debug.
    skipWhenUnchanged: env.AGENT_SKIP_UNCHANGED !== 'false',

    lowStockThreshold: num(env.LOW_STOCK_THRESHOLD, 5),
    highValueOrder: num(env.HIGH_VALUE_ORDER, 250),
    refundRateThreshold: num(env.REFUND_RATE_THRESHOLD, 10),
    salesDropThreshold: num(env.SALES_DROP_THRESHOLD, 40),

    orderLookbackDays: num(env.ORDER_LOOKBACK_DAYS, 7),
  };
}

/** Problems that stop the app starting, and warnings that do not. */
export function validate(config) {
  const errors = [];
  const warnings = [];
  if (!config.shopName) errors.push('SHOP_NAME is not set');
  if (!config.shopifyAccessToken) errors.push('SHOPIFY_ACCESS_TOKEN is not set');
  if (!config.anthropicApiKey) {
    warnings.push(
      'ANTHROPIC_API_KEY is not set -- the dashboard and Shopify sync will work, ' +
      'but the agent cannot assess anything'
    );
  }
  if (config.intervalMinutes < 1) errors.push('AGENT_INTERVAL_MINUTES must be at least 1');
  return { errors, warnings };
}
