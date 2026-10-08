"use client";

import "driver.js/dist/driver.css";
import type { DriveStep } from "driver.js";
import { useEffect, useRef, useState, type ReactNode, type SyntheticEvent } from "react";
import { createPortal } from "react-dom";
import { GUIDE_COURSE, GUIDE_ORIGIN_SET, GUIDE_ORIGIN_TARGET, type GuideCourseStop, type GuidePoint } from "@/lib/route-guide-events";

const TARGET_STADIUM = "GWANGJU";
const LIST_ID = "route-guide-stadium-list";
const TARGET_ID = "route-guide-stadium-target";
// 가이드용 출발점: 광주-KIA 챔피언스 필드 북서쪽 약 600m (팀에서 지도에 직접 찍어 정한 지점)
const GUIDE_ORIGIN: GuidePoint = { lat: 35.174209, lng: 126.885328 };
const DASHED = "route-guide-dashed";
// 가이드용 예시 코스: 위 출발점에서 팀이 직접 만든 광주 코스
const GUIDE_COURSE_STOPS: GuideCourseStop[] = [
  { name: "출발지", category: "직접 지정", lat: GUIDE_ORIGIN.lat, lng: GUIDE_ORIGIN.lng, isMapPoint: true },
  { name: "33떡볶이 광주운암점", category: "먹거리", lat: 35.1753894746164, lng: 126.8872031546657, placeId: "468710781", address: "전남광주통합특별시 북구 비엔날레로 30" },
  { name: "스타벅스 광주신안DT점", category: "카페·디저트", lat: 35.171594404585306, lng: 126.8948211403407, placeId: "123618728", address: "전남광주통합특별시 북구 서암대로 93" },
  { name: "신용(운암)근린공원", category: "산책", lat: 35.1732649022343, lng: 126.894110939877, placeId: "888419157", address: "전남광주통합특별시 북구 신안동 산 2-3" },
  { name: "광주-KIA 챔피언스 필드", category: "야구장", lat: 35.16820922209541, lng: 126.88911206152956, placeId: "stadium:GWANGJU", address: "전남광주통합특별시 북구 서림로 10" },
];
const CORNER_CLASS = "route-guide-corner";
const GUIDE_CHAT_EXAMPLE = "여자친구랑 분식집 갔다가 카페 방문 후 산책 좀 하고 구장에 가고 싶어";
const GUIDE_COURSE_NAME = "마이 코스";

/**
 * 챗봇 입력창에 예시 문장을 한 글자씩 보여 준다.
 * value만 바꾸고 input 이벤트는 보내지 않아 실제 입력 상태(React)는 그대로이며, 정리할 때 원래 값으로 돌린다.
 */
function typeExample(field: HTMLTextAreaElement | HTMLInputElement, text: string, onDone?: () => void): () => void {
  const original = field.value;
  const originalHeight = field.style.height;
  const chars = Array.from(text);
  let shown = 0;
  let timer = 0;
  const tick = () => {
    shown += 1;
    field.value = chars.slice(0, shown).join("");
    // 실제 입력창처럼 최대 104px까지 높이를 늘린다
    if (field instanceof HTMLTextAreaElement) {
      field.style.height = "auto";
      field.style.height = `${Math.min(field.scrollHeight, 104)}px`;
    }
    field.scrollTop = field.scrollHeight;
    if (field instanceof HTMLInputElement) field.scrollLeft = field.scrollWidth;
    if (shown < chars.length) timer = window.setTimeout(tick, 90);
    else onDone?.();
  };
  timer = window.setTimeout(tick, 400);
  return () => {
    window.clearTimeout(timer);
    field.value = original;
    field.style.height = originalHeight;
  };
}

type PopoverSpot = { top: number; left: number };
const GAP = 16;

/**
 * 말풍선을 계산한 자리(화면 좌표)에 고정한다.
 * driver.js가 스크롤·리사이즈 때 위치를 다시 잡아도 CSS 변수(!important)가 우선한다.
 */
