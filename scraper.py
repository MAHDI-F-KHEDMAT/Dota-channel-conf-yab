import re
import time
import json
import html
import base64
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import parse_qs

# ==========================================
# ⚙️ تنظیمات اسکریپت
# ==========================================
DAYS_BACK = 2
ONLY_TLS_REALITY = True

# لیست کانال‌ها
CHANNELS = [
    "https://t.me/ConfigsHUB"
]

# ==========================================
# تابع تشخیص TLS / Reality
# ==========================================
def is_tls_or_reality(config_url):
    url_lower = config_url.lower()

    if url_lower.startswith(("hysteria2://", "hy2://", "tuic://", "hysteria://")):
        return True

    # --- vless & trojan ---
    if url_lower.startswith(("vless://", "trojan://")):
        if '?' not in config_url:
            return False
        try:
            query = config_url.split('?', 1)[1].split('#')[0]
            params = parse_qs(query)
            security = params.get('security', [''])[0].lower()
            flow = params.get('flow', [''])[0].lower()

            if security in ('tls', 'reality', 'xtls') or 'xtls' in flow:
                return True

            if 'pbk' in params or 'sni' in params or 'sid' in params:
                return True

        except Exception:
            pass

        return False

    # --- vmess ---
    if url_lower.startswith("vmess://"):
        try:
            b64 = re.sub(r'(?i)^vmess://', '', config_url).split('#')[0].strip()
            b64 += '=' * (-len(b64) % 4)
            b64 = b64.replace('-', '+').replace('_', '/')

            decoded = base64.b64decode(b64).decode('utf-8', errors='ignore')
            data = json.loads(decoded)
            tls_val = str(data.get('tls', '')).lower()

            if tls_val in ('tls', 'reality', 'true', '1'):
                return True

        except Exception:
            pass

        return 'tls' in url_lower or 'reality' in url_lower

    # --- shadowsocks ---
    if url_lower.startswith(("ss://", "ssr://")):
        return 'plugin=' in url_lower and ('tls' in url_lower or 'obfs' in url_lower)

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

                found_configs = config_pattern.findall(msg_text)

                for config in found_configs:
                    config = config.strip()
                    total_raw += 1
                    configs_in_this_page += 1

                    # پیش‌نمایش کوتاه از کانفیگ جهت چاپ در لاگ
                    preview = config[:55] + "..." if len(config) > 55 else config

                    if ONLY_TLS_REALITY:
                        if is_tls_or_reality(config):
                            channel_configs.append(config)
                            accepted_count += 1
                            print(f"    ✅ [تایید TLS/Reality] {preview}")
                        else:
                            rejected_count += 1
                            print(f"    ❌ [رد شد - غیر TLS]   {preview}")
                    else:
                        channel_configs.append(config)
                        accepted_count += 1
                        print(f"    🔹 [استخراج شد]       {preview}")

        print(f"  📊 آمار صفحه {page_count}: {configs_in_this_page} کانفیگ شناسایی شد.")

        if reached_old or not page_min_id:
            break

        # جلوگیری از حلقه بی‌پایان
        if page_min_id in seen_min_ids:
            print("  ℹ️ شناسه تکراری دریافت شد (انتهای صفحات کانال).")
            break

        seen_min_ids.add(page_min_id)
        current_url = f"{channel_url}?before={page_min_id}"
        time.sleep(1.5)

    print(
        f"\n✔️ پایان کانال | "
        f"کل کانفیگ‌های شناسایی‌شده: {total_raw} | "
        f"تایید شده: {accepted_count} | "
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

    config_pattern = re.compile(
        r'(?i)(?:vless|vmess|trojan|ss|ssr|tuic|'
        r'hysteria2|hy2|hysteria|wireguard|juicity)'
        r'://[^\s\'"<>]+'
    )

    all_extracted_configs = []

    print("=" * 65)
    print(
        f"🚀 شروع استخراج کانفیگ‌ها از {len(CHANNELS)} کانال\n"
        f"📅 محدوده زمانی: {DAYS_BACK} روز گذشته (از {cutoff_datetime.strftime('%Y-%m-%d %H:%M')} به بعد)\n"
        f"🔒 فیلتر TLS/Reality: {'فعال' if ONLY_TLS_REALITY else 'غیرفعال'}"
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
        filename = f'Combined_TLS_Reality_{DAYS_BACK}days.txt' if ONLY_TLS_REALITY else f'Combined_Configs_{DAYS_BACK}days.txt'

        with open(filename, 'w', encoding='utf-8') as f:
            for config in unique_configs:
                f.write(config + '\n\n')

        print(f"\n✅ فایل نهایی ذخیره شد: {filename}")
        print("=" * 65)

    else:
        print("\n❌ هیچ کانفیگی مطابق با شرایط یافت نشد.")
        print("=" * 65)


if __name__ == "__main__":
    scrape_all_channels()
