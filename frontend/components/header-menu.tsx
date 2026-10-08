"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { useChat } from "./chat-provider";
import { CapBot, Icon } from "./icons";
import { useMemberAuth } from "@/lib/member-auth";

const groups = [
  {
    title: "경기", icon: "stadium",
    items: [
      { title: "구장 정보", href: "/stadiums" },
      { title: "경기 일정", href: "/schedule" },
      { title: "순위", href: "/standings" },
      { title: "KBO 리그 H/L", href: "/highlights" },
      { title: "야구 가이드", href: "/guide" },
    ],
  },
  {
    title: "직관 코스 짜기", icon: "route", image: "/images/icons/route-create.png",
    items: [
      { title: "코스 만들기", href: "/routes/new" },
      { title: "코스 둘러보기", href: "/routes" },
    ],
  },
  {
    title: "커뮤니티", icon: "chat",
    items: [
      { title: "자유 게시판", href: "/community" },
      { title: "팀 게시판", href: "/community/teams" },
      { title: "승부 예측", href: "/community/predictions" },
    ],
  },
] as const;

function HeaderMenuContent({ pathname }: { pathname: string }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const { onExpand } = useChat();
  const { status, user } = useMemberAuth();

  useEffect(() => {
    if (!open) return;
    function closeOutside(event: Event) {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) setOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    }
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("focusin", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("focusin", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  function closeMenu() { setOpen(false); }

  return (
    <div className="header-menu" ref={rootRef}>
      <button
        className="header-menu-trigger" type="button" ref={triggerRef}
        aria-label={open ? "카테고리 메뉴 닫기" : "카테고리 메뉴 열기"}
        aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(value => !value)}
      >
        <Icon name={open ? "close" : "menu"} size={23} />
      </button>
      {open && (
        <nav className="header-menu-panel" id={panelId} aria-label="카테고리 메뉴">
          <p className="header-menu-label">카테고리</p>
          <ul className="header-menu-list">
            <li>
              <Link className="header-menu-category" href="/" aria-current={pathname === "/" ? "page" : undefined} onClick={closeMenu}>
                <span className="header-menu-icon"><Icon name="home" size={20} /></span>
                <span>메인</span><Icon className="header-menu-arrow" name="arrow" size={16} />
              </Link>
            </li>
            {groups.map(group => (
              <li key={group.title}>
                <details className="header-menu-group" open={group.items.some(item => item.href === pathname) || (group.title === "직관 코스 짜기" && pathname.startsWith("/routes/")) || (group.title === "커뮤니티" && (pathname === "/community" || pathname.startsWith("/community/"))) ? true : undefined}>
                  <summary className="header-menu-category">
                    {"image" in group ? <span className="header-menu-icon has-image"><Image src={group.image} alt="" width={64} height={64} /></span> : <span className="header-menu-icon"><Icon name={group.icon} size={20} /></span>}
                    <span>{group.title}</span><Icon className="header-menu-chevron" name="chevron" size={16} />
                  </summary>
                  <ul className="header-menu-children">
                    {group.items.map(item => (
                      <li key={item.href}>
                        <Link href={item.href} aria-current={pathname === item.href ? "page" : undefined} onClick={closeMenu}>
                          {item.title}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </details>
              </li>
            ))}
            <li>
              <button className="header-menu-category" type="button" onClick={() => { closeMenu(); onExpand(); }}>
                <span className="header-menu-icon"><CapBot size={24} /></span>
                <span>챗봇</span><Icon className="header-menu-arrow" name="arrow" size={16} />
              </button>
            </li>
            {status === "authenticated" && user?.is_active && user.is_staff && (
              <li>
                <Link className="header-menu-category" href="/admin" aria-label="Admin 대시보드" aria-current={pathname === "/admin" || pathname.startsWith("/admin/") ? "page" : undefined} onClick={closeMenu}>
                  <span className="header-menu-icon"><Icon name="menu" size={20} /></span>
                  <span>Admin</span><Icon className="header-menu-arrow" name="arrow" size={16} />
                </Link>
              </li>
            )}
          </ul>
        </nav>
      )}
    </div>
  );
}

export function HeaderMenu() {
  const pathname = usePathname();
  // Also close on back/forward navigation; current-page links close on click.
  return <HeaderMenuContent key={pathname} pathname={pathname} />;
}
