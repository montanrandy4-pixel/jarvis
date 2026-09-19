/* The panel: live state, stats and the activity feed.
 *
 * Deliberately free of dependencies. The 3D layer needs Three.js from a CDN,
 * and a CDN can be blocked, slow or offline -- when that happens you should
 * still get a working workspace, just a flat one. So everything that matters
 * lives here, and the scene subscribes to it rather than the other way round.
 */
const listeners = [];
export function onUpdate(fn) { listeners.push(fn); }
function broadcast(kind, payload) {
  for (const fn of listeners) { try { fn(kind, payload); } catch (e) { console.error(e); } }
}

const el = (id) => document.getElementById(id);
export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

let nextPassAt = 0;
let feedPainted = false;

export function paint(s) {
  if (!s) return;
  el('shopName').textContent = s.shop?.name || s.shop?.domain || 'shop';
  el('shopDomain').textContent = s.shop?.domain || '';
  el('dryRun').hidden = !s.dry_run;
  el('pulse').classList.add('live');

  const sum = s.summary || {};
  el('revenue').textContent = sum.revenue != null
    ? `${Number(sum.revenue).toLocaleString(undefined,
        { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${sum.currency || ''}`.trim()
    : '—';
  el('orders').textContent = sum.orders ?? '—';

  const blockers = (s.products || []).filter((p) => p.state === 'blocker').length;
  const b = el('blockers');
  b.textContent = blockers;
  b.dataset.zero = blockers === 0 ? '1' : '0';

  nextPassAt = Date.now() + (s.pass?.next_in ?? 0) * 1000;

  if (!feedPainted && s.alerts?.length) {
    s.alerts.slice(-12).forEach((a) => pushFeed(a.severity, a.title, a.detail));
    feedPainted = true;
  }
  broadcast('state', s);
}

export function pushFeed(severity, title, detail) {
  const feed = el('feed');
  if (feed.firstElementChild?.classList.contains('muted')) feed.innerHTML = '';
  const li = document.createElement('li');
  li.className = severity;
  li.innerHTML = escapeHtml(title) +
    (detail ? `<span class="t">${escapeHtml(detail)}</span>` : '');
  feed.prepend(li);
  while (feed.children.length > 40) feed.lastElementChild.remove();
}

function setStage(stage) {
  document.querySelectorAll('.stage').forEach((n) =>
    n.classList.toggle('active', n.dataset.stage === stage));
}

setInterval(() => {
  if (!nextPassAt) return;
  const left = Math.max(0, Math.round((nextPassAt - Date.now()) / 1000));
  const m = Math.floor(left / 60);
  el('countdown').textContent = m > 0 ? `${m}m ${left % 60}s` : `${left}s`;
}, 1000);

function connect() {
  const source = new EventSource('/api/events');
  source.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.kind === 'hello' || msg.kind === 'pass') {
      paint(msg.snapshot);
      if (msg.kind === 'pass') setStage(null);
    } else if (msg.kind === 'scan') {
      setStage(msg.stage);
      broadcast('scan', msg.stage);
    } else if (msg.kind === 'alert') {
      pushFeed(msg.alert.severity, msg.alert.title, msg.alert.detail);
      broadcast('alert', msg.alert);
    } else if (msg.kind === 'resolved') {
      pushFeed('resolved', 'resolved', msg.key);
    }
  };
  source.onerror = () => {
    el('pulse').classList.remove('live');
    source.close();
    setTimeout(connect, 4000);   // between passes, or restarting -- keep trying
  };
}

fetch('/api/state').then((r) => r.json()).then(paint).catch(() => {});
connect();
