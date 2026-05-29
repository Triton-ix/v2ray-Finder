#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import os
import json
import re
import time
import random
import signal
import logging
import base64
import urllib.parse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Tuple
from urllib.parse import urlparse

import requests
import urllib3

# ============================================================
#                     تنظیمات قابل تغییر
# ============================================================

# تنظیمات جستجوی لینک‌های اشتراک
SUBSCRIPTION_SEARCH_DAYS_BACK = 5          # جستجوی لینک‌هایی که در X روز گذشته بروز شده‌اند
SUBSCRIPTION_MAX_SEARCH_PAGES = 5          # حداکثر تعداد صفحات جستجو (قابل تنظیم: 1 تا 10)
SUBSCRIPTION_DELAY_BETWEEN_SEARCH = 15     # تاخیر بین هر جستجو به ثانیه (قابل تنظیم)
SUBSCRIPTION_MAX_WORKERS = 1               # تعداد همزمانی (برای جلوگیری از خطا، تغییر ندهید)

# تنظیمات فایل خروجی
OUTPUT_UNIQUE_FILE = "Full_uniqe-config.txt"    # همه کانفیگ‌های یکتا
OUTPUT_RANDOM_FILE = "random-config.txt"        # کانفیگ‌های تست شده
RANDOM_CONFIG_COUNT = 2000                  # تعداد کانفیگ‌های تصادفی که تست می‌شوند
MAX_RESPONSE_TIME_MS = 200                  # حداکثر زمان پاسخ قابل قبول برای تست (میلی‌ثانیه)

# کلمات کلیدی اصلی برای جستجو
IRAN_KEYWORDS = ['iran', 'ایران', 'ir', 'persia', 'فارسی', 'farsi']

# الگوهای تشخیص لینک اشتراک
SUBSCRIPTION_PATTERNS = [
    r'(https?://raw\.githubusercontent\.com/[^\s"\'<>]+\.(txt|json|yml|yaml|link))',
    r'(https?://github\.com/[^\s"\'<>]+/raw/[^\s"\'<>]+)',
]

# هدرهای درخواست
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# ============================================================
# ============================================================

warnings.filterwarnings('ignore')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')

stop_processing = False

def signal_handler(sig, frame):
    global stop_processing
    stop_processing = True
    print("\n[!] Stopping...")

signal.signal(signal.SIGINT, signal_handler)

# Import colorama
try:
    from colorama import init, Fore, Style
    init(autoreset=True)
except ImportError:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'colorama'])
    from colorama import init, Fore, Style
    init(autoreset=True)

def color_print(text, color=Fore.WHITE, style=Style.NORMAL):
    print(f"{style}{color}{text}{Style.RESET_ALL}")


# ============================================================
# بخش 1: یافتن لینک‌های اشتراک
# ============================================================

def is_within_days(date_obj, days):
    if not date_obj:
        return False
    now = datetime.now(date_obj.tzinfo) if date_obj.tzinfo else datetime.now()
    return (now - date_obj) <= timedelta(days=days)


def build_search_queries() -> List[str]:
    """ساخت جستجوهای محدود برای جلوگیری از خطا"""
    return [
        "v2ray iran",
        "v2ray ایران",
        "v2ray config iran",
        "کانفیگ v2ray ایران",
    ]


def search_github_repos(session, seen_repos):
    """جستجو با تاخیر قابل تنظیم بین درخواست‌ها"""
    repos = []
    search_queries = build_search_queries()
    
    logging.info(f"شروع جستجو با {len(search_queries)} عبارت (تاخیر {SUBSCRIPTION_DELAY_BETWEEN_SEARCH} ثانیه)")
    
    for q in search_queries:
        if stop_processing:
            break
        
        color_print(f"\n[*] جستجو: '{q}'", Fore.CYAN)
        logging.info(f"انتظار {SUBSCRIPTION_DELAY_BETWEEN_SEARCH} ثانیه قبل از درخواست بعدی...")
        time.sleep(SUBSCRIPTION_DELAY_BETWEEN_SEARCH)
        
        for page in range(1, SUBSCRIPTION_MAX_SEARCH_PAGES + 1):
            if stop_processing:
                break
            try:
                url = f'https://api.github.com/search/repositories?q={q}&page={page}&per_page=20&sort=updated&order=desc'
                resp = session.get(url, timeout=30)
                
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get('items', [])
                    if not items:
                        break
                    
                    for repo in items:
                        name = repo['full_name']
                        if name not in seen_repos:
                            seen_repos.add(name)
                            updated_at = repo.get('updated_at', '')
                            last_update = None
                            if updated_at:
                                try:
                                    last_update = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                                except:
                                    pass
                            
                            if last_update and is_within_days(last_update, SUBSCRIPTION_SEARCH_DAYS_BACK):
                                repos.append({
                                    'name': name,
                                    'url': repo['html_url'],
                                    'updated_at': updated_at,
                                    'description': repo.get('description', ''),
                                })
                                color_print(f"  + {name}", Fore.GREEN)
                elif resp.status_code == 403:
                    logging.warning(f"محدودیت درخواست برای '{q}'، انتظار 90 ثانیه...")
                    time.sleep(90)
                    break
                
                if page < SUBSCRIPTION_MAX_SEARCH_PAGES:
                    time.sleep(2)
                    
            except Exception as e:
                logging.error(f"خطا در جستجوی '{q}': {e}")
                continue
    
    logging.info(f"تعداد ریپازیتوری‌های پیدا شده: {len(repos)}")
    return repos


