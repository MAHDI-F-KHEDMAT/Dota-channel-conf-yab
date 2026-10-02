import re
import time
import html
import base64
import socket
import requests
import urllib.parse
import concurrent.futures
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

urllib.parse.uses_netloc.append('vless')

# ==========================================
# ⚙️ تنظیمات اسکریپت
# ==========================================
DAYS_BACK = 2
CONFIG_PREFIX_NAME = "ConfigsHUB_VIP_"
MAX_FINAL_CONFIGS = 200
MAX_PER_HOST = 3

CHANNELS = [
    "https://t.me/ConfigsHUB"
]

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'

# ==========================================
# ۱. تابع تست اتصال TCP
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
    print(f"\n{Colors.YELLOW}⚡ شروع تست اتصال TCP برای {len(unique_configs)} کانفیگ...{Colors.RESET}")
    alive_configs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        future_to_config = {executor.submit(check_tcp_connection, url): url for url in unique_configs}
        for future in concurrent.futures.as_completed(future_to_config):
            res = future.result()
            if res['status']:
                alive_configs.append(res['config'])
    print(f"{Colors.GREEN}✅ تعداد {len(alive_configs)} کانفیگ پاسخگو ماندند.{Colors.RESET}")
    return alive_configs

# ==========================================
# ۲. تنوع‌بخشی به سرورها
# ==========================================
def limit_configs_per_host(configs, max_per_host=MAX_PER_HOST):
    host_count = {}
    filtered = []
    for cfg in configs:
        try:
            host = urlparse(cfg).hostname
            if host:
                count = host_count.get(host, 0)
                if count < max_per_host:
                    host_count[host] = count + 1
                    filtered.append(cfg)
        except Exception:
            continue
    return filtered

# ==========================================
# ۳. پاک‌سازی و استانداردسازی کامل لینک (رفع عامل اصلی کرش)
# ==========================================
def sanitize_and_validate_vless(raw_url):
    try:
        # ۱. رفع مشکل &amp; تلگرام و کاراکترهای نامرئی
        raw_url = html.unescape(raw_url).replace('&amp;', '&').strip()
        raw_url = re.sub(r'[\u200b\u200c\r\n]', '', raw_url)

        parsed = urlparse(raw_url)
        if parsed.scheme != 'vless': return None
        if not parsed.hostname or not parsed.port: return None
        if not (1 <= parsed.port <= 65535): return None

        # ۲. بررسی صحت ساختار UUID
        uuid = parsed.username
        if not uuid or not re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', uuid):
            return None

        qs = parse_qs(parsed.query)
        params = {k.lower(): v[0] for k, v in qs.items()}

        # ۳. نگه داشتن فقط پارامترهای رسمی Xray (حذف پارامترهای سمی و غیراستاندارد)
        VALID_KEYS = {'type', 'security', 'pbk', 'fp', 'sni', 'sid', 'spx', 'flow', 'path', 'host', 'headertype', 'encryption'}
        clean_params = {}

        for k, v in params.items():
            if k in VALID_KEYS:
                clean_params[k] = v

        security = clean_params.get('security', 'none').lower()
        net_type = clean_params.get('type', 'tcp').lower()
        flow = clean_params.get('flow', '').lower()

        if security not in ('none', 'tls', 'xtls', 'reality'): return None

        if security == 'reality':
            pbk = clean_params.get('pbk', '')
            if not pbk or not re.match(r'^[A-Za-z0-9\-_]{43}$', pbk): return None
            if not clean_params.get('sni', ''): return None

        if 'vision' in flow and (net_type != 'tcp' or security not in ('tls', 'xtls', 'reality')):
            return None

        # بازسازی کوئری استرینگ تمیز
        clean_query = urlencode(clean_params)
        
        # بازسازی کامل لینک بدون کرش
        clean_url = urlunparse((
            'vless',
            f"{parsed.hostname}:{parsed.port}",
            f"/{uuid}",
            '',
            clean_query,
            ''
        ))

        return clean_url

    except Exception:
        return None

# ==========================================
# ۴. استخراج از کانال
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
                msg_text = text_div.get_text(separator=" ", strip=False)
                for raw_config in config_pattern.findall(msg_text):
                    clean_cfg = sanitize_and_validate_vless(raw_config)
                    if clean_cfg:
                        channel_configs.append(clean_cfg)

        if reached_old or not page_min_id or page_min_id in seen_min_ids: break
        seen_min_ids.add(page_min_id)
        current_url = f"{channel_url}?before={page_min_id}"
        time.sleep(1.5)
    return channel_configs

# ==========================================
# ۵. مدیریت اصلی
# ==========================================
def scrape_all_channels():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"})
    cutoff_datetime = datetime.utcnow() - timedelta(days=DAYS_BACK)
    config_pattern = re.compile(r'vless://[^\s\'"<>]+')
    
    all_extracted_configs = []
    for channel in CHANNELS:
        try:
            all_extracted_configs.extend(scrape_channel(channel, cutoff_datetime, session, config_pattern))
        except Exception as e:
            print(f"{Colors.RED}❌ خطا: {e}{Colors.RESET}")

    unique_configs = list(dict.fromkeys(all_extracted_configs))

    if unique_configs:
        tcp_alive_configs = filter_tcp_alive_configs(unique_configs)
        diverse_configs = limit_configs_per_host(tcp_alive_configs)
        final_configs = diverse_configs[:MAX_FINAL_CONFIGS]

        if final_configs:
            renamed_list = []
            for idx, config in enumerate(final_configs, 1):
                # نام‌گذاری ساده و بدون درصد-انکود
                new_name = f"{CONFIG_PREFIX_NAME}{idx}"
                renamed_list.append(f"{config}#{new_name}")

            plain_text_content = "\n".join(renamed_list)
            b64_encoded_content = base64.b64encode(plain_text_content.encode('utf-8')).decode('utf-8')

            sub_filename = 'sub_vless_base64.txt'
            with open(sub_filename, 'w', encoding='utf-8') as f:
                f.write(b64_encoded_content)

            print(f"\n{Colors.GREEN}✅ فایل سابسکریپشن بدون کرش با {len(final_configs)} کانفیگ ساخته شد!{Colors.RESET}")
        else:
            print(f"\n{Colors.YELLOW}❌ هیچ کانفیگ زنده و معتبری یافت نشد.{Colors.RESET}")
    else:
        print(f"\n{Colors.YELLOW}❌ هیچ کانفیگی پیدا نشد.{Colors.RESET}")

if __name__ == "__main__":
    scrape_all_channels()
