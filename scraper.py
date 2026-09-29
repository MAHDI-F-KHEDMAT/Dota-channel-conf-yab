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
# تابع تغییر نام کانفیگ (مخصوص VLESS)
# ==========================================
def rename_config(config_url, new_name):
    try:
        # حذف نام قبلی و لایه‌روبی لینک
        base_url = config_url.split('#')[0].strip()
        # قرار دادن اسم جدید بدون کاراکترهای مخرب
        return f"{base_url}#{new_name}"
    except Exception:
        return config_url.strip()

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
                msg_text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff]', '', msg_text)
                msg_text = html.unescape(msg_text)

                found_configs = config_pattern.findall(msg_text)

                for config in found_configs:
                    config = config.strip()
                    if is_vless_tls_or_reality(config):
                        channel_configs.append(config)

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

    unique_configs = list(dict.fromkeys(all_extracted_configs))

    if unique_configs:
        renamed_list = []
        for idx, config in enumerate(unique_configs, 1):
            new_name = f"{CONFIG_PREFIX_NAME}{idx}"
            renamed_list.append(rename_config(config, new_name))

        # ۱. ساخت متن یکپارچه بدون خطوط خالی اضافه
        plain_text_content = "\n".join(renamed_list)

        # ۲. انکد کردن کل متن به فرمت استاندارد Base64
        b64_encoded_content = base64.b64encode(plain_text_content.encode('utf-8')).decode('utf-8')

        # ذخیره فایل مخصوص سابسکریپشن (این فایل را در گیتهاب آپلود کنید)
        sub_filename = f'sub_vless_base64.txt'
        with open(sub_filename, 'w', encoding='utf-8') as f:
            f.write(b64_encoded_content)

        print("\n" + "=" * 65)
        print(f"✅ فایل سابسکریپشن استاندارد ساخته شد: {sub_filename}")
        print("💡 محتوای این فایل را روی GitHub آپلود کنید تا برنامه‌ها کرش نکنند.")
        print("=" * 65)

    else:
        print("\n❌ هیچ کانفیگی یافت نشد.")


if __name__ == "__main__":
    scrape_all_channels()
