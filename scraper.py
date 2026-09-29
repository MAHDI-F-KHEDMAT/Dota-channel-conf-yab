import re
import time
import html
import base64
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# ==========================================
# ⚙️ تنظیمات اسکریپت
# ==========================================
DAYS_BACK = 2
CONFIG_PREFIX_NAME = "ConfigsHUB_VIP_"  # پیشوند اسم کانفیگ‌ها

# لیست کانال‌ها
CHANNELS = [
    "https://t.me/ConfigsHUB"
]

# ==========================================
# تابع پاکسازی و اصلاح لینک از کاراکترهای اضافه تلگرام
# ==========================================
def clean_vless_url(config_url):
    # حذف کاراکترهای ناخواسته پایان لینک (مثل پرانتز، نقطه، کاما و...)
    config_url = re.sub(r'[\)\}\]\>\.\,\;\:\'\" ]+$', '', config_url.strip())
    # حذف کاراکترهای ناخواسته ابتدای لینک
    config_url = re.sub(r'^[\(\{\[\<\'\" ]+', '', config_url)
    return config_url

# ==========================================
# تابع اعتبارسنجی ساختاری (جهت جلوگیری از کرش v2rayNG)
# ==========================================
def is_valid_vless_structure(config_url):
    """
    بررسی دقیق ساختار استاندارد vless://UUID@HOST:PORT
    """
    # الگوی ساختار استاندارد VLESS
    vless_regex = r'^vless://[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}@[a-zA-Z0-9\.\-\_\[\]]+:\d+'
    
    if not re.match(vless_regex, config_url, re.IGNORECASE):
        return False

    # بررسی معتبر بودن پورت (بین ۱ تا ۶۵۵۳۵)
    try:
        port_match = re.search(r'@(?:\[[a-fA-F0-9:]+\]|[^:]+):(\d+)', config_url)
        if port_match:
            port = int(port_match.group(1))
            if not (1 <= port <= 65535):
                return False
    except Exception:
        return False

    return True

# ==========================================
# تابع بررسی اینکه کانفیگ VLESS دارای TLS/Reality است
# ==========================================
def is_vless_tls_or_reality(config_url):
    url_lower = config_url.lower()

    if not url_lower.startswith("vless://"):
        return False

    if '?' not in config_url:
        return False

    try:
        query = config_url.split('?', 1)[1].split('#')[0]
        params = dict(param.split('=', 1) for param in query.split('&') if '=' in param)

        security = params.get('security', '').lower()

        if security in ('tls', 'reality', 'xtls'):
            return True

    except Exception:
        pass

    return False

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
        print(f"\n🌐 [صفحه {page_count}] دریافت اطلاعات از: {current_url}")

        try:
            response = session.get(current_url, timeout=25)

            if response.status_code == 429:
                print("  ⚠️ محدودیت درخواست! ۱۵ ثانیه صبر...")
                time.sleep(15)
                continue

            if response.status_code != 200:
                print(f"  ⚠️ وضعیت غیرعادی ({response.status_code}) - توقف کانال.")
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
                # پاکسازی کاراکترهای مخرب و مخفی UTF-8
                msg_text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff]', '', msg_text)
                msg_text = html.unescape(msg_text)

                found_configs = config_pattern.findall(msg_text)

                for config in found_configs:
                    # ۱. تمیز کردن علائم انتهای لینک
                    clean_config = clean_vless_url(config)
                    
                    # ۲. بررسی صحت ساختاری لینک (جلوگیری از کرش)
                    if is_valid_vless_structure(clean_config):
                        # ۳. بررسی TLS / Reality بودن
                        if is_vless_tls_or_reality(clean_config):
                            channel_configs.append(clean_config)

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
    
    # الگوی دریافت اولیه لینک
    config_pattern = re.compile(r'(?i)vless://[^\s\'"<>]+')

    all_extracted_configs = []

    for channel in CHANNELS:
        try:
            configs = scrape_channel(channel, cutoff_datetime, session, config_pattern)
            all_extracted_configs.extend(configs)
        except Exception as e:
            print(f"❌ خطا: {e}")

    # حذف موارد تکراری
    unique_configs = list(dict.fromkeys(all_extracted_configs))

    if unique_configs:
        renamed_list = []
        for idx, config in enumerate(unique_configs, 1):
            new_name = f"{CONFIG_PREFIX_NAME}{idx}"
            renamed_list.append(rename_config(config, new_name))

        # ساخت متن یکپارچه
        plain_text_content = "\n".join(renamed_list)

        # انکد به Base64
        b64_encoded_content = base64.b64encode(plain_text_content.encode('utf-8')).decode('utf-8')

        sub_filename = 'sub_vless_base64.txt'
        with open(sub_filename, 'w', encoding='utf-8') as f:
            f.write(b64_encoded_content)

        print("\n" + "=" * 65)
        print(f"✅ تعداد {len(renamed_list)} کانفیگ سالم و معتبر استخراج شد.")
        print(f"✅ فایل سابسکریپشن بدون مشکل ساختار ساخته شد: {sub_filename}")
        print("=" * 65)

    else:
        print("\n❌ هیچ کانفیگ معتبری یافت نشد.")


if __name__ == "__main__":
    scrape_all_channels()
