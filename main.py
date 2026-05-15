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
LIMIT_MATCHES = 15

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/dauhoiquan")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

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
def get_logo_fallback(team_name: str, site_logo: str) -> str:
    if site_logo and site_logo.startswith("http"):
        return site_logo
    
    initials = requests.utils.quote(team_name[:2] if len(team_name) >= 2 else "FC")
    return f"https://ui-avatars.com/api/?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"

# =========================================================
# PARSE TÊN ĐỘI TỪ SLUG URL (fallback)
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
        return f"{m.group(4)}:{m.group(5)} {m.group(3)}/{m.group(2)}/{m.group(1)}"
    return ""

# =========================================================
# JS: EXTRACT MATCH DATA TRỰC TIẾP TỪ DOM (ĐÃ FIX LỖI LOGO)
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
            h.includes('hoiquan3.live') &&
            h.length > 40 &&
            !h.includes('/lich-thi-dau') &&
            !h.includes('/ket-qua') &&
            !h.includes('/trang-chu') &&
            !h.includes('/xem-lai')
        );
    });

    for (const a of anchors) {
        const href = a.href;
        if (seen.has(href)) continue;
        seen.add(href);

        let league = '';
        const leagueSelectors = [
            '[class*="league" i]', '[class*="tournament" i]',
            '[class*="competition" i]', '[class*="category" i]',
            '[class*="sport-name" i]', '[class*="title" i]', 'h3', 'h4', 'h5',
        ];
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
        const isLive = /live|trực tiếp|đang phát/.test(cardText) || !!a.querySelector('[class*="live" i]');

        let home = '', away = '';
        for (const seg of href.split('/').reverse()) {
            const base = seg.split('.')[0];
            const vm = base.match(/^(.+?)-vs-(.+)$/i);
            if (vm) {
                const toTitle = s => s.replace(/-/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
                home = toTitle(vm[1]);
                away = toTitle(vm[2]);
                break;
            }
        }

        if (!home || !away) {
            const nameSelectors = [
                '.team-name', '.club-name', '[class*="team-name"]', '[class*="teamName"]',
                '[class*="home-name"]', '[class*="away-name"]', '[class*="team_name"]', '[class*="club_name"]',
                '.home .name', '.away .name',
            ];
            for (const sel of nameSelectors) {
                const els = Array.from(a.querySelectorAll(sel))
                    .map(el => clean(el.innerText))
                    .filter(t => t.length > 1 && !SKIP.test(t));
                if (els.length >= 2) {
                    home = els[0];
                    away = els[els.length - 1];
                    break;
                }
            }
        }

        // BỘ LỌC LOGO ĐỘI BÓNG THÔNG MINH
        let homeLogo = '', awayLogo = '';
        const allImgs = Array.from(a.querySelectorAll('img')).filter(i => i.src && !i.src.includes('gif') && !i.src.includes('svg'));
        
        if (allImgs.length >= 4) {
            // Có 4 ảnh: [0] Giải đấu, [1] Đội nhà, [2] Đội khách, [3] BLV
            homeLogo = allImgs[1].src;
            awayLogo = allImgs[2].src;
        } else if (allImgs.length === 3) {
            // Có 3 ảnh: [0] Giải đấu, [1] Đội nhà, [2] Đội khách
            homeLogo = allImgs[1].src;
            awayLogo = allImgs[2].src;
        } else if (allImgs.length === 2) {
            // Có 2 ảnh thì chắc chắn là đội nhà và đội khách
            homeLogo = allImgs[0].src;
            awayLogo = allImgs[1].src;
        } else if (allImgs.length === 1) {
            homeLogo = allImgs[0].src;
        }

        let timeStr = '';
        const timeEl = a.querySelector('[class*="time" i], [class*="date" i], [class*="hour" i], [class*="schedule" i], [class*="kickoff" i]');
        if (timeEl) {
            timeStr = clean(timeEl.innerText);
        }
        if (!timeStr) {
            const tm = cardText.match(/(\\d{1,2}:\\d{2})\\s*(\\d{1,2}\\/\\d{1,2}(?:\\/\\d{2,4})?)?/);
            if (tm) timeStr = tm[0].trim();
        }

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
    BAD = [
        ".gif", ".png", ".jpg", ".jpeg", ".webp", ".svg",
        ".mp4", ".mp3", ".vtt", ".srt",
        "waiting", "loop", "placeholder", "fallback", "saba.m3u8",
        "/ad/", "/ads/", "/vast/", "quangcao", "banner", "preroll", "postroll",
    ]

    def process_url(url):
        u = url.lower()
        if ".m3u8" not in u: return
        if any(b in u for b in BAD): return
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
        
        try: page.evaluate("document.querySelectorAll('*').forEach(el => { const s = window.getComputedStyle(el); if (s.position === 'fixed' && parseInt(s.zIndex||0) > 900) el.remove(); });")
        except: pass
        
        try:
            vp = page.viewport_size
            if vp:
                cx, cy = vp["width"] // 2, vp["height"] // 2
                for _ in range(2):
                    page.mouse.click(cx, cy)
                    page.wait_for_timeout(1000)
        except: pass
        
        for frame in page.frames:
            try: frame.evaluate("document.querySelectorAll('video').forEach(v => { v.muted=true; v.play().catch(()=>{}); });")
            except: pass
            
        deadline = time.time() + 20
        while time.time() < deadline:
            if any("100ycdn" in s.lower() or "edgemaxcdn" in s.lower() or "wssession=" in s.lower() for s in streams):
                break
            time.sleep(1)
            
        try:
            for url in re.findall(r'https?://[^\s"\'<>]+\.m3u8(?:[^\s"\'<>]*)?', page.content(), re.I):
                process_url(url)
        except: pass
        
    except PWTimeout: print("      ⚠️ TIMEOUT")
    except Exception as e: print(f"      ❌ {e}")
    finally: page.close()

    if not streams: return []

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
# BUILD JSON
# =========================================================
def build_channel(home: str, away: str, thoi_gian: str, is_live: bool,
                  stream_urls: list, match_url: str, logo_nha: str, logo_khach: str,
                  league: str = "") -> dict:
    cid = make_id(match_url)
    title_clean  = f"{home} vs {away}"
    parts = ["⚽ " + title_clean]
    if league: parts.append(league)
    if thoi_gian: parts.append(thoi_gian)
    display_name = " | ".join(parts)

    if is_live and stream_urls:
        label = {"text": "● Live", "position": "top-left", "color": "#00ffffff", "text_color": "#ff0000"}
    elif is_live:
        label = {"text": "🔴 Chờ stream", "position": "top-left", "color": "#00ffffff", "text_color": "#ff6600"}
    else:
        label = {"text": "⏳ Chưa live", "position": "top-left", "color": "#00ffffff", "text_color": "#d54f1a"}

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
        "logo_nha": logo_nha,      
        "logo_khach": logo_khach,  
        "type": "single",
        "display": "thumbnail-only",
        "enable_detail": False,
        "image": {
            "padding": 1,
            "background_color": "#ececec",
            "display": "contain",
            "url": logo_nha,
            "width": 1600,
            "height": 1200,
        },
        "labels": [label],
        "sources": [{
            "id": cid,
            "name": "Hội Quán",
            "contents": [{
                "id": cid,
                "name": title_clean,
                "streams": [{
                    "id": cid,
                    "name": "F",
                    "stream_links": stream_links,
                }]
            }]
        }],
    }

def build_json(channels: list) -> dict:
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
        "groups": [{
            "id": "live",
            "name": "🔴 Live bóng đá",
            "display": "vertical",
            "grid_number": 2,
            "enable_detail": False,
            "channels": channels,
        }],
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

    raw_matches = []

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
            timezone_id="Asia/Ho_Chi_Minh", 
        )

        print(f"\n📺 QUÉT: {TARGET_SITE}")
        page = context.new_page()
        try: Stealth().apply_stealth_sync(page)
        except: pass

        try: page.goto(TARGET_SITE, wait_until="networkidle", timeout=60000)
        except: 
            try: page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
            except: pass

        page.wait_for_timeout(5000)

        for _ in range(5):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(700)

        try:
            raw_matches = page.evaluate(JS_EXTRACT)
            print(f"   JS extract: {len(raw_matches)} trận")
        except Exception as e:
            print(f"   ❌ JS lỗi: {e}")
            raw_matches = []

        page.close()

        for m in raw_matches:
            h = (m.get("home") or "").strip()
            a = (m.get("away") or "").strip()
            if not h or not a or h == a or len(h) < 2:
                slug = m["href"].rstrip("/").split("/")[-1]
                fh, fa = parse_teams_from_title(slug)
                m["home"] = fh
                m["away"] = fa

        if LIMIT_MATCHES:
            raw_matches = raw_matches[:LIMIT_MATCHES]

        for m in raw_matches:
            tg = m.get("timeStr") or parse_time_from_url(m["href"])
            m["timeStr"] = tg
            if not m.get("isLive") and tg:
                for fmt in ("%H:%M %d/%m/%Y", "%H:%M %d/%m"):
                    try:
                        mt = datetime.datetime.strptime(tg.strip(), fmt)
                        if fmt == "%H:%M %d/%m":
                            mt = mt.replace(year=datetime.datetime.now(VN_TZ).year)
                        mt = mt.replace(tzinfo=VN_TZ)
                        diff = (datetime.datetime.now(VN_TZ) - mt).total_seconds() / 60
                        if -10 <= diff <= 120: m["isLive"] = True
                        break
                    except: pass

        live = [m for m in raw_matches if m.get("isLive")]
        print(f"\n🎥 BẮT STREAM {len(live)} TRẬN LIVE...")

        for m in raw_matches: m["streams"] = []

        for idx, m in enumerate(live, 1):
            print(f"\n   [{idx}/{len(live)}] {m['home']} vs {m['away']}")
            streams = capture_stream(context, m["href"])
            m["streams"] = streams
            print(f"      {'✅' if streams else '⚠️'} {len(streams)} stream")

        browser.close()

    channels = []
    for m in raw_matches:
        home      = (m.get("home") or "Unknown").strip().title()
        away      = (m.get("away") or "Unknown").strip().title()
        thoi_gian = m.get("timeStr") or "Không rõ"
        is_live   = m.get("isLive", False)
        league    = (m.get("league") or "").strip()
        
        logo_nha   = get_logo_fallback(home, m.get("homeLogo"))
        logo_khach = get_logo_fallback(away, m.get("awayLogo"))

        ch = build_channel(
            home=home, away=away,
            thoi_gian=thoi_gian, is_live=is_live,
            stream_urls=m["streams"],
            match_url=m["href"],
            logo_nha=logo_nha,
            logo_khach=logo_khach,
            league=league,
        )
        channels.append(ch)

    output  = build_json(channels)
    content = json.dumps(output, indent=2, ensure_ascii=False)
    push_to_github(content)

    print("\n" + "=" * 70)
    total_live    = sum(1 for m in raw_matches if m.get("isLive"))
    total_streams = sum(1 for m in raw_matches if m.get("streams"))
    print(f"✅ HOÀN TẤT: {len(channels)} trận | {total_live} live | {total_streams} có stream")
    print("=" * 70)

if __name__ == "__main__":
    scrape_and_push()
