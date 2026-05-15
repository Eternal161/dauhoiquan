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
LIMIT_MATCHES = 10 # Bạn có thể chỉnh số lượng trận muốn quét ở đây

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/dauhoiquan")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

LOGO_CACHE = {}

# =========================================================
# HELPER
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
def get_api_logo(team_name: str) -> str:
    if not team_name or team_name == "Unknown": return ""
    team_name = re.sub(r"\bFc\b$", "FC", team_name).strip()
    if team_name in LOGO_CACHE: return LOGO_CACHE[team_name]
    try:
        slug = team_name.lower().replace(" ", "-")
        r = requests.get(f"https://football-logos.cc/{slug}/", headers=_HEADERS, timeout=5)
        m = re.search(r'https://football-logos\.cc/logos/[^"]+\.png', r.text)
        if m:
            LOGO_CACHE[team_name] = m.group(0)
            return m.group(0)
    except: pass
    LOGO_CACHE[team_name] = ""
    return ""

def get_final_logo(team_name: str, site_logo: str) -> str:
    api_logo = get_api_logo(team_name)
    if api_logo: return api_logo
    if site_logo and site_logo.startswith("http"): return site_logo
    initials = requests.utils.quote(team_name[:2] if len(team_name) >= 2 else "FC")
    return f"https://ui-avatars.com/api/?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"

# =========================================================
# PARSE TÊN ĐỘI TỪ SLUG URL
# =========================================================
def parse_teams_from_title(title: str):
    clean = re.sub(r'[-_]\d{4}-\d{2}-\d{2}[-_]\d{4}$', '', title)
    clean = re.sub(r'\.\s*[A-Za-z0-9 \-]{3,30}$', '', clean).strip()
    if re.fullmatch(r'[a-z0-9\-]+', clean):
        clean = clean.replace('-', ' ')
    m = re.split(r'\s+vs\.?\s+', clean, maxsplit=1, flags=re.IGNORECASE)
    if len(m) == 2 and m[0].strip() and m[1].strip():
        return m[0].strip().title(), m[1].strip().title()
    m2 = re.split(r'\s+-\s+', clean, maxsplit=1)
    if len(m2) == 2 and m2[0].strip() and m2[1].strip():
        return m2[0].strip().title(), m2[1].strip().title()
    return clean.strip().title(), "Unknown"

def parse_time_from_url(url: str) -> str:
    slug = url.rstrip('/').split('/')[-1]
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', slug)
    if m:
        # Đã thêm dấu cách giữa Giờ và Ngày ở đây
        return f"{m.group(4)}:{m.group(5)} {m.group(3)}/{m.group(2)}/{m.group(1)}"
    return ""

# =========================================================
# JS: EXTRACT MATCH DATA
# =========================================================
JS_EXTRACT = """
() => {
    const results = [];
    const seen = new Set();
    const clean = t => (t || '').replace(/\\s+/g, ' ').trim();
    const SKIP = /^(vs\\.?|live|blv|bóng đá|sắp diễn ra|đang phát|đặt cược|bảng|giải|\\d+[:\\-\\/]\\d+|\\d+$)/i;

    const anchors = Array.from(document.querySelectorAll('a[href]')).filter(a => {
        const h = a.href || '';
        return (
            h.includes('hoiquan') &&
            h.length > 40 &&
            !h.includes('/lich-thi-dau') &&
            !h.includes('/ket-qua')
        );
    });

    for (const a of anchors) {
        const href = a.href;
        if (seen.has(href)) continue;
        seen.add(href);

        let league = '';
        const leagueSelectors = ['[class*="league" i]', '[class*="tournament" i]', 'h3', 'h4'];
        for (const sel of leagueSelectors) {
            const el = a.querySelector(sel);
            if (el) {
                const t = clean(el.innerText);
                if (t && t.length < 40 && !/\\d+\\s*[-:]\\s*\\d+/.test(t)) {
                    league = t;
                    break;
                }
            }
        }

        const cardText = clean(a.innerText).toLowerCase();
        const isLive = /live|trực tiếp|đang phát/.test(cardText);

        let home = '', away = '';
        const gridBox = a.querySelector('div[class*="grid-cols-[1fr_auto_1fr]"]');
        if (gridBox && gridBox.children.length >= 3) {
            home = clean(gridBox.children[0].innerText);
            away = clean(gridBox.children[2].innerText);
        }

        let homeLogo = '', awayLogo = '';
        if (gridBox && gridBox.children.length >= 3) {
            const imgNha = gridBox.children[0].querySelector('img');
            const imgKhach = gridBox.children[2].querySelector('img');
            if (imgNha) homeLogo = imgNha.src;
            if (imgKhach) awayLogo = imgKhach.src;
        }

        let timeStr = '';
        const timeEl = a.querySelector('[class*="time" i], [class*="date" i]');
        if (timeEl) timeStr = clean(timeEl.innerText);

        results.push({ href, home, away, timeStr, isLive, league, homeLogo, awayLogo });
    }
    return results;
}
"""

