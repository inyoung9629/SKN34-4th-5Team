"use client";

import { Icon } from "./icons";
import { useEffect, useState } from "react";
import { fetchChatUsage, type ChatMode, type ChatUsageDto } from "@/lib/chat/client";

// Mounted only in settings, keyed by auth identity so another account never sees old usage.
export function ChatUsage({ mode, refreshKey }: { mode: ChatMode | null; refreshKey: unknown }) {
  const [usage, setUsage] = useState<ChatUsageDto | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (!mode) return;
    const controller = new AbortController();
    void Promise.resolve().then(() => {
      if (controller.signal.aborted) return;
      setLoading(true);
      setError("");
      setUsage(null);
      return fetchChatUsage(mode, controller.signal)
        .then(value => { if (!controller.signal.aborted) setUsage(value); })
        .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "사용량을 불러오지 못했어요."); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    });
    return () => controller.abort();
  }, [mode, refreshKey, retry]);

  const remainingPercent = usage && usage.limit_tokens > 0 ? Math.min(100, Math.max(0, usage.remaining_tokens * 100 / usage.limit_tokens)) : 0;
  const refreshLabel = loading ? "사용량 확인 중" : error ? "사용량 다시 불러오기" : "사용량 새로고침";
  const balanceDetail = usage ? `남은 양 ${usage.remaining_credits} 크레딧 · ${usage.remaining_tokens.toLocaleString()}토큰 / 제공량 ${usage.limit_tokens.toLocaleString()}토큰 · 사용 ${usage.used_tokens.toLocaleString()}토큰 · 진행 중 예약 ${usage.reserved_tokens.toLocaleString()}토큰 (예약된 양은 현재 사용할 수 없어요) · ${usage.tokens_per_credit.toLocaleString()}토큰 = 1크레딧` : "";

  return (
    <section className="workspace-usage-panel" aria-labelledby="workspace-usage-title" aria-busy={Boolean(mode) && loading}>
      <div className="workspace-usage-heading">
        <h3 id="workspace-usage-title">사용량</h3>
        {mode && <button type="button" className="workspace-icon-button" aria-label={refreshLabel} title={refreshLabel} onClick={() => setRetry(value => value + 1)} disabled={loading}><Icon name="refresh" size={19} /></button>}
      </div>
      {!mode ? <p role="status">계정 확인 후 사용량을 확인할 수 있어요.</p> : <>
        {loading && <p className="workspace-usage-status" role="status">사용량 확인 중</p>}
        {error && <p role="alert">{error} 새로고침 버튼으로 다시 확인해 주세요.</p>}
        {!loading && !error && usage && <>
          <div className="workspace-usage-label"><span>남은 제공량</span><strong>{Math.floor(remainingPercent)}%</strong></div>
          <meter className="workspace-usage-meter" min={0} max={100} value={remainingPercent} aria-label="남은 제공량" aria-valuetext={balanceDetail}>{remainingPercent}%</meter>
          <p className="workspace-usage-policy">{usage.resets_at ? `다음 충전: ${new Date(usage.resets_at).toLocaleDateString("ko-KR", { timeZone: usage.timezone || "Asia/Seoul" })}` : "비회원 제공량은 다시 채워지지 않아요."}</p>
          {!usage.can_send && <p role="status">{usage.reserved_tokens > 0 ? "현재 사용 가능한 제공량이 없어요. 진행 중인 답변이 끝나면 다시 확인해 주세요." : "사용 가능한 제공량이 없어요."}</p>}
        </>}
      </>}
    </section>
  );
}
