import re
import time
import html
import base64
import requests
import uuid
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# ==========================================
# ⚙️ تنظیمات اسکریپت
# ==========================================
DAYS_BACK = 2
CONFIG_PREFIX_NAME = "ConfigsHUB_VIP_"

# لیست کانال‌ها
CHANNELS = [
    "https://t.me/ConfigsHUB"
]

# ==========================================
# تابع اعتبارسنجی عمیق برای جلوگیری از Fatal Panic در Xray-Core
# ==========================================
def is_strictly_valid_vless(config_url):
    try:
        config_url = config_url.strip()
        if not config_url.lower().startswith("vless://"):
            return False

        # جدا کردن لینک از اسم (Remark)
        raw_url = config_url.split('#')[0].strip()
        
        if '@' not in raw_url or ':' not in raw_url:
            return False

        parsed = urlparse(raw_url)

        # ۱. بررسی صحت کامل و استاندارد UUID (بررسی ساختار ۳۶ کاراکتری)
        user_id = parsed.username
        if not user_id:
            return False
        try:
            uuid.UUID(user_id)
        except ValueError:
            return False

        # ۲. بررسی آدرس و پورت
        hostname = parsed.hostname
        port = parsed.port
        if not hostname or not port or not (1 <= port <= 65535):
            return False

        # جلوگیری از وجود فاصله یا کاراکترهای کنترلی در آدرس
        if any(c in hostname for c in [' ', '\t', '\n', '\r']):
            return False

        # ۳. تجزیه و تحلیل پارامترهای کوئری
        query_params = parse_qs(parsed.query)
        params = {k.lower(): v[0] for k.lower(), v in query_params.items() if v}

        security = params.get('security', '').lower()
        net_type = params.get('type', 'tcp').lower()
        flow = params.get('flow', '').lower()

        # بررسی شرط TLS / Reality
        if security not in ('tls', 'reality', 'xtls'):
            return False

        # بررسی شرط Reality: عدم وجود pbk یا sni باعث کرش هسته می‌شود
        if security == 'reality':
            pbk = params.get('pbk', '')
            sni = params.get('sni', '')
            if not pbk or len(pbk) < 30 or not sni:
                return False

        # بررسی شرط Vision: ست بودن flow=xtls-rprx-vision روی غیر TCP باعث کرش می‌شود
        if 'vision' in flow:
            if net_type != 'tcp' or security not in ('tls', 'reality', 'xtls'):
                return False

        # ۴. بررسی معتبر بودن Fingerprint
        fp = params.get('fp', '').lower()
        valid_fps = {'chrome', 'firefox', 'safari', 'edge', '360', 'qq', 'ios', 'android', 'random', 'randomized', ''}
        if fp and fp not in valid_fps:
            return False

        return True

    except Exception:
        return False

# ==========================================
# تابع تمیزکاری کاراکترهای زائد تلگرام
# ==========================================
def clean_and_format_url(config_url):
    config_url = re.sub(r'[\)\}\]\>\.\,\;\:\'\" ]+$', '', config_url.strip())
    config_url = re.sub(r'^[\(\{\[\<\'\" ]+', '', config_url)
    return config_url

# ==========================================
# تابع تغییر نام کانفیگ
# ==========================================
def rename_config(config_url, new_name):
    try:
        base_url = config_url.split('#')[0].strip()
        return f"{base_url}#{new_name}"
    except Exception:
        return config_url.strip()

