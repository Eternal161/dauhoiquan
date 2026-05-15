import os
import re
import time
import json
import uuid
import hashlib
import datetime
import requests
from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# =========================================================
# CONFIG
# =========================================================
TARGET_SITE   = "https://sv2.hoiquan3.live/lich-thi-dau/bong-da"
BASE_URL      = "https://sv2.hoiquan3.live"
FILE_PATH     = "hoiquan.json"
LIMIT_MATCHES = 10

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/dauhoiquan")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

LOGO_CACHE = {}

# Regex nhận diện tên giải đấu thuần tuý (không phải tên trận)
LEAGUE_KEYWORDS = re.compile(
    r'^(copa|liga|league|cup|serie|bundesliga|ligue|super league|'
    r'pro league|a-league|v\.?league|afc|uefa|fifa|premier|champions|'
    r'laliga|eredivisie|ekstraklasa|allsvenskan|mls|j\.?league)',
    re.IGNORECASE,
)

# =========================================================
# HELPER: tạo ID kiểu kaytee-xxxxxxxxxxxx
# =========================================================
def make_id(seed: str = "") -> str:
    raw = seed or str(uuid.uuid4())
    h = hashlib.md5(raw.encode()).hexdigest()
    return f"kaytee-{h[:12]}"

def make_link_id() -> str:
    return "lnk-" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:10]

# =========================================================
# LOGO
# =========================================================
def get_team_logo(team_name: str) -> str:
    if not team_name:
        return ""
    if team_name in LOGO_CACHE:
        return LOGO_CACHE[team_name]
    try:
        slug = team_name.lower().replace(" ", "-")
        r = requests.get(f"https://football-logos.cc/{slug}/", headers=_HEADERS, timeout=5)
        m = re.search(r'https://football-logos\.cc/logos/[^"]+\.png', r.text)
        if m:
            LOGO_CACHE[team_name] = m.group(0)
            return m.group(0)
    except:
        pass
    initials = requests.utils.quote(team_name[:2])
    url = f"https://ui-avatars.com/api/?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"
    LOGO_CACHE[team_name] = url
    return url

# =========================================================
# PARSE TEAMS
# =========================================================
def parse_teams(title: str):
    """
    Trả về (doi_nha, doi_khach).
    Ưu tiên: dòng có 'vs' → cắt theo 'vs'.
    Fallback: cắt theo ' - '.
    Nếu không có gì → trả về title gốc + "Unknown".
    """
    # Bỏ cụm ngày giờ ở cuối slug URL (vd: -2024-05-14-2030)
    clean = re.sub(r'[-_]\d{4}-\d{2}-\d{2}[-_]\d{4}$', '', title)
    # Bỏ phần mở rộng kiểu ".Liga 1", ".Premier League" v.v.
    clean = re.sub(r'\.\s*[A-Za-z0-9 \-]{3,30}$', '', clean).strip()

    # Nếu là slug URL toàn chữ thường + gạch ngang → đổi thành dấu cách
    if re.fullmatch(r'[a-z0-9\-]+', clean):
        clean = clean.replace('-', ' ')

    # Cắt theo " vs " (case-insensitive), kể cả "vs."
    m = re.split(r'\s+vs\.?\s+', clean, maxsplit=1, flags=re.IGNORECASE)
    if len(m) == 2 and m[0].strip() and m[1].strip():
        return m[0].strip().title(), m[1].strip().title()

    # Cắt theo " - " (tránh cắt nhầm khi chỉ có 1 phần)
    m2 = re.split(r'\s+-\s+', clean, maxsplit=1)
    if len(m2) == 2 and m2[0].strip() and m2[1].strip():
        return m2[0].strip().title(), m2[1].strip().title()

    # Không nhận ra định dạng → giữ nguyên
    return clean.strip().title(), "Unknown"


def parse_time_from_url(url: str) -> str:
    slug = url.rstrip('/').split('/')[-1]
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', slug)
    if m:
        return f"{m.group(4)}:{m.group(5)} {m.group(3)}/{m.group(2)}/{m.group(1)}"
    return ""

