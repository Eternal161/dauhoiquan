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
# CONFIG HỘI QUÁN TV - BẢN FULL CHỐNG LAG MẠNG
# =========================================================
TARGET_SITE   = "https://sv2.hoiquan4.live/lich-thi-dau/bong-da"
BASE_URL      = "https://sv2.hoiquan4.live"
FILE_PATH     = "hoiquan.json"
LIMIT_MATCHES = 10

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/dauhoiquan")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

LOGO_CACHE = {}

# =========================================================
# HELPER
# =========================================================
def make_id(seed: str = "") -> str:
    h = hashlib.md5((seed or str(uuid.uuid4())).encode()).hexdigest()
    return f"kaytee-{h[:12]}"

def make_link_id() -> str:
    return "lnk-" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:10]

def get_final_logo(team_name: str, site_logo: str) -> str:
    if site_logo and site_logo.startswith("http"): return site_logo
    initials = requests.utils.quote(team_name[:2] if len(team_name) >= 2 else "FC")
    return f"https://ui-avatars.com/api/?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"

# =========================================================
# PARSE THỜI GIAN (CỘNG 7H CHO HỘI QUÁN)
# =========================================================
def parse_time_from_url(url: str) -> str:
    try:
        slug = url.rstrip('/').split('/')[-1]
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', slug)
        if m:
            y, mth, d, h, mn = map(int, m.groups())
            if mth > 12: mth, d = d, mth
            dt_utc = datetime.datetime(y, mth, d, h, mn)
            dt_vn = dt_utc + datetime.timedelta(hours=7)
            return dt_vn.strftime("%H:%M %d/%m/%Y")
    except: pass
    return ""

# =========================================================
# JS: EXTRACT DATA
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

        let home = '', away = '', homeLogo = '', awayLogo = '';
        const gridBox = a.querySelector('div[class*="grid-cols-[1fr_auto_1fr]"]');
        
        if (gridBox && gridBox.children.length >= 3) {
            home = clean(gridBox.children[0].innerText);
            away = clean(gridBox.children[2].innerText);
            const imgNha = gridBox.children[0].querySelector('img');
            const imgKhach = gridBox.children[2].querySelector('img');
            if (imgNha) homeLogo = imgNha.src;
            if (imgKhach) awayLogo = imgKhach.src;
        }

        let timeStr = '';
        if (gridBox && gridBox.children[1]) {
            const centerText = clean(gridBox.children[1].innerText);
            if (centerText && centerText.toUpperCase() !== 'VS' && centerText.length > 0) {
                timeStr = centerText.replace(/\\n/g, ' • ');
            }
        }

        if (!timeStr) {
            const timeEl = a.querySelector('[class*="time" i], [class*="date" i]');
            if (timeEl) timeStr = clean(timeEl.innerText);
        }

        const isLive = /live|trực tiếp|đang phát/.test(clean(a.innerText).toLowerCase());

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
    page.on("request", lambda req: streams.add(req.url) if ".m3u8" in req.url.lower() and "/ad/" not in req.url.lower() else None)
    
    try:
        page.goto(match_url, wait_until="load", timeout=60000)
        page.wait_for_timeout(8000)
    except PWTimeout:
        print("      ⚠️ Web lag nhẹ nhưng vẫn tiếp tục bắt link...")
    except Exception:
        pass
    finally:
        page.close()
    
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
    thoi_gian = m.get('timeStr') or "Không rõ"
    
    # Sửa dính chữ
    thoi_gian = re.sub(r'(\d{1,2}:\d{2})(\d{1,2}/\d{2})', r'\1 \2', thoi_gian)
    
    title_clean = f"{home} vs {away}"
    display_name = f"⚽ {title_clean}" + (f" | {m.get('league')}" if m.get('league') else "") + f" | {thoi_gian}"

    cid = make_id(m['href'])
    is_live = len(stream_urls) > 0
    
    label_text = "● Live" if is_live else ("🔴 Chờ stream" if m.get('isLive') else "⏳ Chưa live")
    label_color = "#ff0000" if is_live else ("#ff6600" if m.get('isLive') else "#d54f1a")

    return {
        "id": cid, "name": display_name, 
        "logo_nha": get_final_logo(home, m.get('homeLogo')), 
        "logo_khach": get_final_logo(away, m.get('awayLogo')),
        "type": "single", "display": "thumbnail-only", "enable_detail": False,
        "image": {"padding": 1, "background_color": "#ececec", "display": "contain", "url": get_final_logo(home, m.get('homeLogo')), "width": 1600, "height": 1200},
        "labels": [{"text": label_text, "position": "top-left", "color": "#00ffffff", "text_color": label_color}],
        "sources": [{
            "id": cid, "name": "Hội Quán",
            "contents": [{
                "id": cid, "name": title_clean,
                "streams": [{"id": cid, "name": "F", "stream_links": [{"id": make_link_id(), "name": f"Link {i+1}", "type": "hls", "default": i==0, "url": u} for i, u in enumerate(stream_urls[:2])]}]
            }]
        }],
    }

# =========================================================
# CHƯƠNG TRÌNH CHÍNH
# =========================================================
def scrape_and_push():
    now_vn = datetime.datetime.now(VN_TZ)
    now_str = now_vn.strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT HỘI QUÁN (Giờ VN): {now_str}")

    with sync_playwright() as p:
        # THÊM CỜ BẢO VỆ CHỐNG SẬP TRÌNH DUYỆT TRÊN GITHUB ACTIONS
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"], timezone_id="Asia/Ho_Chi_Minh")
        page = context.new_page()
        try: Stealth().apply_stealth_sync(page)
        except: pass
        
        # BỌC GIÁP CHỐNG LỖI TIMEOUT TRANG CHỦ
        try:
            print("📺 Đang mở trang Hội Quán...")
            page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
        except PWTimeout:
            print("   ⚠️ Web load quá chậm (quá 60s). Đang ép Bot cào tiếp...")
        except Exception as e:
            print(f"   ⚠️ Có sự cố mạng nhỏ: {e}")
            
        page.wait_for_timeout(5000)
        
        raw_matches = page.evaluate(JS_EXTRACT)
        
        valid_matches = []
        seen_keys = set()
        for m in raw_matches:
            h_lower = (m.get('home') or "").lower()
            a_lower = (m.get('away') or "").lower()
            if not h_lower or not a_lower or "unknown" in h_lower or "hoiquan" in h_lower: continue
            
            key = f"{h_lower} vs {a_lower}"
            if key not in seen_keys:
                seen_keys.add(key)
                valid_matches.append(m)

        raw_matches = valid_matches[:LIMIT_MATCHES]
        channels = []
        for idx, m in enumerate(raw_matches, 1):
            m["timeStr"] = m.get("timeStr") or parse_time_from_url(m["href"]) or "Chưa rõ"
            print(f"   [{idx}/{len(raw_matches)}] {m['home']} vs {m['away']} ({m['timeStr']})")
            
            streams = capture_stream(context, m["href"])
            channels.append(build_channel(m, streams))

    # Đẩy lên GitHub
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    content = json.dumps({
        "id": "hoiquan", "name": "Hội Quán TV", "last_updated": now_str, 
        "groups": [{"id": "live", "name": "🔴 Live bóng đá", "channels": channels}]
    }, indent=2, ensure_ascii=False)
    
    msg = f"⚽ Update Hội Quán (VN Time): {now_str}"
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
    except:
        repo.create_file(FILE_PATH, msg, content)
    print("\n✅ HOÀN TẤT CẬP NHẬT!")

if __name__ == "__main__":
    scrape_and_push()
