import os
import re      # <-- Đã vá lỗi thiếu thư viện RegExp
import time
import json
import datetime
import requests
from github import Github, Auth  # <-- Đã nâng cấp chuẩn đăng nhập Github mới
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# =========================================================
# CONFIG BOT TRUYỆN - CÀO TIẾP NỐI & KHÔNG XÓA DỮ LIỆU CŨ
# =========================================================
DANH_SACH_TRUYEN = [
    "https://damconuong.lol/truyen/thuc-tap-o-lang-tien-ca/chapter-1",
    "https://damconuong.lol/truyen/truyen-lo-da-mo/chapter-1",
    "https://damconuong.lol/truyen/phich-cay-the-gioi-2/chapter-1",
    "https://damconuong.lol/truyen/truyen-toi-phai-lam-gi-bay-gio/chapter-1",
    "https://damconuong.lol/truyen/vong-xoay-chi-em/chapter-1",
    "https://damconuong.lol/truyen/sextoy-ket-noi-khong-day/chapter-1",
    "https://damconuong.lol/truyen/truyen-roi-vao-the-gioi-tro-choi/chapter-1", # Bạn thay bằng link truyện của bạn
]

FILE_PATH = "truyenbimat.json"
VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dauhoiquan") 

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

JS_GET_INFO = """
() => {
    let title = document.querySelector('h1')?.innerText?.trim() || document.title.split('-')[0].trim();
    let img = document.querySelector('.detail-info img, .book-info img, .manga-info img, img');
    let thumb = img ? (img.getAttribute('data-src') || img.src) : '';
    let status = 'Đang cập nhật'; 
    return { title, thumb, status };
}
"""

JS_GET_CHAPTERS = """
() => {
    let chaps = [];
    let seen = new Set();
    let links = document.querySelectorAll('a');
    for (let a of links) {
        let txt = a.innerText.trim();
        if (/^(chapter|chap|chương)\s*\d+(\.\d+)?/i.test(txt)) {
            if (a.href && !a.href.includes('javascript:') && !seen.has(a.href)) {
                seen.add(a.href);
                chaps.push({ name: txt, href: a.href });
            }
        }
    }
    chaps.sort((a, b) => {
        let numA = parseFloat(a.name.match(/\d+(\.\d+)?/)[0]);
        let numB = parseFloat(b.name.match(/\d+(\.\d+)?/)[0]);
        return numA - numB;
    });
    return chaps;
}
"""

JS_GET_IMAGES = """
() => {
    let urls = [];
    let imgs = document.querySelectorAll('.reading-detail img, .page-chapter img, .box_doc img, .chapter-detail img, img');
    for (let img of imgs) {
        let src = img.getAttribute('data-original') || img.getAttribute('data-src') || img.src;
        if (src) {
            src = src.trim();
            // Lọc siêu mạnh chống quảng cáo
            let isAd = src.includes('logo') || src.includes('avatar') || src.includes('banner') ||
                       src.includes('.gif') || src.includes('728x90') || src.includes('yylive') || 
                       src.includes('damconuong') || src.includes('dcn-gold');
            if (src.startsWith('http') && !isAd) {
                urls.push(src);
            }
        }
    }
    return urls;
}
"""

def load_existing_data():
    """Tải file từ GitHub về, nếu lỗi thì coi như mảng rỗng"""
    if not GITHUB_TOKEN:
        return {"comics": []}
    try:
        # Chuẩn đăng nhập Github mới không bị cảnh báo Deprecation
        auth = Auth.Token(GITHUB_TOKEN)
        repo = Github(auth=auth).get_repo(REPO_NAME)
        file_content = repo.get_contents(FILE_PATH)
        data = json.loads(file_content.decoded_content.decode('utf-8'))
        print(f"📥 Đã tải kho dữ liệu cũ: Có sẵn {len(data.get('comics', []))} bộ truyện. - caotruyen.py:97")
        return data
    except:
        print("📭 File chưa tồn tại hoặc lỗi JSON, sẽ tạo file trống. - caotruyen.py:100")
        return {"comics": []}

def parseFloat_safe(text):
    match = re.search(r'\d+(\.\d+)?', text)
    return float(match.group()) if match else 0.0

