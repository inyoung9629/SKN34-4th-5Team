import type { ChatTimelineItem, ChatToolCall, ChatToolStatus } from "@/lib/chat/types";
import { ChatAnswer } from "./chat-answer";
import "@/styles/chat-progress.css";

const STATUS: Record<ChatToolStatus, { mark: string; label: string }> = {
  running: { mark: "…", label: "조회 중" },
  completed: { mark: "✓", label: "정보 조회 완료" },
  failed: { mark: "!", label: "조회 실패" },
};
// serializer/message.py 가 넘겨주는 tool_name 은 LangChain 도구 함수 이름이라 화면에 그대로 쓰기 어렵다.
const TOOL_LABELS: Record<string, string> = {
  search_places: "장소 검색",
  search_courses: "코스 검색",
  get_course: "코스 조회",
  get_directions: "경로 검색",
  search_tourism: "관광지 검색",
  get_weather: "날씨 조회",
  search_community_posts: "커뮤니티 검색",
  get_prediction_games: "승부예측 조회",
  get_stadium: "구장 정보 조회",
  get_seat_zones: "좌석 구역 조회",
  get_seat_views: "좌석 시야 조회",
  get_ticket_prices: "티켓 가격 조회",
  get_ticket_policies: "예매 정책 조회",
  get_transport: "교통 정보 조회",
  get_food_stores: "매점 정보 조회",
  get_facilities: "편의시설 조회",
  get_stadium_contents: "구장 콘텐츠 조회",
  get_seat_maps: "좌석도 조회",
  get_baseball_schema: "야구 데이터 스키마 조회",
  execute_baseball_select: "야구 기록 조회",
  search_documents_tool: "규칙·안내 문서 검색",
  ask_baseball: "야구 정보 확인",
  ask_travel_research: "여행 정보 조사",
  ask_web_research: "웹 정보 조사",
  jev_browse: "웹 근거 확인",
  jev_read_body: "웹 원문 확인",
  ask_place_data: "장소 정보 확인",
  ask_course: "코스 생성·수정",
  plan_course: "코스 검증",
};

const plain = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);

// Superuser-only: the backend sends detail only to superusers, so its presence is the permission signal.
function ToolDetail({ detail }: { detail: NonNullable<ChatToolCall["detail"]> }) {
  return (
    <details className="chat-progress-detail">
      <summary>상세</summary>
      <pre>{`args: ${plain(detail.args)}\nresult: ${detail.result}`}</pre>
      {detail.messages?.map((message, index) => <pre key={index}>{`[${plain(message.role)}] ${plain(message.content)}`}</pre>)}
    </details>
  );
}

// `items` is this level's run, `all` the whole turn: a sub-agent's inner lines may arrive after later main text.
function Lines({ items, all, parentId, live }: { items: ChatTimelineItem[]; all: ChatTimelineItem[]; parentId: string | null; live?: boolean }) {
  return items.flatMap((item, index) => {
    if (item.kind === "text") return item.parentId === parentId ? [<li key={index} className="chat-progress-text"><span aria-hidden="true" /><span>{item.text}</span></li>] : [];
    return item.tools.filter(tool => tool.parentId === parentId && !(live && isComposerStatus(tool))).map(tool => {
      const status = STATUS[tool.status];
      const label = TOOL_LABELS[tool.toolName] ?? "정보 조회";
      const name = tool.kind === "sub_agent" ? `${label} (서브에이전트)` : label;
      const children = tool.kind === "sub_agent" ? Lines({ items: all, all, parentId: tool.id, live }) : [];
      const line = <><span aria-hidden="true">{status.mark}</span><span>{name} {status.label}</span></>;
      // title/detail/하위 텍스트는 superuser 에게만 온다(backend 투영). 과정은 기본 닫힌 details 로 접는다.
      if (!children.length && !tool.detail && !tool.title && !tool.summary) return <li key={tool.id} className={`is-${tool.status}`}>{line}</li>;
      return (
        <li key={tool.id} className={`is-${tool.status}`}>
          <details className="chat-progress-disclosure">
            <summary>{line}</summary>
            {tool.summary && <p className="chat-progress-title">{tool.summary}</p>}
            {tool.title && <p className="chat-progress-title">{tool.title}</p>}
            {tool.detail && <ToolDetail detail={tool.detail} />}
            {children.length > 0 && <ul className="chat-progress-nested">{children}</ul>}
          </details>
        </li>
      );
    });
  });
}

// 실행 중인 메인 직속 서브에이전트는 본문 대신 입력창 위(ChatSubAgentStatus)에만 보인다. 끝나면 본문 접힌 기록으로 옮겨진다.
const isComposerStatus = (tool: ChatToolCall) => tool.kind === "sub_agent" && tool.parentId === null && tool.status === "running";

/** Running sub-agents shown just above the composer: 담당 라벨 + 공개 summary. Pass [] when not busy. */
export function ChatSubAgentStatus({ items }: { items: ChatTimelineItem[] }) {
  const running = items.flatMap(item => item.kind === "tools" ? item.tools.filter(isComposerStatus) : []);
  if (!running.length) return null;
  return (
    <ul className="chat-subagent-status" role="status" aria-label="실행 중인 서브에이전트" aria-live="polite">
      {running.map(tool => (
        <li key={tool.id}><span aria-hidden="true">…</span><span>{TOOL_LABELS[tool.toolName] ?? "정보 조회"}{tool.summary ? ` · ${tool.summary}` : ""} 조회 중</span></li>
      ))}
    </ul>
  );
}

/** Turn process log in arrival order: main text inline, tool lines grouped, sub-agent inner lines nested by parentId.
 * `live`: 진행 중 턴만 실행 중 서브에이전트를 입력창 위로 넘긴다. 저장된(중단·실패) 기록은 미해결 줄도 그대로 보인다. */
export function ChatProgress({ items, live }: { items: ChatTimelineItem[]; live?: boolean }) {
  if (!items.length) return null;
  // Top-level runs: main text renders as an answer paragraph, consecutive main tools as one log list.
  const runs: ChatTimelineItem[][] = [];
  for (const item of items) {
    if (item.kind === "text" ? item.parentId !== null : item.tools[0].parentId !== null || (live && item.tools.every(isComposerStatus))) continue;
    if (item.kind === "tools" && runs.at(-1)?.[0].kind === "tools") runs.at(-1)!.push(item);
    else runs.push([item]);
  }
  return runs.map((run, index) => {
    const head = run[0];
    if (head.kind === "text") return <ChatAnswer key={index} text={head.text} />;
    return (
      <ul key={index} className="chat-progress" role="status" aria-label="도구 호출 로그" aria-live="polite" aria-atomic="false">
        <Lines items={run} all={items} parentId={null} live={live} />
      </ul>
    );
  });
}