def has_iran_keywords_in_text(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in IRAN_KEYWORDS)


def extract_links_from_repo(session, repo_url):
    """استخراج لینک‌های اشتراک از ریپازیتوری"""
    all_links = {}
    repo_path = repo_url.replace('https://github.com/', '')
    
    paths_to_check = ['README.md', 'sub.txt', 'subscription.txt', 'config.txt', 'v2ray.txt']
    
    for branch in ['main', 'master']:
        for path in paths_to_check:
            raw_url = f'https://raw.githubusercontent.com/{repo_path}/{branch}/{path}'
            try:
                resp = session.get(raw_url, timeout=15)
                if resp.status_code == 200:
                    content = resp.text
                    config_count = len([line for line in content.splitlines() if line.strip()])
                    
                    for pattern in SUBSCRIPTION_PATTERNS:
                        matches = re.findall(pattern, content, re.IGNORECASE)
                        for m in matches:
                            if isinstance(m, tuple):
                                m = m[0] if m else ''
                            if m and 'raw.githubusercontent.com' in m:
                                if m not in all_links or config_count > all_links[m][1]:
                                    all_links[m] = (raw_url, config_count)
            except Exception:
                continue
    
    if all_links:
        best_link = max(all_links.items(), key=lambda x: x[1][1])
        return {best_link[0]}
    return set()


def check_repository(session, repo_info, subscription_links):
    """بررسی یک ریپازیتوری و استخراج بهترین لینک"""
    repo_url = repo_info['url']
    repo_name = repo_info['name']
    description = repo_info.get('description', '') or ''
    
    try:
        # بررسی توضیحات یا README
        if not has_iran_keywords_in_text(description):
            try:
                for branch in ['main', 'master']:
                    readme_url = f'https://raw.githubusercontent.com/{repo_name}/{branch}/README.md'
                    readme_resp = session.get(readme_url, timeout=10)
                    if readme_resp.status_code == 200:
                        if has_iran_keywords_in_text(readme_resp.text[:1000]):
                            break
                    else:
                        return False
            except:
                return False
        
        # استخراج لینک
        links = extract_links_from_repo(session, repo_url)
        if links:
            subscription_links.update(links)
            logging.info(f"لینک یافت شد از {repo_name}")
            return True
            
    except Exception as e:
        logging.error(f"خطا در بررسی {repo_name}: {e}")
    
    return False


