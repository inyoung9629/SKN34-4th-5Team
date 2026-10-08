import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

const home = readFileSync(new URL("../components/home-page.tsx", import.meta.url), "utf8");

test("guest and member share the v2 hero question form", () => {
  assert.match(home, /const canOpenChat = authStatus === "anonymous" \|\| authStatus === "authenticated"/);
  assert.match(home, /if \(canOpenChat && !composingRef.current\) openChat\(question\)/);
  assert.ok(home.includes('className="hero-chat-ai">AI'));
  assert.ok(!home.includes('authStatus !== "authenticated" ? <Link'));
});

test("member hero retains its existing question submission", () => {
  assert.ok(home.includes('if (canOpenChat && !composingRef.current) openChat(question)'));
  assert.ok(home.includes("maxLength={MAX_MESSAGE_LENGTH}"));
});
