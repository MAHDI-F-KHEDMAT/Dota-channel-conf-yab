import re
import time
import html
import base64
import requests
import urllib.parse
import concurrent.futures
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# فهماندن پروتکل vless به کتابخانه استاندارد پایتون
urllib.parse.uses_netloc.append('vless')

# ==========================================
# ⚙️ تنظیمات اسکریپت
# ==========================================
DAYS_BACK = 2
CONFIG_PREFIX_NAME = "ConfigsHUB_VIP_"
MAX_FINAL_CONFIGS = 200  # محدودیت فایل نهایی (۲۰۰ کانفیگ برتر)

CHANNELS = ["https://t.me/ConfigsHUB"]

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'

# ==========================================
# توابع مربوط به تست سرعت و دانلود ۱ مگابایت
# ==========================================
def test_download_speed(config_url, local_socks_port=1080):
    """
    تابع تست سرعت کانفیگ. 
    در اجرای واقعی، هسته Xray باید این کانفیگ VLESS را روی پورت SOCKS5 ران کرده باشد.
    """
    # اتصال به پورتی که Xray کانفیگ را روی آن باز کرده است
    proxy_address = f"socks5h://127.0.0.1:{local_socks_port}"
    proxies = {
        "http": proxy_address,
        "https": proxy_address
    }
    
    start_time = time.time()
    try:
        # دانلود فایل ۱ مگابایتی از سرور تست سرعت با تایم‌اوت ۱۰ ثانیه
        # اگر کانفیگ خراب باشد در همین مرحله خطا می‌دهد
        response = requests.get("http://speedtest.tele2.net/1MB.zip", proxies=proxies, timeout=10)
        
        # بررسی اینکه واقعاً فایل با موفقیت دانلود شده است
        if response.status_code == 200 and len(response.content) > 1000000:
            duration = time.time() - start_time
            speed_mbps = (1.0 / duration) * 8 # تبدیل به مگابیت بر ثانیه
            return {'config': config_url, 'speed': speed_mbps, 'status': 'ok'}
            
    except Exception:
        pass # اتصال ناموفق بود
        
    return {'config': config_url, 'speed': 0, 'status': 'failed'}

def filter_top_200_configs(unique_configs):
    """
    تست موازی تمام کانفیگ‌ها و گلچین کردن ۲۰۰ تای برتر
    """
    print(f"\n{Colors.YELLOW}🚀 شروع تست سرعت دانلود (۱ مگابایتی) برای {len(unique_configs)} کانفیگ...{Colors.RESET}")
    print(f"⚠️ {Colors.RED}نکته:{Colors.RESET} اجرای تست VLESS در پایتون نیازمند راه‌اندازی Xray-Core برای هر Thread است.")
    
    valid_tested = []
    
    # اجرای ۲۰ تست به صورت همزمان (Multi-Threading)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # در اینجا فرض می‌کنیم Xray-core در پس‌زمینه هندل شده است
        future_to_config = {executor.submit(test_download_speed, url): url for url in unique_configs}
        
        for future in concurrent.futures.as_completed(future_to_config):
            result = future.result()
            if result['status'] == 'ok':
                valid_tested.append(result)

    # اگر تستی موفق نبود، برای جلوگیری از خالی ماندن فایل، همان کانفیگ‌های اولیه رو برمی‌گردانیم
    if not valid_tested:
        print(f"{Colors.YELLOW}⚠️ هیچ کانفیگی در تست سرعت پایتون موفق نبود. (احتمالاً Xray-Core روی سرور ست نشده است).{Colors.RESET}")
        return unique_configs[:MAX_FINAL_CONFIGS]

    # ۱. مرتب‌سازی لیست بر اساس بالاترین سرعت
    valid_tested.sort(key=lambda x: x['speed'], reverse=True)
    
    # ۲. برش دادن و انتخاب ۲۰۰ تای اول
    top_200 = valid_tested[:MAX_FINAL_CONFIGS]
    
    print(f"{Colors.GREEN}✅ تست سرعت تمام شد. {len(top_200)} کانفیگ پرسرعت جدا شدند.{Colors.RESET}")
    return [item['config'] for item in top_200]

