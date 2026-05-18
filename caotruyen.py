import os
import time
import json
import datetime
import requests
from github import Github
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# =========================================================
# CONFIG BOT TRUYỆN - CÀO TIẾP NỐI & KHÔNG XÓA DỮ LIỆU CŨ
# =========================================================
# Thay bằng link các bộ truyện bạn muốn cào
DANH_SACH_TRUYEN = [
    "https://damconuong.lol/truyen/vong-xoay-chi-em/chapter-1",
    # "https://...", Thêm bao nhiêu link cũng được
]

FILE_PATH = "truyenbimat.json"
VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dauhoiquan") # Lưu vào kho dauhoiquan

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# =========================================================
# JS BÓC TÁCH THÔNG TIN TRUYỆN & CHAPTER
# =========================================================
JS_GET_INFO = """
() => {
    let title = document.querySelector('h1')?.innerText?.trim() || document.title.split('-')[0].trim();
    let img = document.querySelector('.detail-info img, .book-info img, .manga-info img, img');
    let thumb = img ? (img.getAttribute('data-src') || img.src) : '';
    let status = 'Đang cập nhật'; // Có thể nâng cấp bắt status chính xác sau
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
        // Bắt các thẻ có chữ Chapter, Chap, hoặc Chương
        if (/^(chapter|chap|chương)\s*\d+(\.\d+)?/i.test(txt)) {
            if (a.href && !a.href.includes('javascript:') && !seen.has(a.href)) {
                seen.add(a.href);
                chaps.push({ name: txt, href: a.href });
            }
        }
    }
    // Sắp xếp lại từ Chap 1 -> Chap N
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
    // Lấy tất cả ảnh trong khu vực đọc truyện
    let imgs = document.querySelectorAll('.reading-detail img, .page-chapter img, .box_doc img, .chapter-detail img, img');
    for (let img of imgs) {
        let src = img.getAttribute('data-original') || img.getAttribute('data-src') || img.src;
        // Lọc bỏ icon, logo rác
        if (src && src.startsWith('http') && !src.includes('logo') && !src.includes('avatar') && !src.includes('banner')) {
            urls.push(src.trim());
        }
    }
    return urls;
}
"""

def load_existing_data():
    """Tải file truyenbimat.json từ GitHub về để đối chiếu"""
    if not GITHUB_TOKEN:
        print("⚠️ Không có GH_TOKEN, Bot sẽ tạo mới toàn bộ dữ liệu (Local).  Untitled1:85 - caotruyen.py:85")
        return {"comics": []}, None
    try:
        repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        try:
            file_content = repo.get_contents(FILE_PATH)
            data = json.loads(file_content.decoded_content.decode('utf-8'))
            print(f"📥 Đã tải kho dữ liệu cũ: Có sẵn {len(data.get('comics', []))} bộ truyện.  Untitled1:92 - caotruyen.py:92")
            return data, file_content.sha
        except:
            print("📭 File chưa tồn tại trên GitHub, sẽ tạo mới.  Untitled1:95 - caotruyen.py:95")
            return {"comics": []}, None
    except Exception as e:
        print(f"❌ Lỗi kết nối GitHub: {e}  Untitled1:98 - caotruyen.py:98")
        return {"comics": []}, None