function pinPopover(spot: (popover: HTMLElement) => PopoverSpot): () => void {
  const place = () => {
    const popover = document.querySelector<HTMLElement>(".driver-popover.route-guide-popover");
    if (!popover) return;
    const { top, left } = spot(popover);
    popover.classList.add(CORNER_CLASS);
    popover.style.setProperty("--route-guide-top", `${Math.max(8, Math.min(top, window.innerHeight - popover.offsetHeight - 8))}px`);
    popover.style.setProperty("--route-guide-left", `${Math.max(8, Math.min(left, window.innerWidth - popover.offsetWidth - 8))}px`);
  };
  place();
  window.addEventListener("resize", place);
  window.addEventListener("scroll", place, true);
  return () => {
    window.removeEventListener("resize", place);
    window.removeEventListener("scroll", place, true);
    document.querySelector(".driver-popover")?.classList.remove(CORNER_CLASS);
  };
}

/** 챗봇 패널 왼쪽 바깥, 입력창과 같은 높이 (자리가 없으면 입력창 바로 위) */
function besideComposer(panel: Element, composer: Element) {
  return (popover: HTMLElement): PopoverSpot => {
    const box = composer.getBoundingClientRect();
    const left = panel.getBoundingClientRect().left - popover.offsetWidth - GAP;
    if (left >= 8) return { top: box.top + box.height / 2 - popover.offsetHeight / 2, left };
    return { top: box.top - popover.offsetHeight - GAP, left: box.left };
  };
}

/** 비춘 영역 오른쪽 바깥 위쪽 (오른쪽에 자리가 없으면 영역 바로 위 오른쪽) */
function outsideRight(target: Element) {
  return (popover: HTMLElement): PopoverSpot => {
    const rect = target.getBoundingClientRect();
    const left = rect.right + GAP + 8;
    if (left + popover.offsetWidth <= window.innerWidth - 8) return { top: rect.top, left };
    return { top: rect.top - popover.offsetHeight - GAP - 8, left: rect.right - popover.offsetWidth };
  };
}

const EXIT_ID = "route-guide-exit";
const GUIDE_MASCOT = "/images/guide/guide-mascot.png";
const SAVE_DIALOG_ID = "route-guide-save-dialog";
const SAVE_ICON = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2Z" /><path d="M7 3v6h10V3M7 21v-8h10v8" /></svg>';

/** 코스 저장 후 실제로 뜨는 확인 팝업과 같은 모양의 가이드용 팝업 (눌러도 아무 일도 일어나지 않는다) */
function savedDialog(host: HTMLElement): HTMLElement {
  host.querySelector(`#${SAVE_DIALOG_ID}`)?.remove();
  const dialog = document.createElement("div");
  dialog.id = SAVE_DIALOG_ID;
  dialog.className = "writer-confirm-dialog route-guide-save-dialog";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-label", "코스 후기를 작성하시겠어요?");
  dialog.innerHTML = `<span class="writer-confirm-icon">${SAVE_ICON}</span>`
    + "<h2>코스 후기를 작성하시겠어요?</h2>"
    + "<p>코스가 코스 둘러보기에 저장됐어요. 후기를 추가로 작성할 수 있어요.</p>"
    + '<div class="writer-confirm-actions"><button type="button" class="button button-secondary">아니요</button><button type="button" class="button button-primary">예</button></div>';
  dialog.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); });
  host.appendChild(dialog);
  return dialog;
}

/** 비춘 요소 바로 아래 가운데 */
function below(target: Element) {
  return (popover: HTMLElement): PopoverSpot => {
    const rect = target.getBoundingClientRect();
    return { top: rect.bottom + GAP + 8, left: rect.left + rect.width / 2 - popover.offsetWidth / 2 };
  };
}

