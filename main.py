import os
import re
import time
import json
import datetime
import requests
from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# =========================================================
# CONFIG
# =========================================================
TARGET_SITE  = "https://sv2.hoiquan3.live/lich-thi-dau/bong-da"
BASE_URL     = "https://sv2.hoiquan3.live"
FILE_PATH    = "bongda.json"
WAITING_VIDEO_URL = "https://example.com/waiting.mp4"
LIMIT_MATCHES = 10

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/dauhoiquan")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

LOGO_CACHE = {}

# =========================================================
# LOGO
# =========================================================
def normalize_team_name(name):
    name = re.sub(r"\bFc\b$", "FC", name)
    return name.strip()

def get_team_logo(team_name):
    if not team_name or team_name == "Unknown":
        return ""
    team_name = normalize_team_name(team_name)
    if team_name in LOGO_CACHE:
        return LOGO_CACHE[team_name]
    try:
        slug = team_name.lower().replace(" ", "-")
        r = requests.get(f"https://football-logos.cc/{slug}/", headers=_HEADERS, timeout=5)
        match = re.search(r'https://football-logos.cc/logos/[^"]+\.png', r.text)
        if match:
            logo = match.group(0)
            LOGO_CACHE[team_name] = logo
            return logo
    except:
        pass
    initials = requests.utils.quote(team_name[:2])
    return f"https://ui-avatars.com/api/?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"

# =========================================================
# PARSE
# =========================================================
def parse_teams_from_title(title: str):
    clean = re.sub(r'\.[A-Za-z0-9_\- ]{3,15}$', '', title).strip()
    if ' vs ' in clean.lower():
        parts = re.split(r'\s+vs\s+', clean, flags=re.IGNORECASE)
        return parts[0].strip().title(), (parts[1].strip().title() if len(parts) > 1 else "Unknown")
    return clean.title(), "Unknown"

def parse_time_from_url(url: str) -> str:
    slug = url.rstrip('/').split('/')[-1]
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', slug)
    if m:
        return f"{m.group(4)}:{m.group(5)} {m.group(3)}/{m.group(2)}/{m.group(1)}"
    return "Không rõ"

