import { readFileSync, writeFileSync } from 'node:fs';

const path = '/opt/jev/src/model/text.ts';
let source = readFileSync(path, 'utf8');
const before = `  const reasoning = base.includes("api.deepseek.com/")
    ? { thinking: { type: "disabled" } }
    : { reasoning: reason ? { effort: "low" } : { enabled: false } };`;
const after = `  const officialOpenAI = new URL(base).hostname === "api.openai.com";
  const reasoning = officialOpenAI
    ? { reasoning_effort: "low" }
    : base.includes("api.deepseek.com/")
      ? { thinking: { type: "disabled" } }
      : { reasoning: reason ? { effort: "low" } : { enabled: false } };`;
for (const [oldText, newText] of [[before, after], ['    max_tokens: 1024,', '    ...(officialOpenAI ? { max_completion_tokens: 1024 } : { max_tokens: 1024 }),']]) {
  if (source.split(oldText).length !== 2) throw new Error('Pinned helper patch context changed');
  source = source.replace(oldText, newText);
}
writeFileSync(path, source);