// 여러 요소를 한 번에 비추기 위한 투명 영역. 누르면 실제 버튼 대신 이 영역이 클릭을 받는다.
const UNION_ID = "route-guide-union";
function unionTarget(host: HTMLElement, elements: Element[]): HTMLElement {
  host.querySelector(`#${UNION_ID}`)?.remove();
  const rects = elements.map((element) => element.getBoundingClientRect()).filter((rect) => rect.width > 0 && rect.height > 0);
  const top = Math.min(...rects.map((rect) => rect.top));
  const left = Math.min(...rects.map((rect) => rect.left));
  const right = Math.max(...rects.map((rect) => rect.right));
  const bottom = Math.max(...rects.map((rect) => rect.bottom));
  // 샘플 화면(스크롤 영역) 안에 붙여서 스크롤해도 함께 움직이게 한다
  const origin = host.getBoundingClientRect();
  const area = document.createElement("div");
  area.id = UNION_ID;
  Object.assign(area.style, {
    position: "absolute",
    top: `${top - origin.top}px`,
    left: `${left - origin.left}px`,
    width: `${right - left}px`,
    height: `${bottom - top}px`,
    zIndex: "1",
    cursor: "pointer",
  });
  host.appendChild(area);
  return area;
}

const SIDE_POPOVER_ROOM = 390;  // bubble (340px) + gap + highlight padding kept free to the right of the list

// The native <select> popup is drawn by the OS, so a spotlight cannot reach inside it.
// During the guide we show an identical-looking list under the select instead.
function openStadiumList(host: HTMLElement, select: HTMLSelectElement) {
  host.querySelector(`#${LIST_ID}`)?.remove();
  const rect = select.getBoundingClientRect();
  const list = document.createElement("div");
  list.id = LIST_ID;
  list.className = "route-guide-stadium-list";
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", "구장 목록");
  const width = Math.max(rect.width, 240);
  // The select sits near the right edge, so slide the list left when the bubble would not fit beside it.
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - SIDE_POPOVER_ROOM));
  const origin = host.getBoundingClientRect();
  list.style.top = `${rect.bottom - origin.top + 6}px`;
  list.style.left = `${left - origin.left}px`;
  list.style.width = `${width}px`;
  for (const option of Array.from(select.options)) {
    const item = document.createElement("div");
    item.className = "route-guide-stadium-item";
    item.setAttribute("role", "option");
    item.textContent = option.textContent;
    if (option.value === TARGET_STADIUM) {
      item.id = TARGET_ID;
      item.tabIndex = 0;
    } else {
      item.setAttribute("aria-disabled", "true");
    }
    if (option.selected) item.setAttribute("aria-selected", "true");
    list.appendChild(item);
  }
  host.appendChild(list);
}

function closeStadiumList(host: HTMLElement) {
  host.querySelector(`#${LIST_ID}`)?.remove();
}

// Resolves once the planner for the newly chosen stadium is on screen (it remounts after a stadium change).
function waitForPlanner(host: HTMLElement, previous: Element | null, timeoutMs = 15000) {
  return new Promise<void>((resolve) => {
    const started = Date.now();
    const check = () => {
      const current = host.querySelector(".planner-map-canvas");
      if ((current && current !== previous) || Date.now() - started > timeoutMs) resolve();
      else window.setTimeout(check, 120);
    };
    window.setTimeout(check, 120);
  });
}

