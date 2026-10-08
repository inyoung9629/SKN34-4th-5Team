/** 50% 이상이 보이는 상태가 포그라운드에서 1초간 유지될 때 한 번 알린다. */
export function observeAdImpression(element: Element, onVisible: () => void): () => void {
  if (typeof IntersectionObserver === "undefined") return () => {};
  let visible = false, sent = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const clear = () => { if (timer !== undefined) clearTimeout(timer); timer = undefined; };
  const update = () => {
    clear();
    if (!visible || document.visibilityState !== "visible" || sent) return;
    timer = setTimeout(() => {
      if (!visible || document.visibilityState !== "visible" || sent) return;
      sent = true;
      onVisible();
    }, 1000);
  };
  const observer = new IntersectionObserver(entries => {
    const entry = entries[0];
    visible = Boolean(entry?.isIntersecting && entry.intersectionRatio >= 0.5);
    update();
  }, { threshold: [0, 0.5, 1] });
  observer.observe(element);
  document.addEventListener("visibilitychange", update);
  return () => { clear(); observer.disconnect(); document.removeEventListener("visibilitychange", update); };
}