def find_subscription_links():
    """تابع اصلی برای یافتن لینک‌های اشتراک"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("مرحله 1: جستجوی لینک‌های اشتراک از گیت‌هاب", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    print(f"[*] جستجو در {SUBSCRIPTION_SEARCH_DAYS_BACK} روز گذشته")
    print(f"[*] حداکثر صفحات: {SUBSCRIPTION_MAX_SEARCH_PAGES}")
    print(f"[*] تاخیر بین جستجوها: {SUBSCRIPTION_DELAY_BETWEEN_SEARCH} ثانیه\n")
    
    seen_repos = set()
    repos = search_github_repos(session, seen_repos)
    
    if not repos:
        print("[!] ریپازیتوری یافت نشد")
        return []
    
    print(f"[*] بررسی {len(repos)} ریپازیتوری...")
    subscription_links = set()
    
    with ThreadPoolExecutor(max_workers=SUBSCRIPTION_MAX_WORKERS) as executor:
        futures = {executor.submit(check_repository, session, repo, subscription_links): repo for repo in repos}
        for future in as_completed(futures):
            if stop_processing:
                break
            try:
                future.result(timeout=45)
            except Exception as e:
                logging.error(f"خطا در future: {e}")
    
    # اعتبارسنجی لینک‌ها (رفع خطای 404)
    valid_links = []
    for link in list(subscription_links):
        if stop_processing:
            break
        try:
            resp = session.head(link, timeout=10, allow_redirects=True)
            if resp.status_code < 400:
                valid_links.append(link)
                color_print(f"  ✓ لینک معتبر: {link[:80]}...", Fore.GREEN)
            else:
                color_print(f"  ✗ لینک نامعتبر ({resp.status_code}): {link[:80]}...", Fore.RED)
        except Exception as e:
            color_print(f"  ✗ خطا در اتصال: {link[:80]}...", Fore.RED)
    
    unique_links = list(set(valid_links))
    color_print(f"\n[✓] تعداد لینک‌های معتبر: {len(unique_links)}", Fore.GREEN)
    
    return unique_links


# ============================================================
# بخش 2: استخراج کانفیگ از لینک‌ها
# ============================================================

def fetch_configs_from_link(session, url, retries=2):
    """دریافت کانفیگ از لینک با قابلیت تکرار"""
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20, headers=HEADERS)
            resp.raise_for_status()
            content = resp.text.strip().splitlines()
            configs = [line.strip() for line in content if line.strip()]
            if configs:
                color_print(f"    ✓ دریافت {len(configs)} کانفیگ", Fore.GREEN)
            return configs
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                color_print(f"    ✗ لینک خراب (404): {url[:50]}...", Fore.RED)
                return []
            elif attempt < retries - 1:
                logging.warning(f"تکرار {attempt+1} برای {url[:50]}")
                time.sleep(2)
            else:
                logging.error(f"خطا در دریافت {url[:50]}: {e}")
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                logging.error(f"خطا در دریافت {url[:50]}: {e}")
    return []


def fetch_all_configs(subscription_links):
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("مرحله 2: دریافت و حذف کانفیگ‌های تکراری", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    all_configs = []
    total_fetched = 0
    
    for i, link in enumerate(subscription_links, 1):
        if stop_processing:
            break
        print(f"[{i}/{len(subscription_links)}] دریافت از: {link[:70]}...")
        configs = fetch_configs_from_link(session, link)
        if configs:
            total_fetched += len(configs)
            all_configs.extend(configs)
        time.sleep(1)
    
    print(f"\n[*] کل کانفیگ‌های دریافت شده: {total_fetched}")
    
    unique_configs = list(set(all_configs))
    duplicates_removed = total_fetched - len(unique_configs)
    print(f"[*] کانفیگ‌های یکتا: {len(unique_configs)}")
    print(f"[*] کانفیگ‌های تکراری حذف شده: {duplicates_removed}")
    
    # ذخیره همه کانفیگ‌های یکتا
    with open(OUTPUT_UNIQUE_FILE, 'w', encoding='utf-8') as f:
        for cfg in unique_configs:
            f.write(cfg + '\n')
    color_print(f"\n[✓] همه کانفیگ‌های یکتا در {OUTPUT_UNIQUE_FILE} ذخیره شدند", Fore.GREEN)
    
    return unique_configs


# ============================================================
# بخش 3: تست کانفیگ‌ها و ذخیره تصادفی
# ============================================================

def test_single_config(config_line: str) -> Tuple[Optional[str], Optional[float]]:
    """تست یک کانفیگ با اتصال واقعی و اندازه‌گیری تاخیر"""
    try:
        # استخراج پروتکل و آدرس
        if config_line.startswith('vless://'):
            parsed = urlparse(config_line)
            host = parsed.hostname
            port = parsed.port
        elif config_line.startswith('vmess://'):
            encoded = config_line.replace('vmess://', '')
            encoded += '=' * (-len(encoded) % 4)
            decoded = base64.b64decode(encoded).decode('utf-8')
            data = json.loads(decoded)
            host = data.get('add')
            port = int(data.get('port', 0))
        elif config_line.startswith('trojan://'):
            parsed = urlparse(config_line)
            host = parsed.hostname
            port = parsed.port
        else:
            return None, None
        
        if not host or not port:
            return None, None
        
        # تست اتصال ساده
        test_url = f"http://{host}:{port}/"
        start_time = time.time()
        
        # تلاش برای اتصال به پورت
        with socket.create_connection((host, port), timeout=5):
            elapsed_ms = (time.time() - start_time) * 1000
            if elapsed_ms <= MAX_RESPONSE_TIME_MS:
                return config_line, elapsed_ms
        
    except Exception:
        pass
    
    return None, None


def test_and_save_random_configs(unique_configs: List[str]) -> int:
    """انتخاب تصادفی کانفیگ‌ها، تست و ذخیره کانفیگ‌های سالم"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("مرحله 3: تست کانفیگ‌های تصادفی", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    total_available = len(unique_configs)
    print(f"[*] کل کانفیگ‌های یکتا: {total_available}")
    print(f"[*] هدف: تست {RANDOM_CONFIG_COUNT} کانفیگ تصادفی")
    print(f"[*] حداکثر تاخیر مجاز: {MAX_RESPONSE_TIME_MS} میلی‌ثانیه\n")
    
    if total_available <= RANDOM_CONFIG_COUNT:
        sample_configs = unique_configs.copy()
        print(f"[*] تعداد کافی کانفیگ وجود ندارد ({total_available})، همه کانفیگ‌ها تست می‌شوند")
    else:
        sample_configs = random.sample(unique_configs, RANDOM_CONFIG_COUNT)
        print(f"[*] {RANDOM_CONFIG_COUNT} کانفیگ به صورت تصادفی انتخاب شدند")
    
    working_configs = []
    tested_count = 0
    
    for config_line in sample_configs:
        if stop_processing:
            break
        
        tested_count += 1
        result, response_time = test_single_config(config_line)
        
        if result and response_time:
            working_configs.append(config_line)
            print(f"\r[{tested_count}/{len(sample_configs)}] ✓ کانفیگ سالم! تاخیر: {response_time:.0f}ms | مجموع: {len(working_configs)}", flush=True)
        else:
            print(f"\r[{tested_count}/{len(sample_configs)}] ✗ کانفیگ خراب | سالم: {len(working_configs)}", end='', flush=True)
        
        # تاخیر کوتاه بین تست‌ها
        time.sleep(0.3)
    
    print()
    
    if not working_configs:
        color_print("\n[!] هیچ کانفیگ سالمی یافت نشد!", Fore.RED)
        return 0
    
    # ذخیره کانفیگ‌های سالم در فایل خروجی
    with open(OUTPUT_RANDOM_FILE, 'w', encoding='utf-8') as f:
        for config_line in working_configs:
            f.write(config_line + '\n')
    
    color_print(f"\n[✓] {len(working_configs)} کانفیگ سالم در {OUTPUT_RANDOM_FILE} ذخیره شدند", Fore.GREEN)
    return len(working_configs)


