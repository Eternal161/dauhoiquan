import os
import re
import time
import json
import datetime
import requests
import traceback

from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# =========================================================
# CONFIG HỘI QUÁN TV
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
    if not team_name or team_name == "Unknown": return ""
    team_name = normalize_team_name(team_name)
    if team_name in LOGO_CACHE: return LOGO_CACHE[team_name]
    try:
        slug = team_name.lower().replace(" ", "-")
        r = requests.get(f"https://football-logos.cc/{slug}/", headers=_HEADERS, timeout=5)
        match = re.search(r'https://football-logos.cc/logos/[^"]+\.png', r.text)
        if match:
            logo = match.group(0)
            LOGO_CACHE[team_name] = logo
            return logo
    except: pass
    return f"https://ui-avatars.com/api/?name={requests.utils.quote(team_name[:2])}&size=200&background=1565C0&color=ffffff&bold=true"

# =========================================================
# PARSE MATCH TỪ URL (AN TOÀN HƠN PARSE TỪ HTML)
# =========================================================
def parse_url_to_info(url):
    try:
        parts = url.rstrip('/').split('/')
        slug = ""
        for p in reversed(parts):
            if "-vs-" in p:
                slug = p.split('?')[0].split('#')[0]
                break
        if not slug: return "Unknown", "Unknown", "Chưa có lịch"

        # Loại bỏ các chuỗi id ở cuối (ví dụ: /601445470)
        slug = re.sub(r'-\d{6,}$', '', slug)
        
        # Bắt cụm thời gian theo định dạng dd-mm-yyyy-HHmm
        time_match = re.search(r"-(\d{4}-\d{2}-\d{2}-\d{4})$", slug)
        if time_match:
            t = time_match.group(1)
            thoi_gian = f"{t[0:2]}:{t[2:4]} {t[5:7]}/{t[8:10]}/{t[11:15]}"
            teams_slug = slug[:slug.rfind("-" + t)]
        else:
            thoi_gian, teams_slug = "Unknown", slug

        teams = teams_slug.split("-vs-", 1)
        doi_nha = teams[0].replace("-", " ").title().strip()
        doi_khach = teams[1].replace("-", " ").title().strip() if len(teams) > 1 else "Unknown"
        return doi_nha, doi_khach, thoi_gian
    except:
        return "Unknown", "Unknown", "Unknown"

