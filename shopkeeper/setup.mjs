/* One-command setup. Checks Node, installs dependencies, asks for the three
   secrets, writes .env, and offers to start the server.

   Run it with:  node setup.mjs

   Safe to run twice -- it never overwrites an answer you already gave. */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline/promises';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ENV = path.join(HERE, '.env');
const EXAMPLE = path.join(HERE, '.env.example');

const bold = (s) => `\x1b[1m${s}\x1b[0m`;
const dim = (s) => `\x1b[2m${s}\x1b[0m`;
const green = (s) => `\x1b[32m${s}\x1b[0m`;
const red = (s) => `\x1b[31m${s}\x1b[0m`;

const say = (s = '') => console.log(s);
const step = (n, s) => say(`\n${bold(`[${n}]`)} ${s}`);
const ok = (s) => say(`    ${green('✓')} ${s}`);
const bad = (s) => say(`    ${red('✗')} ${s}`);

/** Run a command, letting its output through to the terminal. */
function run(command, args) {
  return new Promise((resolve) => {
    const child = spawn(command, args, {
      cwd: HERE,
      stdio: 'inherit',
      // npm is a .cmd shim on Windows, which needs a shell to resolve.
      shell: process.platform === 'win32',
    });
    child.on('close', (code) => resolve(code === 0));
    child.on('error', () => resolve(false));
  });
}

say(bold('\nShopkeeper setup\n'));

// ---- 1. Node ------------------------------------------------------------
step(1, 'Checking Node');
const major = Number(process.versions.node.split('.')[0]);
if (major < 20) {
  bad(`Node ${process.versions.node} is too old. This needs 20 or newer.`);
  say(`\n    Install the LTS version from ${bold('https://nodejs.org')},`);
  say('    then run this again.\n');
  process.exit(1);
}
ok(`Node ${process.versions.node}`);

// ---- 2. Dependencies ----------------------------------------------------
step(2, 'Installing dependencies');
if (fs.existsSync(path.join(HERE, 'node_modules', 'express'))) {
  ok('Already installed, skipping.');
} else {
  say(dim('    This takes a minute the first time.\n'));
  if (!await run('npm', ['install'])) {
    bad('npm install failed.');
    say('\n    The usual cause is a network hiccup -- try running');
    say(`    ${bold('npm install')} on its own and read the error.`);
    say('\n    If it mentions node-gyp, Python or a C++ compiler, the');
    say('    database library could not find a prebuilt binary for your');
    say('    machine. Installing the current Node LTS usually fixes that.\n');
    process.exit(1);
  }
  ok('Installed.');
}

// ---- 3. Configuration ---------------------------------------------------
step(3, 'Setting up your keys');

if (!fs.existsSync(ENV)) fs.copyFileSync(EXAMPLE, ENV);
let env = fs.readFileSync(ENV, 'utf8');

/** Read the current value of a key from the .env text. */
const current = (key) => {
  const match = env.match(new RegExp(`^${key}=(.*)$`, 'm'));
  return match ? match[1].trim() : '';
};

/** Replace a key's value in the .env text, preserving the comments around it. */
const set = (key, value) => {
  const line = `${key}=${value}`;
  env = new RegExp(`^${key}=.*$`, 'm').test(env)
    ? env.replace(new RegExp(`^${key}=.*$`, 'm'), line)
    : `${env.trimEnd()}\n${line}\n`;
};

/** A value still at its .env.example placeholder counts as unanswered. */
const unset = (value) => !value || value.includes('xxxx') || value === 'my-store';

const questions = [
  {
    key: 'SHOP_NAME',
    label: 'Your Shopify store name',
    hint: 'Just the part before .myshopify.com -- e.g. wwt2sq-wi',
    valid: (v) => v.length > 0,
    clean: (v) => v.replace(/^https?:\/\//, '').replace(/\.myshopify\.com\/?$/, ''),
  },
  {
    key: 'SHOPIFY_ACCESS_TOKEN',
    label: 'Shopify Admin API access token',
    hint: 'Shopify admin -> Settings -> Apps and sales channels -> Develop apps\n'
        + '      -> Create an app -> Configure Admin API scopes -> tick\n'
        + '      read_products, read_inventory, read_orders -> Install\n'
        + '      -> Reveal token once. It starts with shpat_',
    valid: (v) => v.startsWith('shpat_') && v.length > 20,
    invalidMessage: 'That does not look like a token -- they start with shpat_',
  },
  {
    key: 'ANTHROPIC_API_KEY',
    label: 'Anthropic API key',
    hint: 'https://console.anthropic.com/settings/keys -> Create Key.\n'
        + '      It starts with sk-ant-',
    valid: (v) => v.startsWith('sk-ant-') && v.length > 20,
    invalidMessage: 'That does not look like a key -- they start with sk-ant-',
  },
];

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

let answered = 0;
for (const q of questions) {
  if (!unset(current(q.key))) {
    ok(`${q.key} is already set. ${dim('(delete .env to start over)')}`);
    answered += 1;
    continue;
  }

  say(`\n    ${bold(q.label)}`);
  say(dim(`      ${q.hint}`));

  for (;;) {
    const raw = (await rl.question('\n    > ')).trim();
    if (!raw) {
      say(dim('    Skipped. You can fill this in later by editing .env'));
      break;
    }
    const value = q.clean ? q.clean(raw) : raw;
    if (!q.valid(value)) {
      say(`    ${red(q.invalidMessage ?? 'That does not look right.')}`);
      continue;
    }
    set(q.key, value);
    fs.writeFileSync(ENV, env, { mode: 0o600 });
    ok('Saved.');
    answered += 1;
    break;
  }
}

// The file holds live credentials; keep it readable only by this user.
try { fs.chmodSync(ENV, 0o600); } catch { /* Windows has no chmod; fine. */ }

// ---- 4. Done ------------------------------------------------------------
if (answered < questions.length) {
  say(`\n${bold('Not finished.')} ${questions.length - answered} of `
    + `${questions.length} keys are still missing.`);
  say(`Open ${bold('.env')} in any text editor to fill them in, then run`);
  say(`${bold('npm start')}.\n`);
  rl.close();
  process.exit(0);
}

say(`\n${green(bold('Ready.'))} Your keys are in .env -- that file is private`);
say('and is never committed to git.\n');

const start = (await rl.question('Start the dashboard now? [Y/n] ')).trim().toLowerCase();
rl.close();

if (start === 'n' || start === 'no') {
  say(`\nRun ${bold('npm start')} when you are ready, then open`);
  say(`${bold('http://localhost:3000')}\n`);
  process.exit(0);
}

say(`\nStarting. Open ${bold('http://localhost:3000')} in your browser.`);
say(dim('Press Ctrl+C here to stop it.\n'));
await run('npm', ['start']);
