/* Dashboard. No build step and no framework on purpose: it is one page of
   state, and a bundler would be more machinery than the thing it builds. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const TONE = {
  critical: { bar: 'bg-crit', text: 'text-crit', label: 'critical' },
  warning:  { bar: 'bg-warn', text: 'text-warn', label: 'warning' },
  info:     { bar: 'bg-ok',   text: 'text-ok',   label: 'info' },
};

const ago = (iso) => {
  if (!iso) return '—';
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

async function api(path, options) {
  const response = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `request failed (${response.status})`);
  return body;
}

function renderStatus(status) {
  if (!status) return;
  const live = status.active;
  $('pulse').className =
    `h-2.5 w-2.5 rounded-full ${live ? 'bg-ok animate-pulse' : 'bg-muted'}`;
  $('toggleBtn').textContent = live ? 'Pause' : 'Resume';
  $('cCritical').textContent = status.alerts?.critical ?? 0;
  $('cCritical').className =
    `mt-1 text-2xl font-semibold tabular-nums ${status.alerts?.critical ? 'text-crit' : 'text-ok'}`;
  $('cWarning').textContent = status.alerts?.warning ?? 0;
  $('lastRun').textContent = ago(status.lastRun?.finished_at ?? status.lastRun?.started_at);
  $('spend').textContent = `$${(status.spend24h?.cost ?? 0).toFixed(3)}`;

  const bits = [status.model, `every ${status.intervalMinutes}m`];
  if (!status.hasApiKey) bits.push('no API key — agent idle');
  if (status.running) bits.push('checking now…');
  $('storeLine').textContent = bits.join('  ·  ');
}

function alertCard(alert) {
  const tone = TONE[alert.severity] ?? TONE.info;
  const resolved = Boolean(alert.resolved_at);
  return `
    <li class="relative overflow-hidden rounded-xl border border-line bg-panel
               ${resolved ? 'opacity-50' : ''}">
      <span class="absolute inset-y-0 left-0 w-1 ${tone.bar}"></span>
      <div class="pl-4 pr-4 py-3.5">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="text-sm font-medium leading-snug">${esc(alert.title)}</p>
            ${alert.detail ? `<p class="mt-1 text-xs text-muted leading-relaxed">${esc(alert.detail)}</p>` : ''}
            ${alert.recommendation
              ? `<p class="mt-2 text-xs ${tone.text}">→ ${esc(alert.recommendation)}</p>` : ''}
          </div>
          <div class="shrink-0 text-right">
            <span class="text-[10px] uppercase tracking-wider ${tone.text}">${tone.label}</span>
            <p class="mt-1 text-[10px] text-muted">${ago(alert.created_at)}</p>
          </div>
        </div>
        <div class="mt-2.5 flex items-center gap-3 text-[10px] text-muted">
          <span class="rounded border border-line px-1.5 py-0.5">${esc(alert.category)}</span>
          ${alert.subject ? `<span class="truncate">${esc(alert.subject)}</span>` : ''}
          ${resolved ? '<span class="text-ok">resolved</span>'
            : !alert.acknowledged
              ? `<button data-ack="${alert.id}" class="ml-auto hover:text-cream transition">acknowledge</button>`
              : '<span class="ml-auto">acknowledged</span>'}
        </div>
      </div>
    </li>`;
}

async function loadAlerts() {
  const includeResolved = $('showResolved').checked;
  try {
    const { alerts } = await api(`/alerts?resolved=${includeResolved}`);
    $('alerts').innerHTML = alerts.length
      ? alerts.map(alertCard).join('')
      : `<li class="rounded-xl border border-line bg-panel p-8 text-center text-sm text-muted">
           Nothing to report. The agent raises an alert only when there is something to act on.
         </li>`;
  } catch (error) {
    $('alerts').innerHTML =
      `<li class="rounded-xl border border-crit bg-panel p-5 text-sm text-crit">${esc(error.message)}</li>`;
  }
}

async function loadActivity() {
  try {
    const { activity } = await api('/activity?limit=15');
    $('activity').innerHTML = activity.map((row) => `
      <li class="flex gap-3 text-muted">
        <span class="w-16 shrink-0 tabular-nums">${ago(row.created_at)}</span>
        <span class="${row.kind === 'error' ? 'text-crit' : ''}">${esc(row.summary)}</span>
      </li>`).join('');
  } catch { /* the feed is not worth an error banner */ }
}

async function refresh() {
  try { renderStatus(await api('/status')); } catch { /* handled by SSE state */ }
  await Promise.all([loadAlerts(), loadActivity()]);
}

// ---- actions ----
$('runBtn').addEventListener('click', async () => {
  const button = $('runBtn');
  button.disabled = true;
  button.textContent = 'Checking…';
  try {
    const result = await api('/agent/run', { method: 'POST' });
    if (result.skipped) button.textContent = 'Nothing changed';
    else button.textContent = `${result.created} new`;
  } catch (error) {
    button.textContent = 'Failed';
    console.error(error);
  }
  await refresh();
  setTimeout(() => { button.disabled = false; button.textContent = 'Run check now'; }, 2200);
});

$('toggleBtn').addEventListener('click', async () => {
  const resuming = $('toggleBtn').textContent === 'Resume';
  renderStatus(await api(resuming ? '/agent/start' : '/agent/stop', { method: 'POST' }));
});

$('askForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const question = $('question').value.trim();
  if (!question) return;
  const box = $('answer');
  $('askBtn').disabled = true;
  box.classList.remove('hidden');
  box.textContent = 'Thinking…';
  try {
    const { answer } = await api('/agent/ask', {
      method: 'POST',
      body: JSON.stringify({ question }),
    });
    box.textContent = answer;
    loadActivity();
  } catch (error) {
    box.textContent = error.message;
  } finally {
    $('askBtn').disabled = false;
  }
});

$('alerts').addEventListener('click', async (event) => {
  const id = event.target?.dataset?.ack;
  if (!id) return;
  await api(`/alerts/${id}/acknowledge`, { method: 'POST' });
  loadAlerts();
});

$('showResolved').addEventListener('change', loadAlerts);

// ---- live ----
function connect() {
  const source = new EventSource('/api/events');
  source.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.status) renderStatus(message.status);
    if (['alert', 'resolved', 'run:end'].includes(message.kind)) refresh();
    if (message.kind === 'run:start') $('storeLine').textContent = 'checking now…';
  };
  source.onerror = () => {
    source.close();
    $('pulse').className = 'h-2.5 w-2.5 rounded-full bg-crit';
    setTimeout(connect, 4000);   // the server may be restarting
  };
}

refresh();
connect();
setInterval(refresh, 60_000);
