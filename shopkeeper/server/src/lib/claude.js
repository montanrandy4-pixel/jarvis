/**
 * Claude, as the judgement layer.
 *
 * The thresholds in config catch what a rule can catch: stock under N, refunds
 * over X%. What a rule cannot do is weigh several ordinary-looking facts into
 * one worrying picture -- stock falling fast on the one product carrying the
 * week's revenue, a refund rate that is fine overall but concentrated in a
 * single SKU. That judgement is what the model is here for.
 *
 * Three things keep it honest and affordable:
 *
 *   Structured output. Alerts come back as validated objects, not prose to be
 *   regex'd. A malformed response is a caught error, not a corrupt alert.
 *
 *   A cached system prompt. The instructions and the schema are identical on
 *   every pass, so they are cached and cost a tenth as much to re-read.
 *
 *   Facts, not payloads. The caller sends a page of computed numbers rather
 *   than raw Shopify JSON -- fewer tokens, and a better-posed question.
 */
import Anthropic from '@anthropic-ai/sdk';
// Structured outputs are still under `beta` in SDK 0.71: the helper is
// `betaZodOutputFormat` and the call is `client.beta.messages.parse`.
// Its schema builder uses Zod 4's `z.toJSONSchema`, so Zod 4 is required
// even though the SDK's peer range still permits 3.x.
import { betaZodOutputFormat } from '@anthropic-ai/sdk/helpers/beta/zod';
import { z } from 'zod';

// Claude Opus 5 by default. Cost is controlled by calling it rarely and
// sending it little -- not by reaching for a weaker model.
export const DEFAULT_MODEL = 'claude-opus-5';

// Per million tokens, for the spend figures on the dashboard.
const PRICING = {
  'claude-opus-5':   { input: 5,  output: 25, cached: 0.5 },
  'claude-sonnet-5': { input: 2,  output: 10, cached: 0.2 },
  'claude-haiku-4-5':{ input: 1,  output: 5,  cached: 0.1 },
};

const Alert = z.object({
  severity: z.enum(['critical', 'warning', 'info']),
  category: z.enum(['inventory', 'orders', 'refunds', 'sales', 'catalog', 'system']),
  title: z.string(),
  detail: z.string(),
  recommendation: z.string(),
  subject: z.string(),
});

const Assessment = z.object({
  summary: z.string(),
  alerts: z.array(Alert),
});

const SYSTEM = `You are the monitoring agent for a Shopify store. Each pass you
receive a page of computed facts about the store and decide what, if anything,
the owner needs to know right now.

Raise an alert only when there is something to act on. A quiet store should
produce zero alerts, and saying so is a good answer. You are trusted because
you stay silent when nothing is wrong.

Severity:
  critical  money is being lost or a customer is being failed right now
  warning   will cost money if ignored this week
  info      worth knowing, not worth interrupting for

Rules:
- Use only the numbers given. Never invent a figure, a product or a trend.
- Quote the actual number in the detail. "Stock is 2" beats "stock is low".
- One alert per distinct condition. Do not split one problem across several.
- The subject must be the product title, order name, or "store" for shop-wide.
- The recommendation must be a concrete next action, not "monitor this".
- Thresholds in the facts are the owner's stated preferences. Respect them,
  but you may also raise something they did not think to set a threshold for.
- Ignore stock levels on untracked variants; they are not running out.`;

export class ClaudeAgent {
  constructor({ apiKey, model = DEFAULT_MODEL, maxTokens = 4096, client } = {}) {
    this.model = model;
    this.maxTokens = maxTokens;
    this.client = client ?? new Anthropic(apiKey ? { apiKey } : {});
  }

  /** Assess a page of facts. Returns { summary, alerts, usage }. */
  async assess(facts) {
    const response = await this.client.beta.messages.parse({
      model: this.model,
      max_tokens: this.maxTokens,
      thinking: { type: 'adaptive' },
      // Stable prefix first, volatile facts after: the system prompt and the
      // schema are byte-identical every pass, so they cache.
      system: [{ type: 'text', text: SYSTEM, cache_control: { type: 'ephemeral' } }],
      output_config: { format: betaZodOutputFormat(Assessment, 'assessment') },
      messages: [{
        role: 'user',
        content: `Store facts for this pass:\n\n${JSON.stringify(facts, null, 2)}`,
      }],
    });

    if (response.stop_reason === 'refusal') {
      throw new Error(
        `the model declined to answer (${response.stop_details?.category ?? 'unknown'})`
      );
    }
    // parsed_output is null when the response did not satisfy the schema.
    if (!response.parsed_output) {
      throw new Error('the model returned no parseable assessment');
    }

    return {
      ...response.parsed_output,
      usage: this.#usage(response),
    };
  }

  /** A free-text question from the dashboard, answered against the facts. */
  async ask(question, facts) {
    const response = await this.client.messages.create({
      model: this.model,
      max_tokens: this.maxTokens,
      thinking: { type: 'adaptive' },
      system: [{
        type: 'text',
        text: `${SYSTEM}\n\nThe owner is asking a direct question. Answer it from
the facts in plain prose -- no JSON. Be brief. If the facts do not contain the
answer, say so rather than guessing.`,
        cache_control: { type: 'ephemeral' },
      }],
      messages: [{
        role: 'user',
        content: `Store facts:\n\n${JSON.stringify(facts, null, 2)}\n\nQuestion: ${question}`,
      }],
    });

    if (response.stop_reason === 'refusal') {
      throw new Error('the model declined to answer that');
    }
    const text = response.content
      .filter((block) => block.type === 'text')
      .map((block) => block.text)
      .join('\n')
      .trim();

    return { answer: text || '(no answer returned)', usage: this.#usage(response) };
  }

  #usage(response) {
    const u = response.usage ?? {};
    const input = u.input_tokens ?? 0;
    const output = u.output_tokens ?? 0;
    const cached = u.cache_read_input_tokens ?? 0;
    const price = PRICING[this.model] ?? PRICING[DEFAULT_MODEL];
    const cost =
      (input / 1e6) * price.input +
      (output / 1e6) * price.output +
      (cached / 1e6) * price.cached;
    return { input, output, cached, cost: Math.round(cost * 1e6) / 1e6 };
  }
}

/**
 * A stable identity for an alert, so the same condition does not re-alert
 * every pass. Deliberately excludes the free-text detail: the model may phrase
 * the same problem differently next time, and that must not read as new.
 */
export function fingerprint(alert) {
  return `${alert.category}:${alert.subject}:${alert.title}`
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .slice(0, 180);
}

/** Describe an error without leaking a key into a log or the dashboard. */
export function describeError(error) {
  if (error instanceof Anthropic.AuthenticationError) {
    return 'ANTHROPIC_API_KEY is missing or invalid';
  }
  if (error instanceof Anthropic.RateLimitError) {
    return 'rate limited by the Anthropic API; the next pass will retry';
  }
  if (error instanceof Anthropic.APIConnectionError) {
    return 'could not reach the Anthropic API';
  }
  if (error instanceof Anthropic.BadRequestError) {
    return `the request was rejected: ${error.message}`;
  }
  if (error instanceof Anthropic.APIStatusError) {
    return `Anthropic API error ${error.status}`;
  }
  return error?.message ?? String(error);
}