# =========================================================
# CAPTURE STREAM
# =========================================================
def capture_stream(context, match_url: str) -> list:
    page = context.new_page()
    try: Stealth().apply_stealth_sync(page)
    except: pass

    streams = set()
    def process_url(url):
        u = url.lower()
        if ".m3u8" in u and not any(b in u for b in ["/ad/", "/ads/", "fallback"]):
            streams.add(url)

    page.on("request",  lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    try:
        page.goto(match_url, wait_until="load", timeout=60000)
        page.wait_for_timeout(8000) # Chờ 8s để player load link
        page.mouse.wheel(0, 500)
        page.wait_for_timeout(2000)
    except: pass
    finally: page.close()

    if not streams: return []
    
    scored = []
    for s in streams:
        score = 0
        lo = s.lower()
        if "100ycdn.com" in lo: score += 5000
        if "edgemaxcdn" in lo: score += 4500
        if "wssession=" in lo: score += 2000
        scored.append((score, s))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [s for sc, s in scored]

# =========================================================
# BUILD JSON
# =========================================================
def build_channel(home, away, thoi_gian, is_live, stream_urls, match_url, logo_nha, logo_khach, league=""):
    cid = make_id(match_url)
    title_clean = f"{home} vs {away}"
    
    # Xử lý khoảng cách thời gian cho đẹp trên App
    if thoi_gian:
        thoi_gian = re.sub(r'(\d{1,2}:\d{2})(\d{1,2}/\d{2})', r'\1 \2', thoi_gian)
        
    display_name = f"⚽ {title_clean}"
    if league: display_name += f" | {league}"
    if thoi_gian: display_name += f" | {thoi_gian}"

    if is_live and stream_urls:
        label = {"text": "● Live", "position": "top-left", "color": "#00ffffff", "text_color": "#ff0000"}
    elif is_live:
        label = {"text": "🔴 Chờ stream", "position": "top-left", "color": "#00ffffff", "text_color": "#ff6600"}
    else:
        label = {"text": "⏳ Chưa live", "position": "top-left", "color": "#00ffffff", "text_color": "#d54f1a"}

    return {
        "id": cid, "name": display_name, "logo_nha": logo_nha, "logo_khach": logo_khach,
        "type": "single", "display": "thumbnail-only", "enable_detail": False,
        "image": {"padding": 1, "background_color": "#ececec", "display": "contain", "url": logo_nha, "width": 1600, "height": 1200},
        "labels": [label],
        "sources": [{
            "id": cid, "name": "Hội Quán",
            "contents": [{
                "id": cid, "name": title_clean,
                "streams": [{"id": cid, "name": "F", "stream_links": [{"id": make_link_id(), "name": f"Link {i+1}", "type": "hls", "default": i==0, "url": u} for i, u in enumerate(stream_urls[:2])]}]
            }]
        }],
    }

def push_to_github(content: str):
    if not GITHUB_TOKEN:
        with open(FILE_PATH, "w", encoding="utf-8") as f: f.write(content)
        return
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    msg = "⚽ Update Hội Quán: " + datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
    except:
        repo.create_file(FILE_PATH, msg, content)

# =========================================================
# MAIN
# =========================================================
def scrape_and_push():
    print(f"🚀 START HỘI QUÁN BOT: {datetime.datetime.now(VN_TZ).strftime('%H:%M:%S %d/%m/%Y')}")
    raw_matches = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"])
        page = context.new_page()
        try: Stealth().apply_stealth_sync(page)
        except: pass

        page.goto(TARGET_SITE, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)
        raw_matches = page.evaluate(JS_EXTRACT)[:LIMIT_MATCHES]
        page.close()

        # QUÉT TẤT CẢ CÁC TRẬN TÌM THẤY
        for idx, m in enumerate(raw_matches, 1):
            print(f"   [{idx}/{len(raw_matches)}] Đang kiểm tra: {m['home']} vs {m['away']}")
            streams = capture_stream(context, m["href"])
            m["streams"] = streams
            if streams: m["isLive"] = True 

        # Đã xóa browser.close() ở đây để tránh lỗi Event loop

    channels = [build_channel(m['home'].title(), m['away'].title(), m.get('timeStr'), m.get('isLive'), m['streams'], m['href'], get_final_logo(m['home'], m['homeLogo']), get_final_logo(m['away'], m['awayLogo']), m.get('league')) for m in raw_matches]
    push_to_github(json.dumps({"id": "hoiquan", "name": "Hội Quán TV", "groups": [{"id": "live", "name": "🔴 Live bóng đá", "channels": channels}]}, indent=2, ensure_ascii=False))
    print("✅ HOÀN TẤT CẬP NHẬT!")

if __name__ == "__main__":
    scrape_and_push()