# =========================================================
# CAPTURE STREAM — hoiquan3.live dùng 100ycdn / edgemaxcdn
# =========================================================
def capture_stream(context, match_url: str):
    page = context.new_page()
    try:
        Stealth().apply_stealth_sync(page)
    except:
        pass

    streams = set()

    # Blacklist — loại bỏ hoàn toàn
    BAD = [
        ".gif", ".png", ".jpg", ".jpeg", ".webp", ".svg",
        ".mp4", ".mp3", ".vtt", ".srt",
        "waiting", "loop", "placeholder", "fallback", "saba.m3u8",
        "/ad/", "/ads/", "/vast/", "quangcao", "banner",
        "preroll", "postroll",
    ]

    # CDN hợp lệ của hoiquan3.live
    VALID_CDN = [
        "100ycdn.com",
        "edgemaxcdn.org",
        "edgemaxcdn.net",
        "hoiquan",
        "hqtv",
    ]

    def process_url(url):
        u = url.lower()
        if ".m3u8" not in u:
            return
        if any(bad in u for bad in BAD):
            return
        streams.add(url)
        print(f"      🎯 TÓM: {url[:90]}")

    page.on("request",  lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    try:
        page.add_init_script("""
        (() => {
            const origFetch = window.fetch;
            window.fetch = async (...args) => {
                if (typeof args[0] === 'string') console.log('FETCH:', args[0]);
                return origFetch(...args);
            };
            const origXHR = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                if (url && url.includes('.m3u8')) console.log('XHR:', url);
                return origXHR.apply(this, [method, url, ...rest]);
            };
        })();
        """)

        page.goto(match_url, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)

        # Xóa overlay/popup
        try:
            page.evaluate("""
            document.querySelectorAll('*').forEach(el => {
                const s = window.getComputedStyle(el);
                if (s.position === 'fixed' && parseInt(s.zIndex) > 900) el.remove();
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

        # Ép play video
        for frame in page.frames:
            try:
                frame.evaluate("""
                document.querySelectorAll('video').forEach(v => {
                    v.muted = true; v.play().catch(()=>{});
                });
                """)
            except:
                pass

        # Chờ tối đa 20s — hoiquan có thể load chậm hơn
        deadline = time.time() + 20
        while time.time() < deadline:
            if any("100ycdn" in s.lower() or "edgemaxcdn" in s.lower() for s in streams):
                break
            time.sleep(1)

        # Tìm thêm trong HTML render + iframe
        try:
            html = page.content()
            for url in re.findall(r'https?://[^\s"\'<>]+\.m3u8(?:[^\s"\'<>]*)?', html, re.IGNORECASE):
                process_url(url)
        except:
            pass

        for iframe in page.query_selector_all("iframe"):
            try:
                frame = iframe.content_frame()
                if frame:
                    for url in re.findall(r'https?://[^\s"\'<>]+\.m3u8(?:[^\s"\'<>]*)?', frame.content(), re.IGNORECASE):
                        process_url(url)
            except:
                pass

    except PWTimeout:
        print("      ⚠️ TIMEOUT")
    except Exception as e:
        print(f"      ❌ LỖI: {e}")
    finally:
        page.close()

    if not streams:
        return None

    # Chấm điểm — ưu tiên CDN chính của hoiquan
    priority = []
    for s in streams:
        score = 0
        lower = s.lower()
        # Rác
        if any(x in lower for x in ["waiting", "loop", "placeholder", "fallback", "saba.m3u8"]):
            score -= 5000
        if any(x in lower for x in ["/ad/", "/ads/", "/vast/", "quangcao", "preroll", "banner"]):
            score -= 10000
        # CDN tốt của hoiquan3.live
        if "100ycdn.com"     in lower: score += 5000
        if "edgemaxcdn"      in lower: score += 4500
        if "hqtv"            in lower: score += 1000
        # Có token/session = stream thật
        if "wssession="      in lower: score += 2000
        if "wsbindip="       in lower: score += 1000
        if "expire="         in lower: score += 800
        if "sign="           in lower: score += 800
        if "token="          in lower: score += 800
        # Loại file
        if "playlist.m3u8"   in lower: score += 500
        if "index.m3u8"      in lower: score += 200

        priority.append((score, s))

    priority.sort(reverse=True, key=lambda x: x[0])
    best_score, best_url = priority[0]

    if best_score > -5000:
        print(f"      ✅ CHỐT: {best_url[:90]}")
        return best_url

    print("      ⚠️ CHỈ CÓ STREAM RÁC")
    return None

# =========================================================
# JSON & GITHUB
# =========================================================
def create_json(matches):
    total_live    = sum(1 for m in matches if m.get("is_live"))
    total_streams = sum(1 for m in matches if m.get("stream_url") and m["stream_url"] != WAITING_VIDEO_URL)
    data = {
        "playlist_name": "Hội Quán TV",
        "last_updated": datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y"),
        "total_live": total_live,
        "total_streams": total_streams,
        "matches": matches,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)

def push_to_github(content):
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
    matches_data = []
    print("=" * 70)
    print(datetime.datetime.now(VN_TZ).strftime("START HỘI QUÁN BOT %H:%M:%S %d/%m/%Y"))
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage", "--disable-gpu",
                "--autoplay-policy=no-user-gesture-required",
            ]
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_HEADERS["User-Agent"],
            ignore_https_errors=True,
        )

        # ── Bước 1: Quét trang lịch thi đấu ─────────────────────────────────
        print(f"\n📺 ĐANG QUÉT: {TARGET_SITE}")
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

        # Thu thập link trận
        links_info = []
        seen = set()

        for selector in [
            "a[href*='truc-tiep']",
            "a[href*='/bong-da/']",
            "a[href*='-vs-']",
            ".match-item a", ".event-item a",
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

                    # Lấy metadata từ DOM
                    title = ""
                    thoi_gian = ""
                    is_live = False
                    try:
                        parent_text = el.evaluate(
                            "el => { const p = el.closest('.match-item,.event-item,tr,.match-card,.schedule-item,li,.item'); return p ? p.innerText : el.innerText; }"
                        )
                        if parent_text:
                            lines = [l.strip() for l in parent_text.split('\n') if l.strip()]
                            if lines:
                                title = lines[0]
                            for line in lines:
                                if re.search(r'\d{1,2}:\d{2}', line):
                                    thoi_gian = line.strip()
                                if any(kw in line.lower() for kw in ['live', 'trực tiếp', 'đang phát']):
                                    is_live = True
                    except:
                        pass

                    links_info.append({"href": href, "title": title, "thoi_gian": thoi_gian, "is_live": is_live})
            except:
                pass

        if LIMIT_MATCHES:
            links_info = links_info[:LIMIT_MATCHES]

        print(f"   ✅ TÌM THẤY {len(links_info)} TRẬN")
        page.close()

        # ── Bước 2: Xây danh sách trận ───────────────────────────────────────
        for idx, info in enumerate(links_info):
            href      = info["href"]
            raw_title = info["title"] or href.split('/')[-1]
            doi_nha, doi_khach = parse_teams_from_title(raw_title)
            thoi_gian = info["thoi_gian"] or parse_time_from_url(href)
            is_live   = info["is_live"]
            status    = "Đang trực tiếp 🔴" if is_live else "Chưa đá ⏳"

            if not is_live and thoi_gian not in ("Không rõ", ""):
                try:
                    match_time = datetime.datetime.strptime(thoi_gian, "%H:%M %d/%m/%Y").replace(tzinfo=VN_TZ)
                    diff = (datetime.datetime.now(VN_TZ) - match_time).total_seconds() / 60
                    if -10 <= diff <= 120:
                        is_live, status = True, "Đang trực tiếp 🔴"
                    elif diff > 120:
                        status = "Đã kết thúc 🏁"
                except:
                    pass

            print(f"   [{idx+1}] {'🔴' if is_live else '⚪'} {doi_nha} vs {doi_khach} | {thoi_gian}")
            matches_data.append({
                "id": str(idx + 1),
                "title": f"{doi_nha} vs {doi_khach}",
                "doi_nha": doi_nha,
                "doi_khach": doi_khach,
                "thoi_gian": thoi_gian,
                "trang_thai": status,
                "is_live": is_live,
                "logo_nha": get_team_logo(doi_nha),
                "logo_khach": get_team_logo(doi_khach),
                "stream_url": WAITING_VIDEO_URL,
                "link_xem": href,
            })

        # ── Bước 3: Bắt stream live ───────────────────────────────────────────
        live_matches = [m for m in matches_data if m["is_live"]]
        print(f"\n🎥 BẮT LUỒNG {len(live_matches)} TRẬN LIVE...")

        for idx, match in enumerate(live_matches):
            print(f"\n   [{idx+1}/{len(live_matches)}] {match['title']}")
            stream = capture_stream(context, match["link_xem"])
            if stream:
                match["stream_url"] = stream
            else:
                print("      ⚠️ Không có stream")

        browser.close()

    push_to_github(create_json(matches_data))
    print("\n" + "=" * 70)
    print("✅ HOÀN TẤT HỘI QUÁN TV")
    print("=" * 70)

if __name__ == "__main__":
    scrape_and_push()
