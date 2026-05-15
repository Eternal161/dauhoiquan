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

# MÚI GIỜ VIỆT NAM (GMT+7)
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
# PARSE THỜI GIAN TỪ URL (FALLBACK)
# =========================================================
def parse_time_from_url(url: str) -> str:
    slug = url.rstrip('/').split('/')[-1]
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', slug)
    if m:
        # Tách giờ và ngày: 15:30 15/05/2026
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
    
    const anchors = Array.from(document.querySelectorAll('a[href]')).filter(a => {
        const h = a.href || '';
        return h.includes('hoiquan') && h.length > 40 && !h.includes('/lich-thi-dau');
    });

    for (const a of anchors) {
        const href = a.href;
        if (seen.has(href)) continue;
        seen.add(href);

        let league = '';
        const leagueEl = a.querySelector('[class*="league" i], [class*="tournament" i], h3, h4');
        if (leagueEl) league = clean(leagueEl.innerText);

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
        if (!timeStr) {
            const tm = clean(a.innerText).match(/(\\d{1,2}:\\d{2})\\s*(\\d{1,2}\\/\\d{1,2})?/);
            if (tm) timeStr = tm[0].trim();
        }

        results.push({ href, home, away, timeStr, league, homeLogo, awayLogo });
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
    page.on("request", lambda req: streams.add(req.url) if ".m3u8" in req.url.lower() and "/ad/" not in req.url.lower() else None)
    try:
        page.goto(match_url, wait_until="load", timeout=60000)
        page.wait_for_timeout(8000)
    except: pass
    finally: page.close()
    
    scored = []
    for s in streams:
        score = 0
        if "100ycdn.com" in s.lower(): score += 5000
        if "edgemaxcdn" in s.lower(): score += 4500
        scored.append((score, s))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [s for sc, s in scored]

# =========================================================
# BUILD CHANNEL
# =========================================================
def build_channel(m, stream_urls):
    home = (m.get('home') or "Unknown").title()
    away = (m.get('away') or "Unknown").title()
    thoi_gian = m.get('timeStr') or parse_time_from_url(m['href']) or "Không rõ"
    
    # Sửa lỗi dính chữ thời gian: 15:3015/05 -> 15:30 15/05
    thoi_gian = re.sub(r'(\d{1,2}:\d{2})(\d{1,2}/\d{2})', r'\1 \2', thoi_gian)
    
    title_clean = f"{home} vs {away}"
    display_name = f"⚽ {title_clean}"
    if m.get('league'): display_name += f" | {m['league']}"
    display_name += f" | {thoi_gian}"

    cid = make_id(m['href'])
    is_live = len(stream_urls) > 0
    
    label = {
        "text": "● Live" if is_live else "⏳ Chưa live",
        "position": "top-left",
        "color": "#00ffffff",
        "text_color": "#ff0000" if is_live else "#d54f1a"
    }

    return {
        "id": cid, "name": display_name, 
        "logo_nha": get_final_logo(home, m.get('homeLogo')), 
        "logo_khach": get_final_logo(away, m.get('awayLogo')),
        "type": "single", "display": "thumbnail-only", "enable_detail": False,
        "image": {"padding": 1, "background_color": "#ececec", "display": "contain", "url": get_final_logo(home, m.get('homeLogo')), "width": 1600, "height": 1200},
        "labels": [label],
        "sources": [{
            "id": cid, "name": "Hội Quán",
            "contents": [{
                "id": cid, "name": title_clean,
                "streams": [{"id": cid, "name": "F", "stream_links": [{"id": make_link_id(), "name": f"Link {i+1}", "type": "hls", "default": i==0, "url": u} for i, u in enumerate(stream_urls[:2])]}]
            }]
        }],
    }

# =========================================================
# MAIN SCRAPER
# =========================================================
def scrape_and_push():
    # Lấy giờ hiện tại theo VN để log cho chuẩn
    now_vn = datetime.datetime.now(VN_TZ)
    print(f"🚀 BẮT ĐẦU QUÉT (Giờ VN): {now_vn.strftime('%H:%M:%S %d/%m/%Y')}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # ÉP MÚI GIỜ VIỆT NAM CHO TRÌNH DUYỆT
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_HEADERS["User-Agent"],
            timezone_id="Asia/Ho_Chi_Minh"
        )
        
        page = context.new_page()
        try: Stealth().apply_stealth_sync(page)
        except: pass

        page.goto(TARGET_SITE, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)
        raw_matches = page.evaluate(JS_EXTRACT)[:LIMIT_MATCHES]
        page.close()

        channels = []
        for idx, m in enumerate(raw_matches, 1):
            print(f"   [{idx}/{len(raw_matches)}] Đang bắt link: {m['home']} vs {m['away']}")
            streams = capture_stream(context, m["href"])
            channels.append(build_channel(m, streams))

    # Đẩy lên GitHub
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    content = json.dumps({"id": "hoiquan", "name": "Hội Quán TV", "groups": [{"id": "live", "name": "🔴 Live bóng đá", "channels": channels}]}, indent=2, ensure_ascii=False)
    msg = f"⚽ Update Hội Quán (VN Time): {now_vn.strftime('%H:%M %d/%m/%Y')}"
    
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
    except:
        repo.create_file(FILE_PATH, msg, content)
    print("✅ HOÀN TẤT CẬP NHẬT DỮ LIỆU!")

if __name__ == "__main__":
    scrape_and_push()
