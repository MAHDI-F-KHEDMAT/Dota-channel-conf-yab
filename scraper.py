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
MAX_FINAL_CONFIGS = 200  # تعداد کانفیگ نهایی
MAX_PER_HOST = 3        # حداکثر کانفیگ مجاز از یک آی‌پی یا دامنه (برای جلوگیری از کرش)

CHANNELS = [
    "https://t.me/ConfigsHUB"
]

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'

# ==========================================
# ۱. تابع تست اتصال TCP سریع
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
                
    print(f"{Colors.GREEN}✅ تست TCP تمام شد. تعداد {len(alive_configs)} کانفیگ پاسخگو ماندند.{Colors.RESET}")
    return alive_configs

# ==========================================
# ۲. تابع محدوده تنوع سرورها (جلوگیری از کرش کلاینت)
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
# ۳. تابع تست سرعت
# ==========================================
def test_download_speed(config_url, local_socks_port=1080):
    proxy_address = f"socks5h://127.0.0.1:{local_socks_port}"
    proxies = {"http": proxy_address, "https": proxy_address}
    
    start_time = time.time()
    try:
        response = requests.get("http://speedtest.tele2.net/1MB.zip", proxies=proxies, timeout=10)
        if response.status_code == 200 and len(response.content) > 1000000:
            duration = time.time() - start_time
            speed_mbps = (1.0 / duration) * 8
            return {'config': config_url, 'speed': speed_mbps, 'status': 'ok'}
    except Exception:
        pass
        
    return {'config': config_url, 'speed': 0, 'status': 'failed'}

def filter_top_200_configs(tcp_alive_configs):
    if not tcp_alive_configs:
        return []
        
    # محدود کردن کانفیگ‌های تکراری روی یک سرور قبل از تست سرعت
    diverse_configs = limit_configs_per_host(tcp_alive_configs)
    
    print(f"\n{Colors.YELLOW}🚀 شروع تست سرعت روی {len(diverse_configs)} کانفیگ انتخاب‌شده...{Colors.RESET}")
    
    valid_tested = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_config = {executor.submit(test_download_speed, url): url for url in diverse_configs}
        
        for future in concurrent.futures.as_completed(future_to_config):
            result = future.result()
            if result['status'] == 'ok':
                valid_tested.append(result)

    if not valid_tested:
        print(f"{Colors.YELLOW}⚠️ استفاده از کانفیگ‌های تایید شده TCP بدون تست سرعت.{Colors.RESET}")
        return diverse_configs[:MAX_FINAL_CONFIGS]

    valid_tested.sort(key=lambda x: x['speed'], reverse=True)
    top_200 = valid_tested[:MAX_FINAL_CONFIGS]
    
    print(f"{Colors.GREEN}✅ گلچین کردن {len(top_200)} کانفیگ پرسرعت انجام شد.{Colors.RESET}")
    return [item['config'] for item in top_200]

# ==========================================
# ۴. تابع فیلتر فوق‌سخت‌گیرانه ضد کرش
# ==========================================
def is_bulletproof_vless(raw_url):
    try:
        # پاک‌سازی کاراکترهای نامرئی و فضاهای خالی
        raw_url = raw_url.strip().replace('\u200b', '').replace('\u200c', '').replace('\r', '')
        
        parsed = urlparse(raw_url)
        if not parsed.hostname or not parsed.port: return False
        if not (1 <= parsed.port <= 65535): return False
        
        # بررسی فرمت استاندارد UUID
        uuid = parsed.username
        if not uuid or not re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', uuid):
            return False

        qs = parse_qs(parsed.query)
        params = {k.lower(): v[0] for k, v in qs.items()}
        
        net_type = params.get('type', 'tcp').lower()
        security = params.get('security', 'none').lower()
        flow = params.get('flow', '').lower()
        sni = params.get('sni', '')

        if security not in ('none', 'tls', 'xtls', 'reality'): return False
        
        # چک کردن کاراکترهای خرابکار در SNI
        if sni and any(char in sni for char in [' ', '/', ':', '\\', '<', '>', '"', "'"]):
            return False

        if security == 'reality':
            pbk = params.get('pbk', '')
            if not pbk or not re.match(r'^[A-Za-z0-9\-_]{43}$', pbk): return False
            sid = params.get('sid', '')
            if sid and not re.match(r'^([0-9a-fA-F]{2}){1,8}$', sid): return False
            if not sni: return False

        if 'vision' in flow and (net_type != 'tcp' or security not in ('tls', 'xtls', 'reality')): return False
        
        return True
    except Exception:
        return False

# ==========================================
# ۵. تابع استخراج از کانال
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
# ۶. مدیریت اصلی
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
            best_configs = filter_top_200_configs(tcp_alive_configs)
        else:
            best_configs = []

        if best_configs:
            renamed_list = []
            for idx, config in enumerate(best_configs, 1):
                clean_config = config.split('#')[0]
                new_name = urllib.parse.quote(f"{CONFIG_PREFIX_NAME}{idx}")
                renamed_list.append(f"{clean_config}#{new_name}")

            plain_text_content = "\n".join(renamed_list)
            b64_encoded_content = base64.b64encode(plain_text_content.encode('utf-8')).decode('utf-8')

            sub_filename = 'sub_vless_base64.txt'
            with open(sub_filename, 'w', encoding='utf-8') as f:
                f.write(b64_encoded_content)

            print(f"\n{Colors.GREEN}✅ فایل نهایی سابسکریپشن با {len(best_configs)} کانفیگ کاملاً ایمن ساخته شد!{Colors.RESET}")
        else:
            print(f"\n{Colors.YELLOW}❌ هیچ کانفیگی از تست TCP یا سرعت عبور نکرد.{Colors.RESET}")
    else:
        print(f"\n{Colors.YELLOW}❌ هیچ کانفیگ استانداردی یافت نشد.{Colors.RESET}")

if __name__ == "__main__":
    scrape_all_channels()
