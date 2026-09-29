import re
import time
import html
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote

# ==========================================
# ⚙️ تنظیمات اسکریپت
# ==========================================
DAYS_BACK = 2
CONFIG_PREFIX_NAME = "ConfigsHUB_VIP_"  # پیشوند اسم کانفیگ‌ها (مثلا ConfigsHUB_VIP_1)

# لیست کانال‌ها
CHANNELS = [
    "https://t.me/ConfigsHUB"
]

# ==========================================
# تابع تغییر نام کانفیگ (مخصوص VLESS)
# ==========================================
def rename_config(config_url, new_name):
    try:
        # حذف نام قبلی در صورت وجود (حذف هر چیزی بعد از #)
        base_url = config_url.split('#')[0]
        # انکد کردن نام جدید برای استفاده در URL (جلوگیری از خرابی لینک در صورت داشتن فاصله)
        encoded_name = quote(new_name)
        return f"{base_url}#{encoded_name}"
    except Exception:
        return config_url

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
        params = parse_qs(query)
        
        # گرفتن پارامتر security
        security = params.get('security', [''])[0].lower()

        # بررسی اینکه آیا امنیت یکی از موارد زیر است
        if security in ('tls', 'reality', 'xtls'):
            return True

    except Exception:
        pass

    return False


# ==========================================
# تابع بررسی هر کانال با لاگ لحظه‌ای
# ==========================================
def scrape_channel(channel_url, cutoff_datetime, session, config_pattern):

    if "/s/" not in channel_url:
        channel_url = channel_url.replace("t.me/", "t.me/s/")

    current_url = channel_url
    reached_old = False
    page_count = 0
    channel_configs = []
    total_raw = 0
    accepted_count = 0
    rejected_count = 0
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
                print("  ⚠️ محدودیت درخواست (Rate Limit)! ۱۵ ثانیه صبر...")
                time.sleep(15)
                continue

            if response.status_code != 200:
                print(f"  ⚠️ وضعیت غیرعادی ({response.status_code}) - توقف این کانال.")
                break

            response.raise_for_status()

        except Exception as e:
            print(f"  ❌ خطا در اتصال: {e}")
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        messages = soup.find_all('div', class_='tgme_widget_message')

        if not messages:
            print("  ℹ️ پیامی در این صفحه یافت نشد.")
            break

        print(f"  🔍 پیدا شدن {len(messages)} پیام. در حال بررسی...")
        page_min_id = None
        configs_in_this_page = 0

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

            # بررسی محدوده زمانی تعیین‌شده
            if msg_dt < cutoff_datetime:
                print(f"  ⏰ رسیدن به پیام قدیمی‌تر از بازه مجاز ({msg_dt.strftime('%Y-%m-%d %H:%M')}). توقف این کانال.")
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

                # جستجوی کانفیگ‌های vless
                found_configs = config_pattern.findall(msg_text)

                for config in found_configs:
                    config = config.strip()
                    total_raw += 1
                    configs_in_this_page += 1

                    preview = config[:55] + "..." if len(config) > 55 else config

                    # فیلتر فقط VLESS های TLS یا Reality
                    if is_vless_tls_or_reality(config):
                        channel_configs.append(config)
                        accepted_count += 1
                        print(f"    ✅ [تایید TLS/Reality] {preview}")
                    else:
                        rejected_count += 1
                        print(f"    ❌ [رد شد - غیرمجاز]   {preview}")

        print(f"  📊 آمار صفحه {page_count}: {configs_in_this_page} کانفیگ Vless شناسایی شد.")

        if reached_old or not page_min_id:
            break

        if page_min_id in seen_min_ids:
            print("  ℹ️ شناسه تکراری دریافت شد (انتهای صفحات کانال).")
            break

        seen_min_ids.add(page_min_id)
        current_url = f"{channel_url}?before={page_min_id}"
        time.sleep(1.5)

    print(
        f"\n✔️ پایان کانال | "
        f"کل Vless یافت شده: {total_raw} | "
        f"تایید شده (TLS/Reality): {accepted_count} | "
        f"رد شده: {rejected_count}"
    )

    return channel_configs


# ==========================================
# تابع اصلی مدیریت
# ==========================================
def scrape_all_channels():

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    })

    cutoff_datetime = datetime.utcnow() - timedelta(days=DAYS_BACK)

    # الگوی جستجو تغییر کرد: فقط vless استخراج می‌شود
    config_pattern = re.compile(r'(?i)vless://[^\s\'"<>]+')

    all_extracted_configs = []

    print("=" * 65)
    print(
        f"🚀 شروع استخراج Vless های (TLS/Reality) از {len(CHANNELS)} کانال\n"
        f"📅 محدوده زمانی: {DAYS_BACK} روز گذشته (از {cutoff_datetime.strftime('%Y-%m-%d %H:%M')} به بعد)"
    )
    print("=" * 65)

    for index, channel in enumerate(CHANNELS, 1):
        print(f"\n[کانال {index} از {len(CHANNELS)}]")
        try:
            configs = scrape_channel(
                channel,
                cutoff_datetime,
                session,
                config_pattern
            )
            all_extracted_configs.extend(configs)

        except Exception as e:
            print(f"❌ خطای غیرپیش‌بینی‌شده در کانال {channel}: {e}")
            continue

    unique_configs = list(dict.fromkeys(all_extracted_configs))

    print("\n" + "=" * 65)
    print("📊 آمار کلی نهایی:")
    print(f"  تعداد کل پردازش شده: {len(all_extracted_configs)}")
    print(f"  تعداد کانفیگ‌های یکتا (غیرتکراری): {len(unique_configs)}")

    if unique_configs:
        filename = f'Vless_TLS_Reality_{DAYS_BACK}days.txt'

        with open(filename, 'w', encoding='utf-8') as f:
            for idx, config in enumerate(unique_configs, 1):
                new_name = f"{CONFIG_PREFIX_NAME}{idx}"
                renamed_config = rename_config(config, new_name)
                f.write(renamed_config + '\n\n')

        print(f"\n✅ فایل نهایی ذخیره شد: {filename}")
        print("=" * 65)

    else:
        print("\n❌ هیچ کانفیگ Vless مطابق با شرایط یافت نشد.")
        print("=" * 65)


if __name__ == "__main__":
    scrape_all_channels()