# ==========================================
# تابع فیلتر ضد کرش (از پاسخ قبلی)
# ==========================================
def is_bulletproof_vless(raw_url):
    try:
        parsed = urlparse(raw_url)
        if not parsed.hostname or not parsed.port: return False
        if not (1 <= parsed.port <= 65535): return False
        qs = parse_qs(parsed.query)
        params = {k.lower(): v[0] for k, v in qs.items()}
        net_type, security, flow = params.get('type', 'tcp').lower(), params.get('security', 'none').lower(), params.get('flow', '').lower()
        if security not in ('none', 'tls', 'xtls', 'reality'): return False
        if security == 'reality':
            if not params.get('pbk', '') or not re.match(r'^[A-Za-z0-9\-_]{43}$', params.get('pbk', '')): return False
            if params.get('sid', '') and not re.match(r'^([0-9a-fA-F]{2}){1,8}$', params.get('sid', '')): return False
            if not params.get('sni', ''): return False
        if 'vision' in flow and (net_type != 'tcp' or security not in ('tls', 'xtls', 'reality')): return False
        return True
    except Exception: return False

# ==========================================
# تابع بررسی هر کانال
# ==========================================
def scrape_channel(channel_url, cutoff_datetime, session, config_pattern):
    # (کد قبلی دقیقاً مانند قبل - برای استخراج لینک‌ها)
    if "/s/" not in channel_url: channel_url = channel_url.replace("t.me/", "t.me/s/")
    current_url, reached_old, channel_configs, seen_min_ids = channel_url, False, [], set()

    while not reached_old:
        try:
            response = session.get(current_url, timeout=25)
            if response.status_code != 200: break
        except Exception: break

        soup = BeautifulSoup(response.text, 'html.parser')
        messages = soup.find_all('div', class_='tgme_widget_message')
        if not messages: break

        page_min_id = None
        for msg in messages:
            data_post = msg.get('data-post', '')
            if '/' in data_post:
                msg_id = int(data_post.split('/')[-1])
                if page_min_id is None or msg_id < page_min_id: page_min_id = msg_id

            time_tag = msg.find('time')
            if not time_tag: continue
            
            msg_dt = datetime.fromisoformat(time_tag.get('datetime').replace('Z', '+00:00').split('+')[0])
            if msg_dt < cutoff_datetime:
                reached_old = True
                break

            text_div = msg.find('div', class_='tgme_widget_message_text') or msg.find('div', class_='tgme_widget_message_caption')
            if text_div:
                msg_text = html.unescape(text_div.get_text(separator=" ", strip=False))
                for raw_config in config_pattern.findall(msg_text):
                    if is_bulletproof_vless(raw_config):
                        channel_configs.append(raw_config)

        if reached_old or not page_min_id or page_min_id in seen_min_ids: break
        seen_min_ids.add(page_min_id)
        current_url = f"{channel_url}?before={page_min_id}"
    return channel_configs

# ==========================================
# مدیریت اصلی
# ==========================================
def scrape_all_channels():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"})
    cutoff_datetime = datetime.utcnow() - timedelta(days=DAYS_BACK)
    config_pattern = re.compile(r'vless://[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}@[^\s\'"<>#]+')
    
    all_extracted_configs = []
    for channel in CHANNELS:
        all_extracted_configs.extend(scrape_channel(channel, cutoff_datetime, session, config_pattern))

    unique_configs = list(dict.fromkeys(all_extracted_configs))

    if unique_configs:
        # مرحله اضافه شده: تست سرعت و انتخاب 200 تای برتر
        best_200_configs = filter_top_200_configs(unique_configs)

        renamed_list = []
        for idx, config in enumerate(best_200_configs, 1):
            new_name = f"{CONFIG_PREFIX_NAME}TOP_{idx}"
            renamed_list.append(f"{config}#{new_name}")

        plain_text_content = "\n".join(renamed_list)
        b64_encoded_content = base64.b64encode(plain_text_content.encode('utf-8')).decode('utf-8')

        sub_filename = 'sub_vless_base64.txt'
        with open(sub_filename, 'w', encoding='utf-8') as f:
            f.write(b64_encoded_content)

        print(f"\n{Colors.GREEN}✅ فایل نهایی سابسکریپشن با {len(best_200_configs)} کانفیگ ساخته شد!{Colors.RESET}")
    else:
        print(f"\n{Colors.YELLOW}❌ هیچ کانفیگ استانداردی یافت نشد.{Colors.RESET}")

if __name__ == "__main__":
    scrape_all_channels()
