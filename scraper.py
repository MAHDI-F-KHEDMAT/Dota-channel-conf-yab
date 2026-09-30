import re
import time
import html
import base64
import requests
import urllib.parse
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# فهماندن پروتکل vless به کتابخانه استاندارد پایتون (بسیار مهم برای جلوگیری از خطای پارس)
urllib.parse.uses_netloc.append('vless')

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
# تابع فیلتر ضد کرش (Bulletproof Validator)
# ==========================================
def is_bulletproof_vless(raw_url):
    """
    این تابع تمام پارامترهایی که باعث کرش کردن Xray-Core در v2rayNG می‌شوند را بررسی می‌کند.
    """
    try:
        parsed = urlparse(raw_url)
        
        # ۱. بررسی آدرس و پورت
        if not parsed.hostname or not parsed.port:
            return False
        if not (1 <= parsed.port <= 65535):
            return False
            
        # ۲. بررسی پارامترهای کوئری
        qs = parse_qs(parsed.query)
        params = {k.lower(): v[0] for k, v in qs.items()}
        
        net_type = params.get('type', 'tcp').lower()
        security = params.get('security', 'none').lower()
        flow = params.get('flow', '').lower()
        
        if security not in ('none', 'tls', 'xtls', 'reality'):
            return False
            
        # ⚠️ ۳. جلوگیری از کرش Reality (مهم‌ترین بخش)
        if security == 'reality':
            pbk = params.get('pbk', '')
            # کلید pbk در Reality باید دقیقاً ۴۳ کاراکتر Base64Url باشد
            if not pbk or not re.match(r'^[A-Za-z0-9\-_]{43}$', pbk):
                return False
                
            sid = params.get('sid', '')
            # شناسه sid باید حتماً کد Hex (0-9, a-f) و زوج باشد (2, 4, 6, 8 کاراکتر)
            if sid and not re.match(r'^([0-9a-fA-F]{2}){1,8}$', sid):
                return False
                
            sni = params.get('sni', '')
            if not sni:
                return False
                
        # ⚠️ ۴. جلوگیری از کرش Vision
        if 'vision' in flow:
            if net_type != 'tcp' or security not in ('tls', 'xtls', 'reality'):
                return False
                
        return True
    except Exception:
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
                msg_text = html.unescape(msg_text)

                # استخراج لینک با رجکس هوشمند
                found_configs = config_pattern.findall(msg_text)

                for raw_config in found_configs:
                    # بررسی تخصصی ضد کرش
                    if is_bulletproof_vless(raw_config):
                        channel_configs.append(raw_config)

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
    
    # ⚠️ رجکس جدید: این رجکس اصلاً نام (Remark) را بعد از # استخراج نمی‌کند!
    # در نتیجه هیچ ایموجی یا کاراکتر فارسی خرابی وارد لینک نمی‌شود.
    config_pattern = re.compile(r'vless://[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}@[^\s\'"<>#]+')

    all_extracted_configs = []

    for channel in CHANNELS:
        try:
            configs = scrape_channel(channel, cutoff_datetime, session, config_pattern)
            all_extracted_configs.extend(configs)
        except Exception as e:
            print(f"❌ خطا: {e}")

    # حذف تکراری‌ها
    unique_configs = list(dict.fromkeys(all_extracted_configs))

    if unique_configs:
        renamed_list = []
        for idx, config in enumerate(unique_configs, 1):
            # اضافه کردن نام جدید و تمیز به انتهای لینک
            new_name = f"{CONFIG_PREFIX_NAME}{idx}"
            renamed_list.append(f"{config}#{new_name}")

        plain_text_content = "\n".join(renamed_list)
        b64_encoded_content = base64.b64encode(plain_text_content.encode('utf-8')).decode('utf-8')

        sub_filename = 'sub_vless_base64.txt'
        with open(sub_filename, 'w', encoding='utf-8') as f:
            f.write(b64_encoded_content)

        print("\n" + "=" * 65)
        print(f"✅ تعداد {len(renamed_list)} کانفیگ تایید شده (بدون خطر کرش) استخراج شد.")
        print(f"✅ فایل سابسکریپشن ساخته شد: {sub_filename}")
        print("=" * 65)

    else:
        print("\n❌ هیچ کانفیگ استانداردی یافت نشد. (همه دارای خطای ساختاری بودند)")


if __name__ == "__main__":
    scrape_all_channels()