# ==========================================
# تابع بررسی هر کانال
# ==========================================
def scrape_channel(channel_url, cutoff_datetime, session, config_pattern):

    if "/s/" not in channel_url:
        channel_url = channel_url.replace("t.me/", "t.me/s/")

    current_url = channel_url
    reached_old = False
    page_count = 0
    channel_configs = []
    seen_min_ids = set()

    print(f"\n" + "─" * 60)
    print(f"📂 شروع بررسی کانال: {channel_url}")
    print("─" * 60)

    while not reached_old:
        page_count += 1
        print(f"🌐 [صفحه {page_count}] دریافت اطلاعات از: {current_url}")

        try:
            response = session.get(current_url, timeout=25)

            if response.status_code == 429:
                print("  ⚠️ محدودیت درخواست! ۱۵ ثانیه صبر...")
                time.sleep(15)
                continue

            if response.status_code != 200:
                break

            response.raise_for_status()

        except Exception as e:
            print(f"  ❌ خطا در اتصال: {e}")
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        messages = soup.find_all('div', class_='tgme_widget_message')

        if not messages:
            break

        page_min_id = None

        for msg in messages:
            data_post = msg.get('data-post', '')

            if '/' in data_post:
                try:
                    msg_id = int(data_post.split('/')[-1])
                    if page_min_id is None or msg_id < page_min_id:
                        page_min_id = msg_id
                except ValueError:
                    pass

            time_tag = msg.find('time')
            if not time_tag or not time_tag.get('datetime'):
                continue

            dt_str = time_tag.get('datetime').replace('Z', '+00:00')

            try:
                msg_dt = datetime.fromisoformat(dt_str.split('+')[0])
            except ValueError:
                continue

            if msg_dt < cutoff_datetime:
                reached_old = True
                break

            text_div = (
                msg.find('div', class_='tgme_widget_message_text') or
                msg.find('div', class_='tgme_widget_message_caption')
            )

            if text_div:
                for br in text_div.find_all(["br", "wbr", "p", "div"]):
                    br.replace_with(" \n ")

                msg_text = text_div.get_text(separator=" ", strip=False)
                msg_text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff]', '', msg_text)
                msg_text = html.unescape(msg_text)

                found_configs = config_pattern.findall(msg_text)

                for config in found_configs:
                    cleaned = clean_and_format_url(config)
                    # فیلتر سخت‌گیرانه برای حفظ سلامت هسته Xray
                    if is_strictly_valid_vless(cleaned):
                        channel_configs.append(cleaned)

        if reached_old or not page_min_id or page_min_id in seen_min_ids:
            break

        seen_min_ids.add(page_min_id)
        current_url = f"{channel_url}?before={page_min_id}"
        time.sleep(1.5)

    return channel_configs

# ==========================================
# تابع اصلی مدیریت
# ==========================================
def scrape_all_channels():

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"
    })

    cutoff_datetime = datetime.utcnow() - timedelta(days=DAYS_BACK)
    config_pattern = re.compile(r'(?i)vless://[^\s\'"<>]+')

    all_extracted_configs = []

    for channel in CHANNELS:
        try:
            configs = scrape_channel(channel, cutoff_datetime, session, config_pattern)
            all_extracted_configs.extend(configs)
        except Exception as e:
            print(f"❌ خطا: {e}")

    # حذف کانفیگ‌های تکراری (بدون محدودیت تعداد)
    unique_configs = list(dict.fromkeys(all_extracted_configs))

    if unique_configs:
        renamed_list = []
        for idx, config in enumerate(unique_configs, 1):
            new_name = f"{CONFIG_PREFIX_NAME}{idx}"
            renamed_list.append(rename_config(config, new_name))

        plain_text_content = "\n".join(renamed_list)
        b64_encoded_content = base64.b64encode(plain_text_content.encode('utf-8')).decode('utf-8')

        sub_filename = 'sub_vless_base64.txt'
        with open(sub_filename, 'w', encoding='utf-8') as f:
            f.write(b64_encoded_content)

        print("\n" + "=" * 65)
        print(f"✅ تعداد {len(renamed_list)} کانفیگ معتبر استخراج گردید.")
        print(f"✅ فایل سابسکریپشن ساخته شد: {sub_filename}")
        print("=" * 65)

    else:
        print("\n❌ هیچ کانفیگ معتبری یافت نشد.")


if __name__ == "__main__":
    scrape_all_channels()
