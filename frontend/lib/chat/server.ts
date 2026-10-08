import "server-only";
import type { ChatReply, ChatRequest, ChatStatus } from "./types";
import { MAX_REPLY_LENGTH } from "./types";
import { ChatError, isRecord } from "./validation";
import { createDemoReply } from "./demo";
import { CHAT_INSTRUCTIONS } from "./prompt";

type Environment = Record<string, string | undefined>;
type ProviderOptions = { env?: Environment; fetcher?: typeof fetch; signal?: AbortSignal };

function getConfiguration(env: Environment): ChatStatus & { apiKey?: string; backendUrl?: string } {
  const provider = env.CHAT_PROVIDER?.trim() || "demo";
  if (provider !== "demo" && provider !== "openai" && provider !== "backend") {
    throw new ChatError("챗봇 연결 설정을 확인해 주세요.", 503);
  }
  const model = env.OPENAI_MODEL?.trim() || "gpt-6-luna";
  const apiKey = env.OPENAI_API_KEY?.trim();
  const backendUrl = env.CHAT_BACKEND_URL?.trim();
  const ready = provider === "demo" || (provider === "openai" ? Boolean(apiKey) : Boolean(backendUrl));
  return { provider, model, ready, apiKey, backendUrl };
}

export function getChatStatus(env: Environment = process.env): ChatStatus {
  const { provider, model, ready } = getConfiguration(env);
  return { provider, model, ready };
}

function normalizeReply(value: unknown): string {
  if (typeof value !== "string" || !value.trim() || value.length > MAX_REPLY_LENGTH) {
    throw new ChatError("답변 형식을 확인하지 못했어요. 잠시 후 다시 시도해 주세요.", 502);
  }
  return value.trim();
}

export function extractOpenAIReply(value: unknown): string {
  if (!isRecord(value) || value.status !== "completed" || !Array.isArray(value.output)) {
    throw new ChatError("답변을 끝까지 만들지 못했어요. 질문을 짧게 나누어 다시 시도해 주세요.", 502);
  }
  const parts: string[] = [];
  for (const item of value.output) {
    if (!isRecord(item) || item.type !== "message" || item.role !== "assistant" || !Array.isArray(item.content)) continue;
    for (const content of item.content) {
      if (!isRecord(content)) continue;
      if (content.type === "output_text" && typeof content.text === "string") parts.push(content.text);
      if (content.type === "refusal" && typeof content.refusal === "string") parts.push(content.refusal);
    }
  }
  return normalizeReply(parts.join("\n"));
}

export async function createChatReply(request: ChatRequest, options: ProviderOptions = {}): Promise<ChatReply> {
  const config = getConfiguration(options.env ?? process.env);
  const status: ChatStatus = { provider: config.provider, model: config.model, ready: config.ready };
  if (!config.ready) throw new ChatError("아직 AI 연결이 준비되지 않았어요. 연결 설정 후 다시 시도해 주세요.", 503);
  if (config.provider === "demo") return { ...status, reply: createDemoReply(request) };

  const timeout = AbortSignal.timeout(25000);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  const fetcher = options.fetcher ?? fetch;
  let endpoint: string;
  let payload: unknown;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (config.provider === "openai") {
    endpoint = "https://api.openai.com/v1/responses";
    headers.Authorization = `Bearer ${config.apiKey}`;
    payload = {
      model: config.model,
      instructions: CHAT_INSTRUCTIONS,
      input: [
        ...(request.context ? [{ role: "user", content: `참고 문맥: ${JSON.stringify(request.context)}` }] : []),
        ...request.messages,
      ],
      reasoning: { effort: "low" },
      max_output_tokens: 1200,
      store: false,
    };
  } else {
    try {
      const url = new URL(config.backendUrl!);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Invalid URL");
      endpoint = url.href;
    } catch {
      throw new ChatError("챗봇 서버 주소 설정을 확인해 주세요.", 503);
    }
    payload = request;
  }

  try {
    const response = await fetcher(endpoint, {
      method: "POST", headers, body: JSON.stringify(payload), signal, cache: "no-store", redirect: "error",
    });
    if (!response.ok) {
      if (response.status === 429) throw new ChatError("요청이 많거나 사용 한도에 도달했어요. 잠시 후 다시 시도해 주세요.", 429);
      if (response.status === 401 || response.status === 403) throw new ChatError("AI 연결 권한을 확인해야 해요. 연결 설정을 확인해 주세요.", 503);
      throw new ChatError("챗봇 서버에서 답변을 받지 못했어요. 잠시 후 다시 시도해 주세요.", 502);
    }
    const data: unknown = await response.json();
    const reply = config.provider === "openai" ? extractOpenAIReply(data) : normalizeReply(isRecord(data) ? data.reply : undefined);
    return { ...status, reply };
  } catch (error) {
    if (error instanceof ChatError) throw error;
    if (options.signal?.aborted) throw new ChatError("답변 요청을 중지했어요.", 499);
    if (timeout.aborted) throw new ChatError("응답이 오래 걸리고 있어요. 잠시 후 다시 시도해 주세요.", 504);
    throw new ChatError("챗봇에 연결하지 못했어요. 연결 상태를 확인해 주세요.", 502);
  }
}
