"""Conservative public HTML reader for bounded, on-demand course verification.

No browser cookies, JavaScript execution, proxy, credentials, CAPTCHA handling,
cross-origin redirects, or persistent body cache. Robots and HTTP blocks stop
access. Allowlisted platform hosts only; unsupported sources stay unread.
"""
from hashlib import sha256
from html.parser import HTMLParser
import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

UA = "CourseEvidenceReader/1.0"
HOSTS = frozenset({"blog.naver.com", "m.blog.naver.com", "www.diningcode.com", "diningcode.com",
    "polle.com", "www.siksinhot.com", "siksinhot.com", "app.passorder.co.kr", "app.catchtable.co.kr",
    "tabling.co.kr", "www.tabling.co.kr", "www.daangn.com", "www.instagram.com", "www.baskinrobbins.co.kr",
    "www.kkanbu.co.kr", "www.mega-mgccoffee.com", "www.ediya.com", "www.starbucks.co.kr",
    "www.cafewhale.com", "www.jongrokimbap.co.kr", "ontheborder.co.kr", "nol.yanolja.com"})


def allowed_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and not p.username and not p.password and p.port in (None, 443)
                and (p.hostname in HOSTS or (p.hostname or "").endswith(".tistory.com"))
                and not any(c.isspace() or ord(c) < 32 for c in url) and "\\" not in url)
    except ValueError:
        return False


def dns_public(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)


class VisibleText(HTMLParser):
    HIDDEN = {"script", "style", "noscript", "svg", "template"}

    def __init__(self, *, skip_dated_grids=False):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []
        self.in_title = False
        self.title = []
        self.skip_dated_grids = skip_dated_grids
        self.skipped_divs = 0

    def handle_starttag(self, tag, attrs):
        if self.skipped_divs:
            self.skipped_divs += tag == "div"
            return
        if self.skip_dated_grids and tag == "div" and "hour-main-grid" in (dict(attrs).get("class") or "").split():
            self.skipped_divs = 1
            self.parts.append("\n")
            return
        if tag in self.HIDDEN:
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag in ("p", "div", "li", "br", "h1", "h2", "h3", "td", "tr") and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.skipped_divs:
            self.skipped_divs -= tag == "div"
            return
        if tag in self.HIDDEN and self.hidden:
            self.hidden -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, text):
        if not self.hidden and not self.skipped_divs:
            self.parts.append(text)
            if self.in_title:
                self.title.append(text)


def extract_text(html, *, skip_dated_grids=False):
    parser = VisibleText(skip_dated_grids=skip_dated_grids)
    parser.feed(html)
    lines = [" ".join(line.split()) for line in " ".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line), " ".join(parser.title)[:250]


