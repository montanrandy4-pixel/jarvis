/** The HTTP surface: alerts, status, activity, and talking to the agent. */
import express from 'express';
import { describeError } from './lib/rules.js';

export function buildRoutes({ agent, store }) {
  const router = express.Router();

  const fail = (res, error, status = 500) =>
    res.status(status).json({ error: describeError(error) });

  router.get('/health', (_req, res) => {
    res.json({ ok: true, uptime: process.uptime() });
  });

  router.get('/status', (_req, res) => {
    try {
      res.json(agent.status());
    } catch (error) { fail(res, error); }
  });

  router.get('/alerts', (req, res) => {
    try {
      res.json({
        alerts: store.listAlerts({
          limit: Math.min(Number(req.query.limit) || 100, 500),
          includeResolved: req.query.resolved === 'true',
          severity: req.query.severity,
        }),
        counts: store.alertCounts(),
      });
    } catch (error) { fail(res, error); }
  });

  router.post('/alerts/:id/acknowledge', (req, res) => {
    try {
      const ok = store.acknowledgeAlert(Number(req.params.id));
      if (!ok) return res.status(404).json({ error: 'no such alert' });
      res.json({ ok: true });
    } catch (error) { fail(res, error); }
  });

  router.get('/activity', (req, res) => {
    try {
      res.json({
        activity: store.recentActivity(Math.min(Number(req.query.limit) || 50, 200)),
        runs: store.recentRuns(20),
      });
    } catch (error) { fail(res, error); }
  });

  /** Force a pass now, rather than waiting for the interval. */
  router.post('/agent/run', async (_req, res) => {
    try {
      const result = await agent.runOnce('manual');
      if (result.error) return res.status(502).json({ error: result.error });
      res.json({
        ok: true,
        skipped: result.skipped ?? null,
        created: result.created ?? 0,
        resolved: result.resolved ?? 0,
        unchanged: result.unchanged ?? false,
        summary: result.assessment?.summary ?? null,
      });
    } catch (error) { fail(res, error); }
  });

  /**
   * Ask the agent about the store.
   *
   * It understands a fixed set of questions rather than free text -- see
   * RulesAgent.ask -- and says so plainly when it does not recognise one.
   */
  router.post('/agent/ask', async (req, res) => {
    const question = String(req.body?.question ?? '').trim();
    if (!question) return res.status(400).json({ error: 'a question is required' });
    if (question.length > 2000) {
      return res.status(400).json({ error: 'question is too long' });
    }
    try {
      const facts = await agent.currentFacts();
      const { answer } = await agent.rules.ask(question, facts);
      store.log('prompt', question, answer);
      res.json({ answer });
    } catch (error) { fail(res, error, 502); }
  });

  router.post('/agent/start', (_req, res) => { agent.start(); res.json(agent.status()); });
  router.post('/agent/stop', (_req, res) => { agent.stop(); res.json(agent.status()); });

  /** Live feed for the dashboard. */
  router.get('/events', (req, res) => {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-store',
      Connection: 'keep-alive',
    });
    const send = (kind, data) =>
      res.write(`data: ${JSON.stringify({ kind, ...data })}\n\n`);

    send('hello', { status: agent.status() });
    const onAlert = (a) => send('alert', { alert: a });
    const onResolved = (r) => send('resolved', r);
    const onStart = (r) => send('run:start', r);
    const onEnd = (r) => send('run:end', { ...r, status: agent.status() });
    const onError = (r) => send('run:error', r);

    agent.on('alert', onAlert).on('resolved', onResolved)
      .on('run:start', onStart).on('run:end', onEnd).on('run:error', onError);

    // Proxies and browsers drop a silent stream; a comment frame keeps it open.
    const keepAlive = setInterval(() => res.write(': keep-alive\n\n'), 15_000);

    req.on('close', () => {
      clearInterval(keepAlive);
      agent.off('alert', onAlert).off('resolved', onResolved)
        .off('run:start', onStart).off('run:end', onEnd).off('run:error', onError);
    });
  });

  return router;
}
