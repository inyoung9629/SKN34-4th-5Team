import type { SVGProps } from "react";

const paths = {
  home: "m3 10 9-7 9 7M5 9v12h5v-7h4v7h5V9",
  search: "M21 21l-5.1-5.1M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0",
  arrow: "M4 12h15M13 5l7 7-7 7",
  chevron: "m9 5 7 7-7 7",
  menu: "M4 6h16M4 12h16M4 18h16",
  chat: "M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z",
  close: "m6 6 12 12M6 18 18 6",
  sparkles: "m12 3 2.8 6.2L21 12l-6.2 2.8L12 21l-2.8-6.2L3 12l6.2-2.8L12 3ZM20 2v4M18 4h4",
  route: "M6 8v7a4 4 0 0 0 4 4h4M18 16V9a4 4 0 0 0-4-4h-4M9 5a3 3 0 1 1-6 0 3 3 0 0 1 6 0M21 19a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  stadium: "M3 8c0-2.2 4-4 9-4s9 1.8 9 4-4 4-9 4-9-1.8-9-4ZM3 8v8c0 2.2 4 4 9 4s9-1.8 9-4V8M7 12v6M12 13v7M17 12v6M7 7c3-1.3 7-1.3 10 0",
  book: "M4 3h12l4 4v14H4V3ZM15 3v5h5M8 12h8M8 16h6M8 7h3",
  pin: "M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0ZM14.5 10a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0",
  heart: "M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z",
  clock: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 7v5l3 2",
  check: "m5 12 4 4L19 6",
  login: "M14 4h6v16h-6M3 12h12M10 7l5 5-5 5",
  userPlus: "M14 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0M3 21v-2a7 7 0 0 1 11-5.7M19 13v8M15 17h8",
  refresh: "M20 7a8 8 0 0 0-14-2L3 8M3 3v5h5M4 17a8 8 0 0 0 14 2l3-3M21 21v-5h-5",
  map: "m3 5 6-3 6 3 6-3v17l-6 3-6-3-6 3V5ZM9 2v17M15 5v17",
} as const;

export function Icon({ name, size = 24, ...props }: SVGProps<SVGSVGElement> & { name: keyof typeof paths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={paths[name]} /></svg>;
}

export function Baseball({ className = "" }: { className?: string }) {
  const seams = [
    [[70, 58], [333, 93], [349, 350], [107, 455]],
    [[415, 64], [250, 198], [312, 368], [456, 398]],
  ];
  return (
    <svg className={className} viewBox="0 0 500 500" fill="none" aria-hidden="true">
      <defs>
        <radialGradient id="ball-fill" cx=".35" cy=".3" r=".75"><stop stopColor="#fff" /><stop offset="1" stopColor="#edf5ff" /></radialGradient>
        <linearGradient id="ball-fade" x1="250" y1="40" x2="250" y2="490" gradientUnits="userSpaceOnUse"><stop stopColor="white" /><stop offset=".6" stopColor="white" /><stop offset="1" stopColor="black" /></linearGradient>
        <mask id="ball-mask"><rect width="500" height="500" fill="url(#ball-fade)" /></mask>
        <clipPath id="ball-clip"><circle cx="250" cy="250" r="232" /></clipPath>
      </defs>
      <g mask="url(#ball-mask)">
        <circle cx="250" cy="250" r="232" fill="url(#ball-fill)" stroke="#c8ddfb" strokeWidth="5" />
        <g clipPath="url(#ball-clip)" stroke="#c8ddfb" strokeLinecap="round" strokeLinejoin="round" transform="rotate(-12 250 250)">
          {seams.map((p, n) => <g key={n}>
            <path d={"M" + p[0].join(" ") + "C" + p.slice(1).map(v => v.join(" ")).join(" ")} strokeWidth="3" />
            {Array.from({ length: 21 }, (_, i) => {
              const t = (i + .4) / 22, u = 1 - t;
              const x = u*u*u*p[0][0] + 3*u*u*t*p[1][0] + 3*u*t*t*p[2][0] + t*t*t*p[3][0];
              const y = u*u*u*p[0][1] + 3*u*u*t*p[1][1] + 3*u*t*t*p[2][1] + t*t*t*p[3][1];
              const dx = 3*u*u*(p[1][0]-p[0][0]) + 6*u*t*(p[2][0]-p[1][0]) + 3*t*t*(p[3][0]-p[2][0]);
              const dy = 3*u*u*(p[1][1]-p[0][1]) + 6*u*t*(p[2][1]-p[1][1]) + 3*t*t*(p[3][1]-p[2][1]);
              const rotation = (Math.atan2(dy, dx) * 180 / Math.PI - 90).toFixed(3);
              return <path key={i} d="M-14-7Q-4-6 0 3Q4-6 14-7" transform={"translate(" + x.toFixed(3) + " " + y.toFixed(3) + ") rotate(" + rotation + ")"} strokeWidth="5" />;
            })}
          </g>)}
        </g>
      </g>
    </svg>
  );
}

// Chat assistant mark: a small robot (antenna, side ears, face screen, chin plate) wearing a baseball cap, brim to the right.
export function CapBot({ size = 26, ...props }: SVGProps<SVGSVGElement> & { size?: number }) {
  const face = "#eef4ff";
  return <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true" {...props}>
    <circle cx="14.7" cy="1.9" r="1.1" stroke="currentColor" strokeWidth="0.9" />
    <path d="M14.7 3v2.6" stroke="currentColor" strokeWidth="0.9" strokeLinecap="round" />
    <ellipse cx="14.7" cy="5.6" rx="1.1" ry="0.6" fill="currentColor" />
    <rect x="1.4" y="18.4" width="2.7" height="7" rx="1.1" fill="currentColor" />
    <rect x="26.1" y="18.4" width="2.7" height="7" rx="1.1" fill="currentColor" />
    <rect x="8.4" y="27.4" width="13.4" height="4" rx="0.9" fill="currentColor" />
    <rect x="10.2" y="28.1" width="9.6" height="2.2" rx="0.3" fill={face} />
    <circle cx="10.9" cy="28.8" r="0.28" fill="currentColor" />
    <circle cx="10.9" cy="29.7" r="0.28" fill="currentColor" />
    <circle cx="19.1" cy="28.8" r="0.28" fill="currentColor" />
    <circle cx="19.1" cy="29.7" r="0.28" fill="currentColor" />
    <rect x="4.6" y="14.6" width="21" height="14.6" rx="4.6" fill="currentColor" />
    <rect x="6.1" y="16.5" width="18" height="11.2" rx="3" fill={face} />
    <ellipse cx="10.2" cy="21.2" rx="1.55" ry="1.85" fill="currentColor" />
    <ellipse cx="20.1" cy="21.2" rx="1.55" ry="1.85" fill="currentColor" />
    <path d="M12.8 23.7q2.3 1.7 4.6 0" stroke="currentColor" strokeWidth="1.15" strokeLinecap="round" />
    <path d="M4.7 15.4C4.7 9.6 9 5.8 14.6 5.8c4.4 0 7.9 2.4 9.4 5.8l4.9-.8c2.3-.3 2.9 2.7.6 2.9l-6.1.2c-3.9.1-8.5.8-12.9 1.5Z" fill="currentColor" />
    <path d="M14.6 6.3c-2 2.4-3.1 5.3-3.3 8.4" stroke={face} strokeWidth="0.8" strokeLinecap="round" />
    <path d="M11.3 14.7c3.7-1.2 7.7-2 11.6-2.2" stroke={face} strokeWidth="0.7" strokeLinecap="round" />
  </svg>;
}