# =========================================================
# CHỌN TITLE TỪ DANH SÁCH DÒNG VĂN BẢN
# =========================================================
def pick_match_title(lines: list, fallback_href: str = "") -> str:
    """
    Tìm dòng text chứa tên trận (có 'vs' hoặc dạng 'A - B').
    Bỏ qua các dòng chỉ là tên giải đấu.
    """
    VS_RE   = re.compile(r'\bvs\.?\b', re.IGNORECASE)
    DASH_RE = re.compile(r'^.{2,}\s+-\s+.{2,}$')

    # Ưu tiên dòng có " vs "
    for line in lines:
        if VS_RE.search(line) and not LEAGUE_KEYWORDS.match(line.strip()):
            return line.strip()

    # Fallback: dòng dạng "Đội A - Đội B" nhưng không phải tên giải
    for line in lines:
        if DASH_RE.match(line.strip()) and not LEAGUE_KEYWORDS.match(line.strip()):
            return line.strip()

    # Cuối cùng: dùng slug URL
    if fallback_href:
        return fallback_href.rstrip('/').split('/')[-1]

    return lines[0] if lines else ""

# =========================================================
# CAPTURE STREAM
# =========================================================
def capture_stream(context, match_url: str) -> list:
    """Trả về list URL m3u8 hợp lệ, tốt nhất đứng đầu."""
    page = context.new_page()
    try:
        Stealth().apply_stealth_sync(page)
    except:
        pass

    streams = set()

    BAD = [
        ".gif", ".png", ".jpg", ".jpeg", ".webp", ".svg",
        ".mp4", ".mp3", ".vtt", ".srt",
        "waiting", "loop", "placeholder", "fallback", "saba.m3u8",
        "/ad/", "/ads/", "/vast/", "quangcao", "banner", "preroll", "postroll",
    ]

    def process_url(url):
        u = url.lower()
        if ".m3u8" not in u:
            return
        if any(b in u for b in BAD):
            return
        streams.add(url)
        print(f"      🎯 {url[:90]}")

    page.on("request",  lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    try:
        page.add_init_script("""
        (() => {
            const oF = window.fetch;
            window.fetch = async (...a) => {
                if (typeof a[0] === 'string' && a[0].includes('.m3u8')) console.log('FETCH:', a[0]);
                return oF(...a);
            };
            const oX = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(m, u, ...r) {
                if (u && u.includes('.m3u8')) console.log('XHR:', u);
                return oX.apply(this, [m, u, ...r]);
            };
        })();
        """)

        page.goto(match_url, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)

        # Xóa overlay
        try:
            page.evaluate("""
            document.querySelectorAll('*').forEach(el => {
                const s = window.getComputedStyle(el);
                if (s.position === 'fixed' && parseInt(s.zIndex||0) > 900) el.remove();
            });
            """)
        except:
            pass

        # Click kích hoạt player
        try:
            vp = page.viewport_size
            if vp:
                cx, cy = vp["width"] // 2, vp["height"] // 2
                for _ in range(2):
                    page.mouse.click(cx, cy)
                    page.wait_for_timeout(1000)
        except:
            pass

        # Ép play
        for frame in page.frames:
            try:
                frame.evaluate("""
                document.querySelectorAll('video').forEach(v => {
                    v.muted = true; v.play().catch(()=>{});
                });
                """)
            except:
                pass

        # Chờ tối đa 20s để bắt stream có token
        deadline = time.time() + 20
        while time.time() < deadline:
            if any("100ycdn" in s.lower() or "edgemaxcdn" in s.lower() for s in streams):
                break
            time.sleep(1)

        # Quét HTML + iframe
        try:
            for url in re.findall(r'https?://[^\s"\'<>]+\.m3u8(?:[^\s"\'<>]*)?', page.content(), re.I):
                process_url(url)
        except:
            pass
        for iframe in page.query_selector_all("iframe"):
            try:
                frame = iframe.content_frame()
                if frame:
                    for url in re.findall(r'https?://[^\s"\'<>]+\.m3u8(?:[^\s"\'<>]*)?', frame.content(), re.I):
                        process_url(url)
            except:
                pass

    except PWTimeout:
        print("      ⚠️ TIMEOUT")
    except Exception as e:
        print(f"      ❌ {e}")
    finally:
        page.close()

    if not streams:
        return []

    # Chấm điểm để sắp xếp stream tốt nhất lên đầu
    scored = []
    for s in streams:
        score = 0
        lo = s.lower()
        if any(x in lo for x in ["waiting","loop","placeholder","fallback","saba.m3u8"]): score -= 5000
        if any(x in lo for x in ["/ad/","/ads/","/vast/","quangcao","preroll","banner"]): score -= 10000
        if "100ycdn.com"   in lo: score += 5000
        if "edgemaxcdn"    in lo: score += 4500
        if "hqtv"          in lo: score += 1000
        if "wssession="    in lo: score += 2000
        if "wsbindip="     in lo: score += 1000
        if "expire="       in lo: score += 800
        if "sign="         in lo: score += 800
        if "token="        in lo: score += 800
        if "playlist.m3u8" in lo: score += 500
        if "index.m3u8"    in lo: score += 200
        scored.append((score, s))

    scored.sort(reverse=True, key=lambda x: x[0])
    return [s for sc, s in scored if sc > -5000]

# =========================================================
# BUILD JSON (format KayTee / Thập Cẩm)
# =========================================================
def build_channel(match_title: str, thoi_gian: str, is_live: bool,
                  stream_urls: list, match_url: str, thumb_url: str) -> dict:
    cid = make_id(match_url)
    doi_nha, doi_khach = parse_teams(match_title)
    title_clean = f"{doi_nha} vs {doi_khach}"
    display_name = f"⚽ {title_clean} | {thoi_gian}" if thoi_gian else f"⚽ {title_clean}"

    # Label trạng thái
    if is_live and stream_urls:
        label = {"text": "● Live", "position": "top-left", "color": "#00ffffff", "text_color": "#ff0000"}
    elif is_live:
        label = {"text": "🔴 Chờ stream", "position": "top-left", "color": "#00ffffff", "text_color": "#ff6600"}
    else:
        label = {"text": "⏳ Chưa live", "position": "top-left", "color": "#00ffffff", "text_color": "#d54f1a"}

    # Stream links — tối đa 2 link
    stream_links = []
    for idx, url in enumerate(stream_urls[:2], 1):
        stream_links.append({
            "id": make_link_id(),
            "name": f"Link {idx}",
            "type": "hls",
            "default": idx == 1,
            "url": url,
        })

    return {
        "id": cid,
        "name": display_name,
        "type": "single",
        "display": "thumbnail-only",
        "enable_detail": False,
        "image": {
            "padding": 1,
            "background_color": "#ececec",
            "display": "contain",
            "url": thumb_url,
            "width": 1600,
            "height": 1200,
        },
        "labels": [label],
        "sources": [
            {
                "id": cid,
                "name": "Hội Quán",
                "contents": [
                    {
                        "id": cid,
                        "name": title_clean,
                        "streams": [
                            {
                                "id": cid,
                                "name": "F",
                                "stream_links": stream_links,
                            }
                        ],
                    }
                ],
            }
        ],
    }


def build_json(channels: list) -> dict:
    now = datetime.datetime.now(VN_TZ)
    return {
        "id": "hoiquan",
        "url": "https://raw.githubusercontent.com/Eternal161/dauhoiquan/main/hoiquan.json",
        "name": "Hội Quán TV",
        "color": "#1cb57a",
        "grid_number": 3,
        "image": {
            "type": "cover",
            "url": "https://kaytee1012.github.io/hoiquan_logo.png",
        },
        "groups": [
            {
                "id": "live",
                "name": "🔴 Live bóng đá",
                "display": "vertical",
                "grid_number": 2,
                "enable_detail": False,
                "channels": channels,
            }
        ],
    }

# =========================================================
# GITHUB PUSH
# =========================================================
def push_to_github(content: str):
    if not GITHUB_TOKEN:
        print("⚠️ NO GH_TOKEN — lưu local")
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        return
    g    = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    msg  = "⚽ Update Hội Quán: " + datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print("✅ Updated GitHub")
    except:
        repo.create_file(FILE_PATH, msg, content)
        print("✅ Created GitHub file")

# =========================================================
# MAIN
# =========================================================
def scrape_and_push():
    print("=" * 70)
    print(datetime.datetime.now(VN_TZ).strftime("START HỘI QUÁN BOT %H:%M:%S %d/%m/%Y"))
    print("=" * 70)

    matches_raw = []   # list dict: href, title, thoi_gian, is_live

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage", "--disable-gpu",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_HEADERS["User-Agent"],
            ignore_https_errors=True,
        )

        # ── Bước 1: Quét trang lịch ──────────────────────────────────────────
        print(f"\n📺 QUÉT: {TARGET_SITE}")
        page = context.new_page()
        try:
            Stealth().apply_stealth_sync(page)
        except:
            pass

        try:
            page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
        except:
            pass

        for _ in range(4):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(800)

        seen = set()
        for selector in [
            "a[href*='truc-tiep']", "a[href*='/bong-da/']",
            "a[href*='-vs-']", ".match-item a", ".event-item a",
            ".schedule-item a", ".list-match a", "table a",
        ]:
            try:
                for el in page.query_selector_all(selector):
                    href = el.get_attribute("href") or ""
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = BASE_URL + href
                    if "hoiquan3.live" not in href or href in seen:
                        continue
                    seen.add(href)

                    title     = ""
                    thoi_gian = ""
                    is_live   = False

                    try:
                        pt = el.evaluate(
                            "el => { const p = el.closest('.match-item,.event-item,tr,.match-card,.schedule-item,li,.item'); return p ? p.innerText : el.innerText; }"
                        )
                        if pt:
                            lines = [l.strip() for l in pt.split('\n') if l.strip()]

                            # ── Chọn tên trận thông minh ──
                            title = pick_match_title(lines, fallback_href=href)

                            # ── Tìm thời gian và trạng thái ──
                            for line in lines:
                                if re.search(r'\d{1,2}:\d{2}', line) and not thoi_gian:
                                    thoi_gian = line.strip()
                                if any(kw in line.lower() for kw in ['live', 'trực tiếp', 'đang phát']):
                                    is_live = True
                    except:
                        # Nếu không lấy được text từ DOM, dùng slug URL
                        title = href.rstrip('/').split('/')[-1]

                    matches_raw.append({
                        "href":      href,
                        "title":     title,
                        "thoi_gian": thoi_gian,
                        "is_live":   is_live,
                    })
            except:
                pass

        page.close()

        if LIMIT_MATCHES:
            matches_raw = matches_raw[:LIMIT_MATCHES]

        print(f"   ✅ {len(matches_raw)} trận")

        # ── Bước 2: Xác định is_live theo giờ ────────────────────────────────
        for m in matches_raw:
            tg = m["thoi_gian"] or parse_time_from_url(m["href"])
            m["thoi_gian"] = tg
            if not m["is_live"] and tg:
                try:
                    mt = datetime.datetime.strptime(tg, "%H:%M %d/%m/%Y").replace(tzinfo=VN_TZ)
                    diff = (datetime.datetime.now(VN_TZ) - mt).total_seconds() / 60
                    if -10 <= diff <= 120:
                        m["is_live"] = True
                except:
                    pass

        # ── Bước 3: Bắt stream cho trận live ─────────────────────────────────
        live = [m for m in matches_raw if m["is_live"]]
        print(f"\n🎥 BẮT STREAM {len(live)} TRẬN LIVE...")

        for m in matches_raw:
            m["streams"] = []

        for idx, m in enumerate(live, 1):
            doi_nha, doi_khach = parse_teams(m["title"] or m["href"].split('/')[-1])
            print(f"\n   [{idx}/{len(live)}] {doi_nha} vs {doi_khach}")
            streams = capture_stream(context, m["href"])
            m["streams"] = streams
            if streams:
                print(f"      ✅ {len(streams)} stream")
            else:
                print("      ⚠️ Không có stream")

        browser.close()

    # ── Bước 4: Build JSON ────────────────────────────────────────────────────
    channels = []
    for m in matches_raw:
        raw_title = m["title"] or m["href"].split('/')[-1]
        doi_nha, doi_khach = parse_teams(raw_title)
        title_clean = f"{doi_nha} vs {doi_khach}"
        thoi_gian = m["thoi_gian"] or "Không rõ"

        # Thumbnail: dùng logo đội nhà
        thumb = get_team_logo(doi_nha)

        ch = build_channel(
            match_title=title_clean,
            thoi_gian=thoi_gian,
            is_live=m["is_live"],
            stream_urls=m["streams"],
            match_url=m["href"],
            thumb_url=thumb,
        )
        channels.append(ch)
        print(f"   ✔ {title_clean} | live={m['is_live']} | streams={len(m['streams'])}")

    output  = build_json(channels)
    content = json.dumps(output, indent=2, ensure_ascii=False)

    push_to_github(content)

    print("\n" + "=" * 70)
    total_live    = sum(1 for m in matches_raw if m["is_live"])
    total_streams = sum(1 for m in matches_raw if m["streams"])
    print(f"✅ HOÀN TẤT: {len(channels)} trận | {total_live} live | {total_streams} có stream")
    print("=" * 70)


if __name__ == "__main__":
    scrape_and_push()