// Sets a React-controlled <select> the same way a user pick would.
function chooseStadium(select: HTMLSelectElement, value: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
  setValue?.call(select, value);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

/** 샘플 화면이 그려지고 지도가 준비될 때까지 기다린다 */
function waitForSample(host: HTMLElement, timeoutMs = 20000) {
  return new Promise<boolean>((resolve) => {
    const started = Date.now();
    const check = () => {
      if (host.querySelector("#route-stadium") && host.querySelector(".planner-map-canvas")) resolve(true);
      else if (Date.now() - started > timeoutMs) resolve(false);
      else window.setTimeout(check, 120);
    };
    check();
  });
}

/**
 * 스포트라이트 가이드. 실제 작성 화면을 건드리지 않도록, 같은 화면을 샘플 모드로 전체 화면에 띄워 그 위에서 진행한다.
 * host: 샘플 화면의 내용 영역, scroller: 샘플 화면의 스크롤 영역. 반환값은 가이드를 멈추는 함수.
 */
async function runGuide(host: HTMLElement, scroller: HTMLElement, onClose: () => void, includeMemberSteps: boolean): Promise<() => void> {
  const { driver } = await import("driver.js");
  const find = (selector: string) => host.querySelector(selector);
  // driver.js는 문자열 선택자를 문서 전체에서 찾으므로, 항상 샘플 화면 안의 요소를 직접 넘긴다
  const inHost = (selector: string) => () => find(selector) ?? host;
  const select = () => find("#route-stadium") as HTMLSelectElement | null;
  let closed = false;
  let cleanup: (() => void) | undefined;
  const release = () => { cleanup?.(); cleanup = undefined; };
  const finishOrNext = () => { if (tour.isLastStep()) tour.destroy(); else tour.moveNext(); };

  const selectSteps = (steps: DriveStep[]): DriveStep[] => includeMemberSteps
    ? steps
    : steps.filter(step => step.popover?.title !== "코스 저장" && step.popover?.title !== "코스 저장 완료");

  const tour = driver({
    popoverClass: "route-guide-popover",
    overlayColor: "#0b1424",
    overlayOpacity: 0.62,
    stagePadding: 8,
    stageRadius: 14,
    popoverOffset: 16,
    smoothScroll: true,
    // 말풍선의 ×는 쓰지 않고, 화면 오른쪽 위 종료 버튼(과 Esc)으로만 나간다
    allowClose: false,
    // Clicking the dimmed area (e.g. a greyed-out stadium) must not end the guide; use × or Esc.
    overlayClickBehavior: () => undefined,
    showProgress: false,
    nextBtnText: "다음",
    prevBtnText: "이전",
    doneBtnText: "완료",
    steps: selectSteps([
      {
        popover: {
          title: "가이드를 시작합니다",
          description: "나만의 직관 루트를 만드는 방법을<br />한 단계씩 같이 해볼게요.",
          showButtons: ["next"],
          nextBtnText: "시작하기",
        },
      },
      {
        element: inHost("#route-stadium"),
        popover: {
          title: "구장 선택",
          description: "가고 싶은 구장을 먼저 선택하실 수 있어요.",
          side: "top",
          align: "end",
          showButtons: [],
        },
        // Pressing the select opens the guide list instead of the native popup.
        onHighlighted: (element) => {
          const target = element as HTMLSelectElement | undefined;
          if (!target) return;
          const open = (event: Event) => {
            if (event instanceof KeyboardEvent && ![" ", "Enter", "ArrowDown", "ArrowUp"].includes(event.key)) return;
            event.preventDefault();
            release();
            openStadiumList(host, target);
            tour.moveNext();
          };
          target.addEventListener("mousedown", open);
          target.addEventListener("keydown", open);
          cleanup = () => { target.removeEventListener("mousedown", open); target.removeEventListener("keydown", open); };
        },
        onDeselected: release,
      },
      {
        element: inHost(`#${TARGET_ID}`),
        popover: {
          title: "광주-KIA 챔피언스 필드",
          description: "원활한 안내를 위해 챔피언스 필드를 선택해보겠습니다.",
          side: "right",
          align: "center",
          showButtons: [],
        },
        onHighlighted: (element) => {
          if (!element) return;
          let picked = false;
          const pick = async (event: Event) => {
            if (picked || (event instanceof KeyboardEvent && ![" ", "Enter"].includes(event.key))) return;
            picked = true;
            event.preventDefault();
            release();
            const stadium = select();
            const previousMap = find(".planner-map-canvas");
            const changed = Boolean(stadium && stadium.value !== TARGET_STADIUM);
            if (stadium) chooseStadium(stadium, TARGET_STADIUM);
            closeStadiumList(host);
            if (changed) await waitForPlanner(host, previousMap);
            scroller.scrollTo({ top: 0, behavior: "instant" });
            finishOrNext();
          };
          element.addEventListener("click", (event) => void pick(event));
          element.addEventListener("keydown", (event) => void pick(event));
          (element as HTMLElement).focus({ preventScroll: true });
          cleanup = () => { element.remove(); };
        },
        onDeselected: release,
      },
      {
        // 카테고리 줄 전체만 비춘다. 눌러도 실제 필터는 바뀌지 않고(연출) 다음 단계로만 넘어간다.
        element: inHost(".planner-filters"),
        popover: {
          title: "카테고리 선택",
          description: "보고 싶은 카테고리를 선택할 수 있어요.",
          side: "top",
          align: "start",
          showButtons: [],
        },
        onHighlightStarted: () => scroller.scrollTo({ top: 0, behavior: "instant" }),
        onHighlighted: (element) => {
          if (!element) { finishOrNext(); return; }
          const chosen = (event: Event) => {
            if (!(event.target as Element | null)?.closest("button")) return;
            // Swallow the press before it reaches the real button (capture phase).
            event.preventDefault();
            event.stopPropagation();
            release();
            finishOrNext();
          };
          element.addEventListener("click", chosen, true);
          cleanup = () => element.removeEventListener("click", chosen, true);
        },
        onDeselected: release,
      },
      {
        // 카테고리를 뺀 지도 전체를 비추고, 점선으로 표시한 출발점을 눌러야 다음 단계
        element: inHost(".planner-map-stage"),
        popover: {
          title: "출발점 선택",
          description: "챗봇 사용 전 출발점을 선택하셔야 합니다.<br />출발점을 선택해주세요.",
          side: "right",
          align: "start",
          showButtons: [],
        },
        onHighlightStarted: () => {
          window.dispatchEvent(new CustomEvent<GuidePoint | null>(GUIDE_ORIGIN_TARGET, { detail: GUIDE_ORIGIN }));
        },
        onHighlighted: (element) => {
          const done = () => { release(); finishOrNext(); };
          window.addEventListener(GUIDE_ORIGIN_SET, done, { once: true });
          // 지도가 다시 렌더링·스크롤돼도 말풍선이 지도 오른쪽 바깥에 붙어 있게 고정
          const unpin = element ? pinPopover(outsideRight(element)) : undefined;
          cleanup = () => {
            unpin?.();
            window.removeEventListener(GUIDE_ORIGIN_SET, done);
          };
        },
        onDeselected: () => {
          release();
          window.dispatchEvent(new CustomEvent<GuidePoint | null>(GUIDE_ORIGIN_TARGET, { detail: null }));
        },
      },
      {
        // 챗봇 전체를 비추고, 입력창을 점선으로 둘러 안내한다
        element: inHost("#writer-panel-chat"),
        popover: {
          title: "챗봇에게 코스 부탁하기",
          description: "코스 생성을 위한 조건을 입력해주세요",
          side: "left",
          align: "center",
          showButtons: [],
        },
        onHighlightStarted: () => {
          // 모바일에서는 챗봇이 탭 뒤에 있으니 먼저 챗봇 탭을 연다
          const panel = find("#writer-panel-chat") as HTMLElement | null;
          if (panel && !panel.offsetParent) (find("#writer-tab-chat") as HTMLElement | null)?.click();
        },
        onHighlighted: (panel) => {
          const composer = find("#writer-panel-chat .chat-popup-composer")
            ?? find("#writer-panel-chat .chat-popup-composer-area");
          composer?.classList.add(DASHED);
          // 입력창을 마우스로 누르면 실제로 입력되지 않고 예시 코스 단계로 넘어간다
          const swallow = (event: Event) => { event.preventDefault(); event.stopPropagation(); };
          const press = (event: Event) => {
            event.preventDefault();
            event.stopPropagation();
            release();
            finishOrNext();
          };
          composer?.addEventListener("mousedown", press, true);
          const field = host.querySelector<HTMLTextAreaElement>("#writer-chat-question");
          const stopTyping = field ? typeExample(field, GUIDE_CHAT_EXAMPLE) : undefined;
          composer?.addEventListener("click", swallow, true);
          // 입력창이 화면에 보이도록 스크롤하고, 말풍선은 입력창 옆에 둔다
          composer?.scrollIntoView({ behavior: "smooth", block: "nearest" });
          const unpin = panel && composer ? pinPopover(besideComposer(panel, composer)) : undefined;
          cleanup = () => {
            unpin?.();
            stopTyping?.();
            composer?.classList.remove(DASHED);
            composer?.removeEventListener("mousedown", press, true);
            window.setTimeout(() => composer?.removeEventListener("click", swallow, true), 0);
          };
        },
        onDeselected: release,
      },
      {
        // 챗봇은 다시 어둡게, 지도만 밝혀 미리 만든 예시 코스를 보여준다.
        // 코스가 완성되면 지도 영역이 다시 렌더링되므로 지도 위에 투명 영역을 덮어 비추고 클릭을 받는다.
        element: () => unionTarget(host, [...host.querySelectorAll(".planner-map-stage")]),
        popover: {
          title: "예시 코스",
          description: "챗봇이 조건을 분석한 뒤 코스를 생성해줍니다.<br />원활한 가이드 진행을 위해 예시 코스를 생성했습니다.",
          side: "right",
          align: "start",
          showButtons: [],
        },
        onHighlightStarted: () => {
          window.dispatchEvent(new CustomEvent<GuideCourseStop[]>(GUIDE_COURSE, { detail: GUIDE_COURSE_STOPS }));
        },
        onHighlighted: (element) => {
          if (!element) return;
          // 밝아진 지도 아무 곳이나 누르면 다음 단계
          const next = (event: Event) => { event.preventDefault(); event.stopPropagation(); release(); finishOrNext(); };
          element.addEventListener("click", next, { once: true });
          const unpin = pinPopover(outsideRight(element));
          cleanup = () => {
            unpin();
            element.removeEventListener("click", next);
          };
        },
        // 다음 단계가 같은 id로 새 영역을 이미 만들었을 수 있으니, 이 단계의 영역만 지운다
        onDeselected: (element) => {
          release();
          if (element?.id === UNION_ID) element.remove();
        },
      },
      {
        // 오른쪽 '내 코스' 패널과 코스 수정·초기화 버튼을 함께 비춘다. 비춘 곳 아무 데나 누르면 다음
        element: () => {
          const routeTab = find("#nearby-route-tab") as HTMLElement | null;
          if (routeTab?.getAttribute("aria-selected") !== "true") routeTab?.click();
          const parts = [
            find(".planner-side"),
            ...host.querySelectorAll(".planner-drawing-toolbar button"),
          ].filter((part): part is Element => Boolean(part));
          return unionTarget(host, parts);
        },
        popover: {
          title: "내 코스",
          description: "내 코스의 상세 정보를 볼 수 있어요",
          side: "right",
          align: "center",
          showButtons: [],
        },
        onHighlighted: (element) => {
          if (!element) return;
          const next = (event: Event) => { event.preventDefault(); event.stopPropagation(); release(); finishOrNext(); };
          element.addEventListener("click", next, { once: true });
          cleanup = () => element.removeEventListener("click", next);
        },
        // 다음 단계가 같은 id로 새 영역을 이미 만들었을 수 있으니, 이 단계의 영역만 지운다
        onDeselected: (element) => {
          release();
          if (element?.id === UNION_ID) element.remove();
        },
      },
      {
        // 코스 수정 버튼만 비춘다. 눌러도 실제로 잠금이 풀리지는 않고 다음 단계로 넘어간다
        element: inHost(".planner-course-toggle"),
        popover: {
          title: "코스 수정",
          description: "해당 코스는 수정이 가능합니다",
          side: "bottom",
          align: "center",
          showButtons: [],
        },
        onHighlighted: (element) => {
          if (!element) return;
          const next = (event: Event) => { event.preventDefault(); event.stopPropagation(); release(); finishOrNext(); };
          element.addEventListener("click", next, true);
          const unpin = pinPopover(below(element));
          cleanup = () => {
            unpin();
            element.removeEventListener("click", next, true);
          };
        },
        onDeselected: release,
      },
      {
        // 코스 저장 영역을 비추고, 코스 이름 입력칸에 예시 이름을 한 글자씩 보여 준다
        element: inHost(".course-save"),
        popover: {
          title: "코스 저장",
          description: "코스가 확정되면 코스를 저장할 수 있습니다",
          side: "right",
          align: "center",
          showButtons: [],
        },
        onHighlighted: (element) => {
          const field = host.querySelector<HTMLInputElement>("#planner-course-name");
          if (!element || !field) return;
          const saveButton = element.querySelector<HTMLButtonElement>(".course-save-button");
          const wasDisabled = saveButton?.disabled ?? true;
          field.classList.add(DASHED);
          // 입력칸은 눌러도 실제로 입력되지 않는다
          const swallow = (event: Event) => { event.preventDefault(); event.stopPropagation(); };
          // 예시 이름이 다 써지면 코스 저장 버튼이 파랗게 켜지고, 누르면 (실제 저장 없이) 다음 단계
          const save = (event: Event) => { swallow(event); release(); finishOrNext(); };
          const stopTyping = typeExample(field, GUIDE_COURSE_NAME, () => {
            if (!saveButton) return;
            saveButton.disabled = false;
            saveButton.addEventListener("click", save, true);
          });
          field.addEventListener("mousedown", swallow, true);
          field.addEventListener("click", swallow, true);
          cleanup = () => {
            stopTyping();
            field.classList.remove(DASHED);
            field.removeEventListener("mousedown", swallow, true);
            window.setTimeout(() => field.removeEventListener("click", swallow, true), 0);
            if (saveButton) {
              saveButton.removeEventListener("click", save, true);
              saveButton.disabled = wasDisabled;
            }
          };
        },
        onDeselected: release,
      },
      {
        // 코스 저장을 누른 것처럼 저장 확인 팝업을 띄우고 팝업만 밝힌다
        element: () => savedDialog(host),
        popover: {
          title: "코스 저장 완료",
          description: "코스가 저장되었습니다.<br />해당 코스를 다른 사람들과 공유할 수 있어요",
          side: "right",
          align: "center",
          showButtons: [],
        },
        onHighlighted: (element) => {
          if (!element) return;
          // 밝아진 팝업 아무 곳이나 누르면 다음 단계
          const next = () => { release(); finishOrNext(); };
          element.addEventListener("click", next, { once: true });
          cleanup = () => element.removeEventListener("click", next);
        },
        onDeselected: (element) => {
          release();
          if (element?.id === SAVE_DIALOG_ID) element.remove();
        },
      },
      {
        // 모두 어둡게, 가운데 말풍선만
        popover: {
          popoverClass: "route-guide-popover route-guide-final",
          title: "가이드를 마쳤어요",
          description: includeMemberSteps ? "나만의 코스를 공유하고 다른 사람들의 코스도 구경해보세요" : "지도에서 나만의 코스를 구성해보세요. 이야기 작성과 코스 저장은 로그인 후 이용할 수 있어요.",
          showButtons: ["next"],
          doneBtnText: "가이드 종료",
          // 캐릭터가 말풍선 오른쪽에 서서 팔을 말풍선에 걸친다
          onPopoverRender: (popover) => {
            const mascot = document.createElement("img");
            mascot.className = "route-guide-mascot";
            mascot.src = GUIDE_MASCOT;
            mascot.alt = "";
            popover.wrapper.appendChild(mascot);
          },
        },
      },
    ]),
    onDestroyed: () => {
      release();
      closed = true;
      closeStadiumList(host);
      find(`#${UNION_ID}`)?.remove();
      find(`#${SAVE_DIALOG_ID}`)?.remove();
      document.getElementById(EXIT_ID)?.remove();
      window.removeEventListener("keydown", escape, true);
      unlockScroll();
      window.dispatchEvent(new CustomEvent<GuidePoint | null>(GUIDE_ORIGIN_TARGET, { detail: null }));
      // 샘플 화면만 닫는다. 뒤의 실제 작성 화면은 처음 그대로다.
      onClose();
    },
  });

  // 가이드 진행 중 화면 오른쪽 위의 종료 버튼
  document.getElementById(EXIT_ID)?.remove();
  const exit = document.createElement("button");
  exit.type = "button";
  exit.id = EXIT_ID;
  exit.setAttribute("aria-label", "가이드 종료");
  exit.title = "가이드 종료";
  exit.textContent = "×";
  exit.addEventListener("click", () => tour.destroy());
  document.body.appendChild(exit);
  const escape = (event: KeyboardEvent) => { if (event.key === "Escape") tour.destroy(); };
  window.addEventListener("keydown", escape, true);

  // 가이드 중에는 휠·터치·휠 클릭(자동 스크롤)·키보드로 화면을 옮길 수 없다. 이동은 가이드가 직접 한다.
  scroller.classList.add("is-guiding");
  const blockScroll = (event: Event) => { event.preventDefault(); event.stopPropagation(); };
  const blockMiddle = (event: MouseEvent) => { if (event.button === 1) blockScroll(event); };
  const scrollKeys = new Set([" ", "PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);
  const blockKeys = (event: KeyboardEvent) => {
    const typing = (event.target as Element | null)?.closest("input, textarea, select, [contenteditable='true']");
    if (!typing && scrollKeys.has(event.key)) event.preventDefault();
  };
  const lock = { capture: true, passive: false } as const;
  window.addEventListener("wheel", blockScroll, lock);
  window.addEventListener("touchmove", blockScroll, lock);
  window.addEventListener("mousedown", blockMiddle, lock);
  window.addEventListener("auxclick", blockMiddle, lock);
  window.addEventListener("keydown", blockKeys, true);
  // 가이드가 샘플 화면을 스크롤하면(창 높이가 낮을 때) 밝힌 영역 위치를 다시 맞춘다
  let refreshFrame = 0;
  const followScroll = () => {
    cancelAnimationFrame(refreshFrame);
    refreshFrame = requestAnimationFrame(() => { if (tour.isActive()) tour.refresh(); });
  };
  scroller.addEventListener("scroll", followScroll, { passive: true });
  const unlockScroll = () => {
    cancelAnimationFrame(refreshFrame);
    scroller.removeEventListener("scroll", followScroll);
    scroller.classList.remove("is-guiding");
    window.removeEventListener("wheel", blockScroll, lock);
    window.removeEventListener("touchmove", blockScroll, lock);
    window.removeEventListener("mousedown", blockMiddle, lock);
    window.removeEventListener("auxclick", blockMiddle, lock);
    window.removeEventListener("keydown", blockKeys, true);
  };

  tour.drive();
  return () => { if (!closed) tour.destroy(); };
}

type RouteGuideButtonProps = {
  /** 가이드용 샘플 화면 (같은 작성 화면을 샘플 모드로). 없으면 버튼은 모양만 보인다. */
  renderSample?: () => ReactNode;
  includeMemberSteps?: boolean;
};

// Spotlight onboarding for the route writer (/routes/new). Only starts when the user presses "가이드 시작".
export function RouteGuideButton({ renderSample, includeMemberSteps = false }: RouteGuideButtonProps) {
  const [open, setOpen] = useState(false);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const scroller = scrollerRef.current;
    const host = hostRef.current;
    if (!open || !scroller || !host) return;
    let cancelled = false;
    let stop: (() => void) | undefined;
    const close = () => { cancelled = true; setOpen(false); };
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    void waitForSample(host).then(async (ready) => {
      if (cancelled) return;
      if (!ready) { close(); return; }
      const halt = await runGuide(host, scroller, close, includeMemberSteps);
      if (cancelled) halt(); else stop = halt;
    });
    return () => {
      cancelled = true;
      stop?.();
      document.body.style.overflow = overflow;
    };
  }, [open, includeMemberSteps]);

  const stop = (event: SyntheticEvent) => event.stopPropagation();
  return <>
    <button type="button" className="writer-guide-button" onClick={() => { if (renderSample) setOpen(true); }}>
      <span aria-hidden="true">✦</span>가이드 시작
    </button>
    {open && renderSample && createPortal(
      // 포털 안의 React 이벤트가 실제 작성 화면 쪽 부모로 전달되지 않게 막는다
      <div ref={scrollerRef} className="route-guide-sandbox" aria-label="루트 만들기 가이드 (샘플 화면)"
        onClick={stop} onSubmit={stop} onChange={stop} onInput={stop} onKeyDown={stop} onMouseDown={stop} onPointerDown={stop}>
        <div ref={hostRef} className="route-guide-sandbox-content">{renderSample()}</div>
      </div>,
      document.body,
    )}
  </>;
}