def scrape_and_update():
    now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT CÀO TRUYỆN (Giờ VN): {now_str} - caotruyen.py:109")

    existing_data = load_existing_data()
    existing_comics = existing_data.get("comics", [])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"])
        
        for comic_url in DANH_SACH_TRUYEN:
            print(f"\n📺 Đang quét truyện tại: {comic_url} - caotruyen.py:119")
            page = context.new_page()
            Stealth().apply_stealth_sync(page)
            
            try:
                page.goto(comic_url, wait_until="load", timeout=60000)
                page.wait_for_timeout(3000)
                
                info = page.evaluate(JS_GET_INFO)
                title = info['title']
                print(f"📖 Tên truyện: {title} - caotruyen.py:129")

                all_chaps = page.evaluate(JS_GET_CHAPTERS)
                if not all_chaps:
                    print("⚠️ Không tìm thấy danh sách Chapter! - caotruyen.py:133")
                    page.close()
                    continue

                comic_entry = next((c for c in existing_comics if c["title"] == title), None)
                
                if comic_entry is None:
                    comic_entry = {
                        "title": title,
                        "status": all_chaps[-1]['name'],
                        "thumb_url": info['thumb'],
                        "chapters": []
                    }
                    existing_comics.append(comic_entry)
                    existing_chap_names = []
                else:
                    comic_entry["status"] = all_chaps[-1]['name']
                    if info['thumb']: comic_entry["thumb_url"] = info['thumb']
                    existing_chap_names = [c["name"] for c in comic_entry.get("chapters", [])]

                chaps_to_scrape = [c for c in all_chaps if c['name'] not in existing_chap_names]

                if not chaps_to_scrape:
                    print(f"✅ Truyện đã đầy đủ {len(all_chaps)} chap. Không cần cào thêm! - caotruyen.py:156")
                    page.close()
                    continue
                
                print(f"👉 Truyện có tổng {len(all_chaps)} chap. Phát hiện {len(chaps_to_scrape)} chap MỚI cần cào. - caotruyen.py:160")

                for idx, chap in enumerate(chaps_to_scrape, 1):
                    print(f"[{idx}/{len(chaps_to_scrape)}] Đang cào {chap['name']}... - caotruyen.py:163")
                    try:
                        page.goto(chap['href'], wait_until="load", timeout=30000)
                        for _ in range(10):
                            page.mouse.wheel(0, 800)
                            page.wait_for_timeout(500)
                            
                        images = page.evaluate(JS_GET_IMAGES)
                        if images:
                            print(f"✅ Tóm được {len(images)} ảnh. - caotruyen.py:172")
                            comic_entry["chapters"].append({"name": chap['name'], "images": images})
                        else:
                            print(f"⚠️ Trắng tay, không thấy ảnh. - caotruyen.py:175")
                    except Exception as e:
                        print(f"❌ Lỗi cào chap: {e} - caotruyen.py:177")

                comic_entry["chapters"].sort(key=lambda x: parseFloat_safe(x["name"]))

            except Exception as e:
                print(f"❌ Lỗi xử lý truyện: {e} - caotruyen.py:182")
            finally:
                page.close()

        browser.close()

    print("\n🎉 HOÀN TẤT CÀO TRUYỆN! Bắt đầu tải lên GitHub... - caotruyen.py:188")
    
    output_data = {"comics": existing_comics}
    content = json.dumps(output_data, indent=2, ensure_ascii=False)
    
    # LƯỚI BẢO VỆ CHỐNG LỖI 422: Check lại SHA một lần nữa trước khi đẩy
    if GITHUB_TOKEN:
        try:
            auth = Auth.Token(GITHUB_TOKEN)
            repo = Github(auth=auth).get_repo(REPO_NAME)
            msg = "📖 Auto-Update Truyện Tranh: " + now_str
            try:
                # Thử tìm file cũ xem có trên Github không
                existing = repo.get_contents(FILE_PATH)
                repo.update_file(existing.path, msg, content, existing.sha)
                print(f"✅ Đã CẬP NHẬT GHI ĐÈ thành công lên {REPO_NAME}/{FILE_PATH} - caotruyen.py:203")
            except:
                # Nếu chắc chắn không có mới tạo mới
                repo.create_file(FILE_PATH, msg, content)
                print(f"✅ Đã TẠO MỚI thành công file {FILE_PATH} trên GitHub! - caotruyen.py:207")
        except Exception as e:
            print(f"❌ Lỗi khi thao tác với GitHub: {e} - caotruyen.py:209")
    else:
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✅ Đã lưu file cục bộ: {FILE_PATH} - caotruyen.py:213")

if __name__ == "__main__":
    scrape_and_update()
