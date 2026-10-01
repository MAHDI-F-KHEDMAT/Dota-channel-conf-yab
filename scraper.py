import re
import time
import html
import base64
import socket
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
MAX_FINAL_CONFIGS = 500  # تعداد کانفیگ‌های برتر فایل نهایی
DOWNLOAD_TEST_URL = "https://speed.cloudflare.com/__down?bytes=1048576"  # لینک ۱ مگابایتی کلودفلر

CHANNELS = [
    "https://t.me/ConfigsHUB"
]

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'

# ==========================================
# ۱. تست اتصال TCP سریع (فیلتر اولیه کانفیگ‌های مرده)
# ==========================================
def check_tcp_connection(config_url, timeout=3):
    try:
        parsed = urlparse(config_url)
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            return {'config': config_url, 'status': False}

        with socket.create_connection((host, port), timeout=timeout):
            return {'config': config_url, 'status': True}
    except Exception:
        return {'config': config_url, 'status': False}

def filter_tcp_alive_configs(unique_configs):
    print(f"\n{Colors.YELLOW}⚡ شروع تست اتصال TCP سریع برای {len(unique_configs)} کانفیگ...{Colors.RESET}")

    alive_configs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        future_to_config = {executor.submit(check_tcp_connection, url): url for url in unique_configs}
        for future in concurrent.futures.as_completed(future_to_config):
            result = future.result()
            if result['status']:
                alive_configs.append(result['config'])

    print(f"{Colors.GREEN}✅ تست TCP تمام شد. تعداد {len(alive_configs)} کانفیگ زنده و پاسخگو ماندند.{Colors.RESET}")
    return alive_configs

# ==========================================
# ۲. تست سرعت دانلود ۱ مگابایت واقعی بر روی تمام کانفیگ‌های زنده
# ==========================================
def test_download_speed(config_url, local_socks_port=1080):
    proxy_address = f"socks5h://127.0.0.1:{local_socks_port}"
    proxies = {
        "http": proxy_address,
        "https": proxy_address
    }

    start_time = time.time()
    try:
        response = requests.get(DOWNLOAD_TEST_URL, proxies=proxies, timeout=12, stream=True)
        if response.status_code == 200:
            downloaded_bytes = 0
            for chunk in response.iter_content(chunk_size=16384):
                if chunk:
                    downloaded_bytes += len(chunk)

            # بررسی اتمام کامل دانلود ۱ مگابایت (۱,۰۰۰,۰۰۰ بایت)
            if downloaded_bytes >= 1000000:
                duration = time.time() - start_time
                speed_mbps = (downloaded_bytes * 8) / (1024 * 1024 * duration) if duration > 0 else 0
                return {'config': config_url, 'speed': speed_mbps, 'status': 'ok'}
    except Exception:
        pass

    return {'config': config_url, 'speed': 0, 'status': 'failed'}

def filter_top_500_configs(tcp_alive_configs):
    if not tcp_alive_configs:
        return []

    print(f"\n{Colors.YELLOW}🚀 شروع تست سرعت دانلود ۱ مگابایتی روی تمامی {len(tcp_alive_configs)} کانفیگ زنده...{Colors.RESET}")

    valid_tested = []
    # اجرای تست سرعت دانلود موازی بر روی تمامی کانفیگ‌های زنده
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_config = {executor.submit(test_download_speed, url): url for url in tcp_alive_configs}
        for future in concurrent.futures.as_completed(future_to_config):
            result = future.result()
            if result['status'] == 'ok':
                valid_tested.append(result)

    if not valid_tested:
        print(f"{Colors.YELLOW}⚠️ هیچ کانفیگی موفق به دانلود کامل فایل ۱ مگابایتی نشد. استفاده از {min(len(tcp_alive_configs), MAX_FINAL_CONFIGS)} کانفیگ تایید شده TCP.{Colors.RESET}")
        return tcp_alive_configs[:MAX_FINAL_CONFIGS]

    # مرتب‌سازی بر اساس سرعت دانلود (از سریع‌ترین به کندترین)
    valid_tested.sort(key=lambda x: x['speed'], reverse=True)
    top_500 = valid_tested[:MAX_FINAL_CONFIGS]

    print(f"{Colors.GREEN}✅ گلچین کردن {len(top_500)} کانفیگ پرسرعت انجام شد.{Colors.RESET}")
    return [item['config'] for item in top_500]

