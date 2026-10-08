export type StadiumWeather = {
  label: "맑음" | "구름많음" | "흐림" | "비" | "비/눈" | "눈" | "소나기";
  temperature: number;
  forecastAt: string;
  issuedAt: string;
  fetchedAt: string;
  source: "기상청 단기예보";
};

const venues: [string, string[]][] = [
  ["JAMSIL", ["잠실"]], ["GOCHEOK", ["고척"]], ["MUNHAK", ["문학", "인천", "랜더스"]],
  ["SUWON", ["수원", "위즈"]], ["DAEJEON", ["대전", "한화생명"]], ["DAEGU", ["대구", "라이온즈"]],
  ["GWANGJU", ["광주", "챔피언스"]], ["SAJIK", ["사직"]], ["CHANGWON", ["창원", "NC파크"]],
];

const labels = new Set(["맑음", "구름많음", "흐림", "비", "비/눈", "눈", "소나기"]);

export function weatherStadiumCode(name: string) {
  return venues.find(([, aliases]) => aliases.some(alias => name.includes(alias)))?.[0];
}

export function parseStadiumWeather(value: unknown): StadiumWeather | null {
  if (!value || typeof value !== "object") return null;
  const weather = value as Record<string, unknown>;
  if (typeof weather.label !== "string" || !labels.has(weather.label)
    || typeof weather.temperature !== "number" || !Number.isFinite(weather.temperature)
    || typeof weather.forecastAt !== "string" || !Number.isFinite(Date.parse(weather.forecastAt))
    || typeof weather.issuedAt !== "string" || !Number.isFinite(Date.parse(weather.issuedAt))
    || typeof weather.fetchedAt !== "string" || !Number.isFinite(Date.parse(weather.fetchedAt))
    || weather.source !== "기상청 단기예보") return null;
  return weather as StadiumWeather;
}

// The backend runs only a few KMA lookups at once and answers 429 instead of queueing,
// so several cards loading together (or a dev re-mount) briefly retry before giving up.
const BUSY_RETRY_DELAYS_MS = [600, 1500];

function wait(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) { reject(signal.reason); return; }
    const onAbort = () => { clearTimeout(timer); reject(signal?.reason); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(); }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function fetchStadiumWeather(
  stadium: string,
  date: string,
  time: string,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
) {
  const query = new URLSearchParams({ stadium, date, time });
  try {
    for (let attempt = 0; ; attempt += 1) {
      const response = await fetcher(`/api/v1/weather/?${query}`, {
        cache: "no-store",
        redirect: "error",
        signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(12000)]) : AbortSignal.timeout(12000),
      });
      if (response.status === 429 && attempt < BUSY_RETRY_DELAYS_MS.length) {
        await wait(BUSY_RETRY_DELAYS_MS[attempt], signal);
        continue;
      }
      if (!response.ok) return null;
      const body: unknown = await response.json();
      return parseStadiumWeather(body && typeof body === "object" ? (body as Record<string, unknown>).weather : null);
    }
  } catch {
    return null;
  }
}
