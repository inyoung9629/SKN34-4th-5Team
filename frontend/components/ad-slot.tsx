"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { AD_MODE, deliveryLifetime, safeAdUrl, type AdDelivery, type AdPlacement } from "@/lib/ads";
import { ClubAdCarousel, SupplierAdCarousel } from "./club-ad-carousel";
import { loadHomeAd, trackHomeAd } from "@/lib/api/ads";
import { observeAdImpression } from "@/lib/ad-visibility";
import styles from "./ad-slot.module.css";

export function AdSlot({ placement, routeIds = [] }: { placement: AdPlacement; routeIds?: string[] }) {
  if (AD_MODE === "mock" && placement === "home-club-banner") return <ClubAdCarousel />;
  if (AD_MODE === "mock" && placement === "home-route-partner-banner") return <SupplierAdCarousel />;
  const routeKey = JSON.stringify([...new Set(routeIds)].sort().slice(0, 3));
  return <AdLoader key={`${placement}:${routeKey}`} placement={placement} routeKey={routeKey} />;
}

function AdLoader({ placement, routeKey }: { placement: AdPlacement; routeKey: string }) {
  const [result, setResult] = useState<{ delivery: AdDelivery; receivedAt: number } | null>(null);
  useEffect(() => {
    const controller = new AbortController(), requestedAt = performance.now();
    void loadHomeAd({ placement, routeIds: JSON.parse(routeKey) as string[] }, AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]))
      .then(delivery => {
        // 통신 시간도 차감하여 보수적으로 만료 시각을 계산한다.
        if (!controller.signal.aborted && delivery) setResult({ delivery, receivedAt: requestedAt });
      })
      .catch(() => { /* 광고 실패는 다른 홈 콘텐츠에 영향을 주지 않는다. */ });
    return () => controller.abort();
  }, [placement, routeKey]);
  return result ? <AdBanner {...result} /> : null;
}

function AdBanner({ delivery, receivedAt }: { delivery: AdDelivery; receivedAt: number }) {
  const ad = delivery.ad;
  const root = useRef<HTMLElement | null>(null);
  const impressionSent = useRef(false), clickSent = useRef(false);
  const [failed, setFailed] = useState(false), [expired, setExpired] = useState(false);
  const [imageReady, setImageReady] = useState(!ad?.image_url);
  const lifetime = deliveryLifetime(delivery);
  const href = ad ? safeAdUrl(ad.destination_url) : null;
  const image = ad?.image_url ? safeAdUrl(ad.image_url) : null;
  const hidden = failed || expired || !ad || !href || Boolean(ad.image_url && !image);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const check = () => {
      const remaining = lifetime - (performance.now() - receivedAt);
      if (remaining <= 0) { setExpired(true); return; }
      timer = setTimeout(check, Math.min(remaining, 60000));
    };
    // 처음부터 만료된 응답도 effect의 동기 setState 없이 처리한다.
    timer = setTimeout(check, 0);
    return () => clearTimeout(timer);
  }, [lifetime, receivedAt]);

  useEffect(() => {
    if (imageReady) return;
    const timer = setTimeout(() => setFailed(true), 15000);
    return () => clearTimeout(timer);
  }, [imageReady]);

  useEffect(() => {
    if (!root.current || hidden || !imageReady || impressionSent.current) return;
    return observeAdImpression(root.current, () => {
      if (performance.now() - receivedAt >= lifetime || impressionSent.current) return;
      impressionSent.current = true;
      void trackHomeAd(delivery, "impression").catch(() => {});
    });
  }, [delivery, hidden, imageReady, lifetime, receivedAt]);

  if (hidden || !ad || !href) return null;
  const club = ad.placement === "home-club-banner", external = href.startsWith("https:");
  return <aside ref={root} className={`container ${styles.slot}`} aria-label={club ? "구단 광고" : "음식점·시설 광고"}>
    <a href={href} className={`${styles.banner} ${club ? styles.club : styles.partner}`} target={external ? "_blank" : undefined} rel={external ? "sponsored noopener noreferrer" : "sponsored"} onClick={event => {
      if (performance.now() - receivedAt >= lifetime) { event.preventDefault(); setExpired(true); return; }
      if (!clickSent.current) { clickSent.current = true; void trackHomeAd(delivery, "click").catch(() => {}); }
    }}>
      <span className={styles.visual}>
        {image ? <Image src={image} alt={ad.image_alt} fill unoptimized loading="eager" sizes="(max-width: 640px) 76px, 220px" className={styles.image} onLoad={() => setImageReady(true)} onError={() => setFailed(true)} /> : <span className={styles.placeholder} aria-hidden="true">{club ? "CLUB" : "LOCAL"}<small>AD</small></span>}
      </span>
      <span className={styles.content}>
        <span className={styles.meta}><span className={styles.label}>광고</span>{delivery.preview && <span>목업 미리보기</span>}<span>{ad.context_label}</span></span>
        <strong className={styles.title}>{ad.title}</strong><span className={styles.description}>{ad.description}</span><span className={styles.advertiser}>{ad.advertiser}</span>
      </span>
      <span className={styles.cta}>{ad.button_label}<span aria-hidden="true"> ↗</span>{external && <span className="sr-only"> — 새 창에서 열기</span>}</span>
    </a>
  </aside>;
}