# ==========================================
# ۳. فیلتر ساختاری پروتکل VLESS
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
# ۴. استخراج کانفیگ‌ها از کانال‌ها
# ==========================================
def scrape_channel(channel_url, cutoff_datetime, session, config_pattern):
    if "/s/" not in channel_url: channel_url = channel_url.replace("t.me/", "t.me/s/")
    current_url, reached_old, channel_configs, seen_min_ids = channel_url, False, [], set()

    print(f"\n" + "─" * 60)
    print(f"📂 شروع بررسی کانال: {channel_url}")
    print("─" * 60)

    while not reached_old:
        try:
            response = session.get(current_url, timeout=25)
            if response.status_code == 429:
                time.sleep(15)
                continue
            if response.status_code != 200: break
        except Exception: break

        soup = BeautifulSoup(response.text, 'html.parser')
        messages = soup.find_all('div', class_='tgme_widget_message')
        if not messages: break

        page_min_id = None
        for msg in messages:
            data_post = msg.get('data-post', '')
            if '/' in data_post:
                try:
                    msg_id = int(data_post.split('/')[-1])
                    if page_min_id is None or msg_id < page_min_id: page_min_id = msg_id
                except ValueError: pass

            time_tag = msg.find('time')
            if not time_tag: continue

            try:
                msg_dt = datetime.fromisoformat(time_tag.get('datetime').replace('Z', '+00:00').split('+')[0])
            except ValueError: continue

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
        time.sleep(1.5)
    return channel_configs

# ==========================================
# ۵. اجرای اصلی اسکریپت
# ==========================================
def scrape_all_channels():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"})
    cutoff_datetime = datetime.utcnow() - timedelta(days=DAYS_BACK)
    config_pattern = re.compile(r'vless://[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}@[^\s\'"<>#]+')

    all_extracted_configs = []
    for channel in CHANNELS:
        try:
            all_extracted_configs.extend(scrape_channel(channel, cutoff_datetime, session, config_pattern))
        except Exception as e:
            print(f"{Colors.RED}❌ خطا: {e}{Colors.RESET}")

    unique_configs = list(dict.fromkeys(all_extracted_configs))

    if unique_configs:
        tcp_alive_configs = filter_tcp_alive_configs(unique_configs)

        if tcp_alive_configs:
            best_configs = filter_top_500_configs(tcp_alive_configs)
        else:
            best_configs = []

        if best_configs:
            renamed_list = []
            for idx, config in enumerate(best_configs, 1):
                new_name = f"{CONFIG_PREFIX_NAME}{idx}"
                renamed_list.append(f"{config}#{new_name}")

            plain_text_content = "\n".join(renamed_list)
            b64_encoded_content = base64.b64encode(plain_text_content.encode('utf-8')).decode('utf-8')

            sub_filename = 'sub_vless_base64.txt'
            with open(sub_filename, 'w', encoding='utf-8') as f:
                f.write(b64_encoded_content)

            print(f"\n{Colors.GREEN}✅ فایل نهایی سابسکریپشن با {len(best_configs)} کانفیگ پرسرعت ایجاد شد!{Colors.RESET}")
        else:
            print(f"\n{Colors.YELLOW}❌ هیچ کانفیگی از تست سرعت عبور نکرد.{Colors.RESET}")
    else:
        print(f"\n{Colors.YELLOW}❌ هیچ کانفیگ استانداردی یافت نشد.{Colors.RESET}")

if __name__ == "__main__":
    scrape_all_channels()