# =========================================================
# CAPTURE STREAM (SÁT THỦ BẮT LINK HỘI QUÁN)
# =========================================================
def capture_stream(context, match_url):
    page = context.new_page()
    Stealth().apply_stealth_sync(page)
    streams = set()

    def process_url(url):
        u = url.lower()
        if any(bad in u for bad in [".mp4", ".jpg", ".png", "waiting", "loop", "saba.m3u8", "/ad/", "/ads/", "/vast/", "quangcao", "banner"]):
            return
        # Tóm chặt các tên miền liên quan đến Hội Quán TV
        if ".m3u8" in u or "100ycdn.com" in u or "edgemaxcdn.org" in u or "hqtv" in u:
            streams.add(url)
            print(f"      🎯 TÓM ĐƯỢC: {url[:70]}...")

    page.on("request", lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    try:
        page.add_init_script("""
        (() => {
            const origFetch = window.fetch;
            window.fetch = async (...args) => {
                if (typeof args[0] === 'string') console.log('FETCH:', args[0]);
                return origFetch(...args);
            };
        })();
        """)
        
        page.goto(match_url, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)

        # Cào mã HTML để phòng hờ web giấu link
        try:
            html_content = page.content()
            found = re.findall(r'(https?://[^\s"\'<>]+(?:m3u8|edgemaxcdn\.org|100ycdn\.com|hqtv)[^\s"\'<>]*)', html_content)
            for f in found: process_url(f.replace('\\/', '/'))
        except: pass

        # Xóa popup chặn click
        try:
            page.evaluate("""
            document.querySelectorAll('*').forEach(el => {
                const s = window.getComputedStyle(el);
                if (s.position === 'fixed' && parseInt(s.zIndex) > 900) el.remove();
            });
            """)
        except: pass

        # Click phá quảng cáo
        try:
            vp = page.viewport_size
            if vp:
                cx, cy = vp["width"] // 2, vp["height"] // 2
                for _ in range(3):
                    page.mouse.click(cx, cy)
                    page.wait_for_timeout(800)
        except: pass

        # Ép tất cả Iframe phát video
        for frame in page.frames:
            try:
                frame.evaluate("""
                document.querySelectorAll('video').forEach(v => {
                    v.muted = true; v.play().catch(()=>{});
                });
                """)
            except: pass

        deadline = time.time() + 15
        while time.time() < deadline:
            # Nhận thấy token xịn của Hội Quán là ngắt luôn vòng lặp cho tiết kiệm thời gian
            if any("100ycdn" in s.lower() or "edgemaxcdn" in s.lower() or "wssession=" in s.lower() for s in streams):
                break
            time.sleep(1)

    except PWTimeout: print("      ⚠️ TIMEOUT TRANG")
    except Exception as e: print("      ❌ STREAM ERROR:", e)
    finally: page.close()

    # BỘ LỌC ĐIỂM CHUYÊN TRỊ HỘI QUÁN
    if streams:
        priority = []
        for s in streams:
            score = 0
            lower = s.lower()
            if any(bad in lower for bad in ["waiting", "loop", "placeholder", "fallback", "saba.m3u8"]): score -= 5000
            if "100ycdn.com" in lower: score += 6000
            if "edgemaxcdn.org" in lower or "hqtv" in lower: score += 5000
            if any(k in lower for k in ["expire=", "sign=", "token=", "wssession="]): score += 1000
            if "playlist.m3u8" in lower: score += 500
            elif "index.m3u8" in lower or "chunklist" in lower: score += 200
            if any(bad in lower for bad in ["/ad/", "/ads/", "/vast/", "quangcao", "preroll", "banner"]): score -= 10000
            priority.append((score, s))

        priority.sort(reverse=True, key=lambda x: x[0])
        best_score, best_url = priority[0]
        if best_score > -5000:
            print(f"      ✅ CHỐT LINK CHUẨN: {best_url[:70]}...")
            return best_url
        else:
            print("      ⚠️ CHỈ TÌM THẤY LUỒNG CHỜ/RÁC")
    return None

# =========================================================
# JSON & GITHUB
# =========================================================
def create_json(matches):
    total_live = sum(1 for m in matches if m.get("is_live"))
    total_streams = sum(1 for m in matches if m.get("stream_url") and m["stream_url"] != WAITING_VIDEO_URL)
    data = {
        "playlist_name": "Sáng TV (Hội Quán)",
        "last_updated": datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y"),
        "total_live": total_live,
        "total_streams": total_streams,
        "matches": matches
    }
    return json.dumps(data, indent=2, ensure_ascii=False)

def push_to_github(content):
    if not GITHUB_TOKEN:
        print("⚠️ KHÔNG CÓ GH_TOKEN, ĐANG LƯU VÀO MÁY (LOCAL)...")
        with open(FILE_PATH, "w", encoding="utf-8") as f: f.write(content)
        return
    try:
        g, repo = Github(GITHUB_TOKEN), Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        msg = "⚽ Update Hội Quán: " + datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
        try:
            existing = repo.get_contents(FILE_PATH)
            repo.update_file(existing.path, msg, content, existing.sha)
            print(f"✅ Đã CẬP NHẬT Github: {FILE_PATH}")
        except:
            repo.create_file(FILE_PATH, msg, content)
            print(f"✅ Đã TẠO MỚI trên Github: {FILE_PATH}")
    except Exception as e:
        print(f"❌ LỖI ĐẨY GITHUB: {e}")
        with open(FILE_PATH, "w", encoding="utf-8") as f: f.write(content)

# =========================================================
# MAIN - HỘI QUÁN TV
# =========================================================
def scrape_and_push():
    matches_data = []
    print("=" * 70)
    print(datetime.datetime.now(VN_TZ).strftime("START HỘI QUÁN BOT %H:%M:%S %d/%m/%Y"))
    print("=" * 70)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--autoplay-policy=no-user-gesture-required"]
            )
            context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"], ignore_https_errors=True)

            print("\n📺 ĐANG QUÉT KÊNH: HỘI QUÁN TV")
            page = context.new_page()
            Stealth().apply_stealth_sync(page)

            try:
                page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
            except: pass

            # Cuộn trang load ảnh
            for _ in range(4):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(1000)

            links, seen = [], set()
            
            # Quét tìm URL trận đấu (Chắc chắn đúng 100%)
            for el in page.locator("a[href*='-vs-']").all():
                href = el.get_attribute("href")
                if not href or "-vs-" not in href or href in seen: continue
                seen.add(href)
                if not href.startswith("http"): href = BASE_URL.rstrip('/') + '/' + href.lstrip('/')
                links.append(href)

            if LIMIT_MATCHES: links = links[:LIMIT_MATCHES]
            print(f"   ✅ TÌM THẤY {len(links)} TRẬN ĐẤU")

            for idx, href in enumerate(links):
                doi_nha, doi_khach, thoi_gian = parse_url_to_info(href)
                is_live, status = False, "Chưa đá ⏳"
                try:
                    match_time = datetime.datetime.strptime(thoi_gian, "%H:%M %d/%m/%Y").replace(tzinfo=VN_TZ)
                    diff_minutes = (datetime.datetime.now(VN_TZ) - match_time).total_seconds() / 60
                    if -10 <= diff_minutes <= 120: is_live, status = True, "Đang trực tiếp 🔴"
                    elif diff_minutes > 120: status = "Đã kết thúc 🏁"
                except: pass

                print(f"   [{idx+1}] {'🔴' if is_live else '⚪'} {doi_nha} vs {doi_khach}")
                matches_data.append({
                    "id": str(idx + 1), "title": f"{doi_nha} vs {doi_khach}", "doi_nha": doi_nha, "doi_khach": doi_khach,
                    "thoi_gian": thoi_gian, "trang_thai": status, "is_live": is_live,
                    "logo_nha": get_team_logo(doi_nha), "logo_khach": get_team_logo(doi_khach),
                    "stream_url": WAITING_VIDEO_URL, "link_xem": href
                })
            page.close()

            print("\n🎥 TIẾN HÀNH BẮT LUỒNG...")
            live_matches = [m for m in matches_data if m["is_live"]]
            for idx, match in enumerate(live_matches):
                print(f"\n   [{idx+1}/{len(live_matches)}] Cào link: {match['title']}")
                stream = capture_stream(context, match["link_xem"])
                if stream: match["stream_url"] = stream

            browser.close()
            
    except Exception as err:
        print(f"❌ LỖI NGHIÊM TRỌNG: {err}")
        traceback.print_exc()
        
    finally:
        push_to_github(create_json(matches_data))
        print("\n" + "=" * 70 + "\n✅ HOÀN TẤT HỘI QUÁN TV\n" + "=" * 70)

if __name__ == "__main__":
    scrape_and_push()