# ============================================================
# بخش اصلی
# ============================================================

def git_commit_and_push():
    """Commit و Push فایل‌های خروجی"""
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False)
        
        for file in [OUTPUT_UNIQUE_FILE, OUTPUT_RANDOM_FILE]:
            if os.path.exists(file):
                subprocess.run(["git", "add", file], check=False)
        
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if result.returncode != 0:
            commit_msg = f"Auto-update configs - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            subprocess.run(["git", "commit", "-m", commit_msg], check=False)
            subprocess.run(["git", "push"], check=False)
            color_print("[✓] فایل‌ها در گیت‌هاب ذخیره شدند", Fore.GREEN)
        else:
            color_print("[*] تغییری برای ذخیره وجود ندارد", Fore.CYAN)
    except Exception as e:
        logging.error(f"خطا در گیت: {e}")


def main():
    global stop_processing
    stop_processing = False
    
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("مدیریت کانفیگ‌های V2RAY", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    start_time = time.time()
    
    try:
        # مرحله 1: یافتن لینک‌های اشتراک
        subscription_links = find_subscription_links()
        if not subscription_links:
            color_print("\n[!] هیچ لینک اشتراکی یافت نشد", Fore.RED)
            sys.exit(1)
        
        # مرحله 2: دریافت و پاکسازی کانفیگ‌ها
        unique_configs = fetch_all_configs(subscription_links)
        if not unique_configs:
            color_print("\n[!] هیچ کانفیگی استخراج نشد", Fore.RED)
            sys.exit(1)
        
        # مرحله 3: تست کانفیگ‌های تصادفی
        saved_count = test_and_save_random_configs(unique_configs)
        
        # گزارش نهایی
        elapsed = time.time() - start_time
        color_print("\n" + "="*60, Fore.CYAN)
        color_print("گزارش نهایی", Fore.YELLOW, Style.BRIGHT)
        color_print("="*60, Fore.CYAN)
        print(f"  لینک‌های اشتراک معتبر: {len(subscription_links)}")
        print(f"  کانفیگ‌های یکتا: {len(unique_configs)}")
        print(f"  کانفیگ‌های سالم و تست شده: {saved_count}")
        print(f"  فایل خروجی کامل: {OUTPUT_UNIQUE_FILE}")
        print(f"  فایل خروجی تست شده: {OUTPUT_RANDOM_FILE}")
        print(f"  زمان اجرا: {elapsed:.1f} ثانیه")
        color_print("="*60, Fore.CYAN)
        
        # ذخیره در گیت‌هاب
        git_commit_and_push()
        
    except Exception as e:
        logging.error(f"خطای مهلک: {e}")
        import traceback
        traceback.print_exc()
        color_print(f"\n[ERROR] {e}", Fore.RED)
        sys.exit(1)


if __name__ == "__main__":
    main()