class DatedHours(HTMLParser):
    """Keep Diningcode's parallel date/time columns paired within each grid."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.grid = None
        self.rows = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in VisibleText.HIDDEN:
            self.hidden += 1
        if self.hidden:
            return
        classes = set((dict(attrs).get("class") or "").split())
        if self.grid is None:
            if tag != "div" or "hour-main-grid" not in classes:
                return
            self.grid = {"dates": [], "times": [], "today_date": [], "today_time": []}
        ancestors = set().union(*(frame[1] for frame in self.stack))
        kind = ("dates" if "hour_date" in classes and "hour-dates-column" in ancestors else
                "times" if "hour_time_item" in classes and "hour-times-column" in ancestors else
                "today_date" if "open-desc" in classes else
                "today_time" if "today-main-hours" in classes else None)
        if tag in {"br", "div", "p"}:
            self.handle_data("\n")
        if tag not in self.VOID:
            self.stack.append((tag, classes, kind, []))

    def handle_data(self, text):
        if self.hidden:
            return
        for _, _, kind, parts in self.stack:
            if kind:
                parts.append(text)

    def handle_endtag(self, tag):
        if tag in VisibleText.HIDDEN and self.hidden:
            self.hidden -= 1
            return
        if self.hidden:
            return
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        for _, _, kind, parts in reversed(self.stack[index:]):
            if kind:
                lines = [" ".join(line.split()) for line in "".join(parts).splitlines()]
                self.grid[kind].append("\n".join(line for line in lines if line))
        del self.stack[index:]
        if not self.stack:
            today, today_hours = self.grid["today_date"], self.grid["today_time"]
            if (len(today) == len(today_hours) == 1 and today_hours[0]
                    and re.fullmatch(r"오늘(?:\([월화수목금토일]\))?", today[0])):
                self.rows.append(f"영업시간\n{today[0]}\n{today_hours[0]}")
            dates, times = self.grid["dates"], self.grid["times"]
            # Incomplete/mismatched columns cannot establish which day is closed.
            if (0 < len(dates) == len(times) <= 7 and len(set(dates)) == len(dates) and all(times)
                    and all(re.fullmatch(r"\d{1,2}월\s*\d{1,2}일(?:\s*\([월화수목금토일]\))?", day) for day in dates)):
                self.rows.extend(f"영업시간\n{day}\n{hours}" for day, hours in zip(dates, times))
            self.grid = None


def extract_dated_hours(html):
    parser = DatedHours()
    parser.feed(html)
    return parser.rows


def excerpt(text, terms, cap=6000):
    if len(text) <= cap:
        return text, True
    spans = [(0, 1600)]
    for term in terms:
        if not term:
            continue
        for m in list(re.finditer(re.escape(term), text, re.IGNORECASE))[:3]:
            spans.append((max(0, m.start() - 300), min(len(text), m.end() + 650)))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return "\n[... omitted ...]\n".join(text[a:b] for a, b in merged)[:cap], False


class PublicReader:
    def __init__(self, client=None, dns_check=dns_public, gap=1.0, deadline=None, max_requests=32):
        self.deadline = deadline
        self.max_requests = max_requests
        self.owned = client is None
        self.client = client or httpx.Client(timeout=10, follow_redirects=False, trust_env=False,
            transport=httpx.HTTPTransport(retries=0), headers={"User-Agent": UA, "Accept": "text/html,text/plain"})
        self.dns_check = dns_check
        self.gap = gap
        self.last = {}
        self.robots = {}
        self.blocked = set()
        self.attempted_urls = set()
        self.http_requests = 0

    def close(self):
        if self.owned:
            self.client.close()

    def get(self, url, max_bytes):
        host = urlsplit(url).hostname
        if self.http_requests >= self.max_requests or (self.deadline and time.monotonic() >= self.deadline):
            raise ValueError("Reader budget")
        if not self.dns_check(host):
            raise ValueError("Nonpublic DNS")
        time.sleep(max(0, self.gap - (time.monotonic() - self.last.get(host, 0))))
        self.last[host] = time.monotonic()
        self.http_requests += 1
        remaining = min(8., self.deadline - time.monotonic()) if self.deadline else 8.
        if remaining <= 0:
            raise ValueError("Reader deadline")
        with self.client.stream("GET", url, timeout=remaining) as response:
            if response.status_code in (401, 403, 429):
                self.blocked.add(host)
            if response.status_code != 200:
                return response.status_code, "", response.headers.get("location"), response.headers.get("content-type", "")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if self.deadline and time.monotonic() >= self.deadline:
                    raise ValueError("Reader deadline")
                if len(data) > max_bytes:
                    raise ValueError("Body limit")
            encoding = response.charset_encoding or "utf-8"
            try:
                text = bytes(data).decode(encoding, errors="replace")
            except LookupError:
                text = bytes(data).decode("utf-8", errors="replace")
            return 200, text, None, response.headers.get("content-type", "")

    def permitted(self, url):
        p = urlsplit(url)
        origin = urlunsplit((p.scheme, p.netloc, "", "", ""))
        if p.hostname in self.blocked:
            return False, "host_blocked"
        if origin not in self.robots:
            try:
                status, text, _, _ = self.get(origin + "/robots.txt", 200_000)
                if status in (404, 410):
                    self.robots[origin] = True
                elif status == 200:
                    if text.strip() and not re.search(r"(?im)^\s*user-agent\s*:", text):
                        self.robots[origin] = False
                    else:
                        robots = RobotFileParser()
                        robots.parse(text.splitlines())
                        self.robots[origin] = robots
                else:
                    self.robots[origin] = False
            except (httpx.HTTPError, OSError, ValueError):
                self.robots[origin] = False
        robots = self.robots[origin]
        if robots is False:
            return False, "robots_unavailable"
        if robots is True:
            return True, "allowed_without_robots"
        if not robots.can_fetch(UA, url):
            return False, "robots_disallowed"
        delay = robots.crawl_delay(UA)
        rate = robots.request_rate(UA)
        if rate and rate.requests:
            delay = max(delay or 0, rate.seconds / rate.requests)
        if delay and delay > 10:
            return False, "robots_delay_exceeds_pilot_budget"
        if delay:
            wait = max(0, delay - (time.monotonic() - self.last.get(p.hostname, 0)))
            if self.deadline and time.monotonic() + wait >= self.deadline:
                return False, "reader_deadline"
            time.sleep(wait)
        return True, "robots_allowed"

    def read(self, url, terms, *, complete_text=False, metadata_extractor=None):
        row = {"url": url, "body_read": False, "body_text": "", "status": "unread"}
        if not allowed_url(url):
            return {**row, "status": "unsupported_host_or_scheme"}
        if url in self.attempted_urls:
            return {**row, "status": "already_attempted_no_retry"}
        self.attempted_urls.add(url)
        start = time.monotonic()
        try:
            current = url
            for redirect in range(3):
                permitted, reason = self.permitted(current)
                if not permitted:
                    row["status"] = reason
                    break
                status, html, location, content_type = self.get(current, 1_000_000)
                row["http_status"] = status
                if status in (301, 302, 303, 307, 308) and location:
                    target = urljoin(current, location)
                    if not allowed_url(target) or urlsplit(target).netloc != urlsplit(current).netloc:
                        row["status"] = "redirect_not_followed"
                        break
                    current = target
                    row["status"] = "redirect_limit"
                    continue
                if status != 200:
                    row["status"] = "http_" + str(status)
                    break
                if not any(t in content_type.lower() for t in ("text/html", "text/plain", "application/xhtml")):
                    row["status"] = "unsupported_content_type"
                    break
                text, title = extract_text(html)
                # Challenge detection is based on rendered text, not embedded scripts.
                if any(t in text[:1500].lower() for t in ("verify you are human", "access denied", "captcha", "비정상적인 접근", "접근이 제한", "자동입력 방지")):
                    self.blocked.add(urlsplit(current).hostname)
                    row["status"] = "challenge_or_access_denied"
                    break
                if metadata_extractor:
                    # Public page metadata is data only; never execute scripts.
                    row["metadata"] = metadata_extractor(html, current)
                if len(text) < 250:
                    row["status"] = "thin_or_javascript_page"
                    break
                passage, complete = (text, True) if complete_text else excerpt(text, terms)
                row.update(status="read", body_read=True, body_text=passage, title=title,
                           visible_text_complete=complete, visible_text_characters=len(text),
                           text_sha256=sha256(text.encode()).hexdigest(), final_url=current)
                if urlsplit(current).hostname in {"diningcode.com", "www.diningcode.com"}:
                    # Identity/menu evidence retains the ordinary body. Hours
                    # must not reuse flattened columns, even for a broken grid.
                    row["availability_text"] = extract_text(html, skip_dated_grids=True)[0]
                    row["dated_hours"] = extract_dated_hours(html)
                break
        except (httpx.HTTPError, OSError, ValueError) as exc:
            row.update(status="read_error", error_type=type(exc).__name__)
        row["elapsed_seconds"] = round(time.monotonic() - start, 3)
        return row
