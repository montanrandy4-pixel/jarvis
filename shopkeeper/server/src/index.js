/** Boot: database, Shopify, agent, HTTP, dashboard. */
import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { loadConfig, validate } from './config.js';
import { Agent } from './agent.js';
import { buildRoutes } from './routes.js';
import { Shopify } from './lib/shopify.js';
import { openDatabase, createStore } from '../../database/db.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const CLIENT_DIR = path.resolve(here, '../../client');

export function buildApp({ config, store, agent }) {
  const app = express();
  app.use(express.json({ limit: '128kb' }));
  app.use('/api', buildRoutes({ agent, store }));
  app.use(express.static(CLIENT_DIR, { index: 'index.html' }));
  app.use((_req, res) => res.status(404).json({ error: 'not found' }));
  return app;
}

export function main() {
  const config = loadConfig();
  const { errors, warnings } = validate(config);
  for (const warning of warnings) console.warn(`warning: ${warning}`);
  if (errors.length) {
    for (const error of errors) console.error(`error: ${error}`);
    console.error('\nCopy .env.example to .env and fill it in.');
    process.exit(1);
  }

  const db = openDatabase(config.databaseFile);
  const store = createStore(db);
  const shopify = new Shopify({
    shopName: config.shopName,
    accessToken: config.shopifyAccessToken,
    apiVersion: config.shopifyApiVersion,
  });
  const agent = new Agent({ config, store, shopify });
  const app = buildApp({ config, store, agent });

  const server = app.listen(config.port, () => {
    console.log(`shopkeeper   http://localhost:${config.port}`);
    console.log(`  store      ${shopify.domain}`);
    console.log(`  interval   every ${config.intervalMinutes}m`);
    console.log(`  database   ${config.databaseFile}`);
    agent.start();
  });

  const shutdown = () => {
    console.log('\nstopping...');
    agent.stop();
    server.close(() => { db.close(); process.exit(0); });
    // Do not hang forever on a stuck connection.
    setTimeout(() => process.exit(0), 3000).unref();
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
  return { app, server, agent, store };
}

if (process.argv[1] && import.meta.url === `file://${process.argv[1]}`) main();