def scrape_and_update():
    now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT CÀO TRUYỆN (Giờ VN): {now_str}  Untitled1:103 - caotruyen.py:103")

    # 1. Tải dữ liệu cũ về
    existing_data, file_sha = load_existing_data()
    existing_comics = existing_data.get("comics", [])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"])
        
        for comic_url in DANH_SACH_TRUYEN:
            print(f"\n📺 Đang quét truyện tại: {comic_url}  Untitled1:114 - caotruyen.py:114")
            page = context.new_page()
            Stealth().apply_stealth_sync(page)
            
            try:
                page.goto(comic_url, wait_until="load", timeout=60000)
                page.wait_for_timeout(6000)
                
                info = page.evaluate(JS_GET_INFO)
                title = info['title']
                print(f"📖 Tên truyện: {title}  Untitled1:124 - caotruyen.py:124")

                all_chaps = page.evaluate(JS_GET_CHAPTERS)
                if not all_chaps:
                    print("⚠️ Không tìm thấy danh sách Chapter!  Untitled1:128 - caotruyen.py:128")
                    page.close()
                    continue

                # 2. Tìm truyện này trong kho dữ liệu cũ
                comic_entry = next((c for c in existing_comics if c["title"] == title), None)
                is_new_comic = False
                
                if comic_entry is None:
                    is_new_comic = True
                    comic_entry = {
                        "title": title,
                        "status": all_chaps[-1]['name'], # Gắn status là chap mới nhất
                        "thumb_url": info['thumb'],
                        "chapters": []
                    }
                    existing_comics.append(comic_entry)
                    existing_chap_names = []
                else:
                    comic_entry["status"] = all_chaps[-1]['name'] # Cập nhật status mới
                    if info['thumb']: comic_entry["thumb_url"] = info['thumb']
                    existing_chap_names = [c["name"] for c in comic_entry.get("chapters", [])]

                # 3. Lọc ra các chapter CẦN CÀO (Chưa có trong JSON)
                chaps_to_scrape = [c for c in all_chaps if c['name'] not in existing_chap_names]

                if not chaps_to_scrape:
                    print(f"✅ Truyện đã đầy đủ {len(all_chaps)} chap. Không cần cào thêm!  Untitled1:155 - caotruyen.py:155")
                    page.close()
                    continue
                
                print(f"👉 Truyện có tổng {len(all_chaps)} chap. Phát hiện {len(chaps_to_scrape)} chap MỚI cần cào.  Untitled1:159 - caotruyen.py:159")

                # 4. Bắt đầu vào từng chap mới để lấy ảnh
                for idx, chap in enumerate(chaps_to_scrape, 1):
                    print(f"[{idx}/{len(chaps_to_scrape)}] Đang cào {chap['name']}...  Untitled1:163 - caotruyen.py:163")
                    try:
                        page.goto(chap['href'], wait_until="load", timeout=30000)
                        
                        # Giả lập cuộn chuột từ từ để các ảnh Lazyload hiện ra hết
                        for _ in range(10):
                            page.mouse.wheel(0, 800)
                            page.wait_for_timeout(500)
                            
                        images = page.evaluate(JS_GET_IMAGES)
                        
                        if images:
                            print(f"✅ Tóm được {len(images)} ảnh.  Untitled1:175 - caotruyen.py:175")
                            comic_entry["chapters"].append({
                                "name": chap['name'],
                                "images": images
                            })
                        else:
                            print(f"⚠️ Trắng tay, không thấy ảnh.  Untitled1:181 - caotruyen.py:181")
                            
                    except Exception as e:
                        print(f"❌ Lỗi cào chap: {e}  Untitled1:184 - caotruyen.py:184")

                # Sắp xếp lại ruột JSON cho gọn gàng từ Chap 1 -> Chap N
                comic_entry["chapters"].sort(key=lambda x: parseFloat_safe(x["name"]))

            except Exception as e:
                print(f"❌ Lỗi xử lý truyện: {e}  Untitled1:190 - caotruyen.py:190")
            finally:
                page.close()

        browser.close()

    # 5. Lưu và Đẩy ngược lại lên GitHub
    print("\n🎉 HOÀN TẤT CÀO TRUYỆN!  Untitled1:197 - caotruyen.py:197")
    
    output_data = {"comics": existing_comics}
    content = json.dumps(output_data, indent=2, ensure_ascii=False)
    
    if GITHUB_TOKEN:
        try:
            repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
            msg = "📖 Auto-Update Truyện Tranh: " + now_str
            if file_sha:
                repo.update_file(FILE_PATH, msg, content, file_sha)
                print(f"✅ Đã CẬP NHẬT GHI ĐÈ thành công lên {REPO_NAME}/{FILE_PATH}  Untitled1:208 - caotruyen.py:208")
            else:
                repo.create_file(FILE_PATH, msg, content)
                print(f"✅ Đã TẠO MỚI thành công file {FILE_PATH}  Untitled1:211 - caotruyen.py:211")
        except Exception as e:
            print(f"❌ Lỗi tải lên GitHub: {e}  Untitled1:213 - caotruyen.py:213")
    else:
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✅ Đã lưu file cục bộ: {FILE_PATH}  Untitled1:217 - caotruyen.py:217")

def parseFloat_safe(text):
    """Hàm phụ trợ giúp sắp xếp tên chapter chuẩn xác"""
    match = re.search(r'\d+(\.\d+)?', text)
    return float(match.group()) if match else 0.0

if __name__ == "__main__":
    scrape_and_update()
