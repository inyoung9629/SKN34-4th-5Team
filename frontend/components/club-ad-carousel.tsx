"use client";

import { useEffect, useRef, useState } from "react";
import { clubAds, clubInstagramUrl, startClubRotation } from "@/lib/club-ads";
import { supplierAds, nextAdIndex, randomAdIndex } from "@/lib/supplier-ads";
import styles from "./club-ad-carousel.module.css";

type BannerSlide = { code: string; name: string; href: string; image: string; alt: string };
const clubSlides: BannerSlide[] = clubAds.map((club, index) => ({
  code: club.code, name: club.name, href: clubInstagramUrl(index),
  image: `/images/ads/clubs/${club.code.toLowerCase()}`,
  alt: `${club.name} · 경기 밖의 순간까지, 함께. 공식 인스타그램에서 만나보세요.`,
}));
const supplierSlides: BannerSlide[] = supplierAds.map(ad => ({
  code: ad.id, name: ad.name, href: ad.href, image: `/images/ads/suppliers/${ad.id}`,
  alt: `${ad.name} · ${ad.product} · ${ad.teams.join(", ")} 용품 후원 브랜드 목업 광고`,
}));

export function ClubAdCarousel() {
  return <ImageAdCarousel slides={clubSlides} label="구단 광고" destination="공식 인스타그램" />;
}
export function SupplierAdCarousel() {
  return <ImageAdCarousel slides={supplierSlides} label="유니폼·용품 광고" destination="공식 판매 페이지" />;
}

// Both placements share navigation, timers, pause behavior, and accessibility.
function ImageAdCarousel({ slides, label, destination }: { slides: readonly BannerSlide[]; label: string; destination: string }) {
  const [index, setIndex] = useState<number | null>(null);
  const [paused, setPaused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [visible, setVisible] = useState(true);
  const [inView, setInView] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    // Randomize only after hydration, once per mount; do not persist to storage.
    const frame = requestAnimationFrame(() => setIndex(randomAdIndex(slides.length)));
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const syncMotion = () => setReducedMotion(motion.matches);
    const syncVisible = () => setVisible(document.visibilityState === "visible");
    syncMotion();
    syncVisible();
    motion.addEventListener("change", syncMotion);
    document.addEventListener("visibilitychange", syncVisible);
    const observer = new IntersectionObserver(entries => {
      setInView(entries.some(entry => entry.isIntersecting));
    });
    if (root.current) observer.observe(root.current);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      motion.removeEventListener("change", syncMotion);
      document.removeEventListener("visibilitychange", syncVisible);
    };
  }, [slides.length]);

  const rotating = index !== null && !paused && !hovered && !focused && visible && inView && !reducedMotion;
  useEffect(() => {
    if (!rotating) return;
    return startClubRotation(() => setIndex(current => current === null ? current : nextAdIndex(current, slides.length)));
  }, [rotating, index, slides.length]);

  const move = (direction: number) => setIndex(current => nextAdIndex(current ?? 0, slides.length, direction));
  const club = index === null ? null : slides[index];

  return <aside ref={root} className={`container ${styles.slot}`} aria-label={label} aria-roledescription="캐러셀"
    onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
    onFocusCapture={() => setFocused(true)}
    onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false); }}
    onKeyDown={event => {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        move(event.key === "ArrowLeft" ? -1 : 1);
      }
    }}>
    <div className={styles.frame}>
      {club && index !== null ? <a className={styles.link} href={club.href} target="_blank" rel="sponsored noopener noreferrer"
        aria-label={`${club.name} ${destination} — 새 탭에서 열기`}>
        <picture key={club.code}>
          <source media="(max-width: 640px)" srcSet={`${club.image}-mobile.svg`} />
          {/* Static self-contained SVG artwork; picture selects the mobile composition. */}
          <img className={styles.artwork} src={`${club.image}.svg`}
            alt={club.alt} width={1200} height={400} />
        </picture>
      </a> : <div className={styles.placeholder} role="status"><span className="sr-only">{label} 준비 중</span></div>}
      <button className={`${styles.arrow} ${styles.previous}`} type="button" aria-label={`이전 ${label}`} onClick={() => move(-1)} disabled={!club}>‹</button>
      <button className={`${styles.arrow} ${styles.next}`} type="button" aria-label={`다음 ${label}`} onClick={() => move(1)} disabled={!club}>›</button>
      <div className={styles.controls}>
        <span className={styles.disclosure}>광고 · 목업</span>
        <button type="button" onClick={() => setPaused(value => !value)} disabled={reducedMotion}
          aria-label={reducedMotion ? "동작 줄이기 설정으로 자동 전환 중지" : paused ? "광고 자동 전환 재생" : "광고 자동 전환 일시정지"}>
          {paused || reducedMotion ? "▶" : "Ⅱ"}
        </button>
        <span className={styles.counter} aria-live="off" aria-label={club ? `${index! + 1} / ${slides.length}, ${club.name}` : "광고 준비 중"}>
          <strong>{index === null ? "—" : index + 1}</strong><span>/ {slides.length}</span>
        </span>
      </div>
    </div>
  </aside>;
}
