// Offline request contract against the actual patched upstream module.
import assert from 'node:assert/strict';
import { helperJson } from '/opt/jev/src/model/text.ts';
process.env.TEXT_MODEL_API_KEY = 'fixture-not-a-key';
process.env.TEXT_MODEL = 'gpt-6-luna';
for (const base of ['https://api.openai.com/v1', 'https://openrouter.ai/api/v1']) {
  process.env.TEXT_MODEL_BASE_URL = base;
  for (const reason of [false, true]) {
    let request;
    globalThis.fetch = async (url, options) => {
      request = JSON.parse(options.body);
      assert.equal(url, `${base}/chat/completions`);
      return new Response(JSON.stringify({ choices: [{ message: { content: '{"answer":"fixture"}' } }] }));
    };
    await helperJson('Return JSON', {}, true, reason);
    assert.equal(request.model, 'gpt-6-luna');
    if (base.includes('api.openai.com')) {
      assert.equal(request.reasoning_effort, 'low');
      assert.equal(request.max_completion_tokens, 1024);
      assert.ok(!('reasoning' in request));
      assert.ok(!('max_tokens' in request));
    } else {
      assert.equal(request.max_tokens, 1024);
      assert.deepEqual(request.reasoning, reason ? { effort: 'low' } : { enabled: false });
    }
  }
}
console.log('PASS: patched pinned helper request compatibility (4 offline cases)');
