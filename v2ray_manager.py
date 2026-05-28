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
import warnings
import traceback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, List, Set, Tuple
from urllib.parse import urlparse

import requests
import urllib3

# ============================================================
#                     تنظیمات قابل تغییر
# ============================================================

# تنظیمات جستجوی لینک‌های اشتراک
SUBSCRIPTION_SEARCH_DAYS_BACK = 5          # جستجوی لینک‌هایی که در X روز گذشته بروز شده‌اند
SUBSCRIPTION_MAX_SEARCH_PAGES = 5          # حداکثر تعداد صفحات جستجو (پیش‌فرض 5)
SUBSCRIPTION_DELAY_BETWEEN_QUERIES = 15    # تاخیر بین هر جستجو در گیت‌هاب (ثانیه) - پیش‌فرض 15
SUBSCRIPTION_MAX_WORKERS = 1               # تعداد همزمانی برای بررسی ریپازیتوری‌ها

# تنظیمات فایل خروجی
OUTPUT_FILE_FULL = "Full_uniqe-config.txt"     # همه کانفیگ‌های یکتا
OUTPUT_FILE_RANDOM = "random-config.txt"       # کانفیگ‌های رندوم
RANDOM_CONFIG_COUNT = 2000                     # تعداد کانفیگ‌های رندوم (قابل تنظیم)

# کلمات کلیدی اصلی برای جستجوی ریپازیتوری‌های ایرانی
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

stop_processing = False
DEBUG_LOG = "debug.log"

def signal_handler(sig, frame):
    global stop_processing
    stop_processing = True
    print("\n[!] Stopping...")

signal.signal(signal.SIGINT, signal_handler)

# Import colorama after installing if needed
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
# بخش 1: یافتن لینک‌های اشتراک (با تاخیر قابل تنظیم)
# ============================================================

def is_within_days(date_obj, days):
    if not date_obj:
        return False
    now = datetime.now(date_obj.tzinfo) if date_obj.tzinfo else datetime.now()
    return (now - date_obj) <= timedelta(days=days)

def build_search_queries() -> List[str]:
    """ساخت جستجوهای محدود برای جلوگیری از Rate Limit"""
    queries = [
        "v2ray iran",
        "v2ray ایران",
        "v2ray config iran",
        "کانفیگ v2ray ایران",
    ]
    return queries

def search_github_repos(session, seen_repos):
    """جستجو در گیت‌هاب با تاخیر قابل تنظیم بین هر درخواست"""
    repos = []
    search_queries = build_search_queries()
    
    logging.info(f"شروع جستجو با {len(search_queries)} عبارت (تاخیر {SUBSCRIPTION_DELAY_BETWEEN_QUERIES} ثانیه بین هر درخواست)")
    
    for q in search_queries:
        if stop_processing:
            break
        
        logging.info(f"در حال جستجوی: '{q}'")
        
        for page in range(1, SUBSCRIPTION_MAX_SEARCH_PAGES + 1):
            if stop_processing:
                break
            try:
                url = f'https://api.github.com/search/repositories?q={q}&page={page}&per_page=30&sort=updated&order=desc'
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
                elif resp.status_code == 403:
                    logging.warning(f"Rate limit برای '{q}'، انتظار 90 ثانیه...")
                    time.sleep(90)
                    break
                
                # تاخیر بین صفحات
                if page < SUBSCRIPTION_MAX_SEARCH_PAGES:
                    time.sleep(2)
                    
            except Exception as e:
                logging.error(f"خطا در جستجوی '{q}': {e}")
                continue
        
        # تاخیر بین هر جستجو (قابل تنظیم)
        if q != search_queries[-1]:
            logging.info(f"انتظار {SUBSCRIPTION_DELAY_BETWEEN_QUERIES} ثانیه قبل از درخواست بعدی...")
            time.sleep(SUBSCRIPTION_DELAY_BETWEEN_QUERIES)
    
    logging.info(f"{len(repos)} ریپازیتوری کاندید پیدا شد")
    return repos

def has_iran_keywords_in_text(text: str) -> bool:
    """بررسی وجود کلمات کلیدی ایران در متن"""
    if not text:
        return False
    text_lower = text.lower()
    for keyword in IRAN_KEYWORDS:
        if keyword.lower() in text_lower:
            return True
    return False

def extract_links_from_repo(session, repo_url, patterns):
    """استخراج لینک‌های اشتراک از ریپازیتوری"""
    all_links = {}
    repo_path = repo_url.replace('https://github.com/', '')
    
    paths_to_check = ['README.md', 'sub.txt', 'subscription.txt', 'config.txt', 'v2ray.txt']
    
    for branch in ['main', 'master']:
        for path in paths_to_check:
            raw_url = f'https://raw.githubusercontent.com/{repo_path}/{branch}/{path}'
            try:
                resp = session.get(raw_url, timeout=10)
                if resp.status_code == 200:
                    content = resp.text
                    lines_count = len([line for line in content.splitlines() if line.strip()])
                    
                    for pattern in patterns:
                        matches = re.findall(pattern, content, re.IGNORECASE)
                        for m in matches:
                            if isinstance(m, tuple):
                                m = m[0] if m else ''
                            if m and 'raw.githubusercontent.com' in m:
                                if m not in all_links or lines_count > all_links[m][1]:
                                    all_links[m] = (raw_url, lines_count)
            except:
                continue
    
    if all_links:
        best_link = max(all_links.items(), key=lambda x: x[1][1])
        return {best_link[0]}
    return set()

def check_repository(session, repo_info, patterns, subscription_links):
    """بررسی یک ریپازیتوری و استخراج بهترین لینک اشتراک"""
    repo_url = repo_info['url']
    repo_name = repo_info['name']
    description = repo_info.get('description', '') or ''
    
    try:
        if not has_iran_keywords_in_text(description):
            try:
                found = False
                for branch in ['main', 'master']:
                    readme_url = f'https://raw.githubusercontent.com/{repo_name}/{branch}/README.md'
                    readme_resp = session.get(readme_url, timeout=10)
                    if readme_resp.status_code == 200:
                        readme_content = readme_resp.text[:1000]
                        if has_iran_keywords_in_text(readme_content):
                            found = True
                            break
                if not found:
                    return False
            except:
                return False
        
        links = extract_links_from_repo(session, repo_url, patterns)
        
        if links:
            subscription_links.update(links)
            logging.info(f"لینک پیدا شد از {repo_name}")
            return True
            
    except Exception as e:
        logging.error(f"خطا در بررسی {repo_name}: {e}")
    
    return False

def find_subscription_links():
    """تابع اصلی برای یافتن لینک‌های اشتراک"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("مرحله 1: یافتن لینک‌های اشتراک از گیت‌هاب", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    print(f"[*] جستجوی ریپازیتوری‌های مرتبط با ایران (آخرین {SUBSCRIPTION_SEARCH_DAYS_BACK} روز)")
    print(f"[*] تعداد صفحات هر جستجو: {SUBSCRIPTION_MAX_SEARCH_PAGES}")
    print(f"[*] تاخیر بین جستجوها: {SUBSCRIPTION_DELAY_BETWEEN_QUERIES} ثانیه\n")
    
    seen_repos = set()
    repos = search_github_repos(session, seen_repos)
    
    if not repos:
        print("[!] ریپازیتوری‌ای پیدا نشد")
        return []
    
    print(f"[*] بررسی {len(repos)} ریپازیتوری...")
    subscription_links = set()
    
    with ThreadPoolExecutor(max_workers=SUBSCRIPTION_MAX_WORKERS) as executor:
        futures = {executor.submit(check_repository, session, repo, SUBSCRIPTION_PATTERNS, subscription_links): repo for repo in repos}
        for future in as_completed(futures):
            if stop_processing:
                break
            try:
                future.result(timeout=45)
            except Exception as e:
                logging.error(f"خطا: {e}")
    
    # اعتبارسنجی لینک‌ها و حذف لینک‌های خراب
    valid_links = []
    for link in list(subscription_links):
        if stop_processing:
            break
        try:
            resp = session.head(link, timeout=10, allow_redirects=True)
            if resp.status_code < 400:
                valid_links.append(link)
            else:
                logging.warning(f"لینک خراب (HTTP {resp.status_code}): {link[:80]}")
        except Exception as e:
            logging.warning(f"لینک غیرقابل دسترس: {link[:80]} - {str(e)[:50]}")
    
    unique_links = list(set(valid_links))
    color_print(f"\n[✓] {len(unique_links)} لینک اشتراک معتبر پیدا شد", Fore.GREEN)
    
    return unique_links

# ============================================================
# بخش 2: استخراج کانفیگ از لینک‌ها و حذف تکراری‌ها
# ============================================================

def fetch_configs_from_link(session, url, retries=2):
    """دریافت کانفیگ از یک لینک با مکانیزم تکرار"""
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20, headers=HEADERS)
            resp.raise_for_status()
            content = resp.text.strip().splitlines()
            configs = [line.strip() for line in content if line.strip() and ('://' in line or 'vless' in line or 'vmess' in line)]
            if configs:
                return configs
            else:
                logging.warning(f"لینک معتبر اما بدون کانفیگ: {url[:50]}")
                return []
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logging.warning(f"لینک 404 (پیدا نشد): {url[:50]}")
                return []
            elif attempt < retries - 1:
                logging.warning(f"تلاش مجدد {attempt+1} برای {url[:50]}")
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
    """دریافت همه کانفیگ‌ها و حذف تکراری‌ها"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("مرحله 2: دریافت و یکتاسازی کانفیگ‌ها", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    all_configs = []
    total_fetched = 0
    
    for i, link in enumerate(subscription_links, 1):
        if stop_processing:
            break
        print(f"[{i}/{len(subscription_links)}] دریافت از: {link[:60]}...")
        configs = fetch_configs_from_link(session, link)
        if configs:
            print(f"    ✓ {len(configs)} کانفیگ پیدا شد")
            total_fetched += len(configs)
            all_configs.extend(configs)
        time.sleep(1)
    
    print(f"\n[*] کل کانفیگ‌های دریافت شده: {total_fetched}")
    
    unique_configs = list(set(all_configs))
    duplicates_removed = total_fetched - len(unique_configs)
    print(f"[*] کانفیگ‌های یکتا: {len(unique_configs)}")
    print(f"[*] کانفیگ‌های تکراری حذف شده: {duplicates_removed}")
    
    return unique_configs

# ============================================================
# بخش 3: ذخیره کانفیگ‌ها (بدون تست Xray)
# ============================================================

def save_configs(unique_configs: List[str]) -> int:
    """ذخیره کانفیگ‌های یکتا در فایل اصلی و تولید فایل رندوم"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("مرحله 3: ذخیره کانفیگ‌ها", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    if not unique_configs:
        color_print("[!] کانفیگی برای ذخیره وجود ندارد!", Fore.RED)
        return 0
    
    # ذخیره همه کانفیگ‌های یکتا در فایل اصلی
    with open(OUTPUT_FILE_FULL, 'w', encoding='utf-8') as f:
        for config in unique_configs:
            f.write(config + '\n')
    
    color_print(f"[✓] {len(unique_configs)} کانفیگ یکتا در {OUTPUT_FILE_FULL} ذخیره شد", Fore.GREEN)
    
    # تولید فایل رندوم
    random_count = min(RANDOM_CONFIG_COUNT, len(unique_configs))
    if random_count > 0:
        random_configs = random.sample(unique_configs, random_count)
        with open(OUTPUT_FILE_RANDOM, 'w', encoding='utf-8') as f:
            for config in random_configs:
                f.write(config + '\n')
        color_print(f"[✓] {random_count} کانفیگ کاملاً رندوم در {OUTPUT_FILE_RANDOM} ذخیره شد", Fore.GREEN)
        color_print(f"[*] این فایل هر بار با کانفیگ‌های متفاوت به‌روز می‌شود", Fore.CYAN)
    else:
        color_print(f"[!] تعداد کانفیگ‌ها ({len(unique_configs)}) کمتر از درخواست شما ({RANDOM_CONFIG_COUNT}) است", Fore.YELLOW)
    
    return len(unique_configs)

# ============================================================
# بخش اصلی
# ============================================================

def git_commit_and_push():
    """Commit and push the output files"""
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False)
        subprocess.run(["git", "add", OUTPUT_FILE_FULL, OUTPUT_FILE_RANDOM], check=False)
        subprocess.run(["git", "commit", "-m", f"Auto-update configs - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"], check=False)
        subprocess.run(["git", "push"], check=False)
        color_print("[✓] فایل‌ها در گیت‌هاب به‌روز شدند", Fore.GREEN)
    except Exception as e:
        logging.error(f"Git error: {e}")

def main():
    global stop_processing
    stop_processing = False
    
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("V2RAY MANAGER - جمع‌آوری و یکتاسازی کانفیگ‌ها", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    start_time = time.time()
    
    try:
        subscription_links = find_subscription_links()
        if not subscription_links:
            color_print("\n[!] لینک اشتراکی پیدا نشد", Fore.RED)
            sys.exit(1)
        
        unique_configs = fetch_all_configs(subscription_links)
        if not unique_configs:
            color_print("\n[!] کانفیگی استخراج نشد", Fore.RED)
            sys.exit(1)
        
        saved_count = save_configs(unique_configs)
        
        elapsed = time.time() - start_time
        color_print("\n" + "="*60, Fore.CYAN)
        color_print("خلاصه اجرا", Fore.YELLOW, Style.BRIGHT)
        color_print("="*60, Fore.CYAN)
        print(f"  لینک‌های اشتراک پیدا شده: {len(subscription_links)}")
        print(f"  کانفیگ‌های یکتا: {len(unique_configs)}")
        print(f"  کانفیگ‌های ذخیره شده در فایل اصلی: {saved_count}")
        print(f"  کانفیگ‌های رندوم (در صورت وجود): {min(RANDOM_CONFIG_COUNT, len(unique_configs))}")
        print(f"  زمان اجرا: {elapsed:.1f} ثانیه")
        color_print("="*60, Fore.CYAN)
        
        if saved_count > 0:
            git_commit_and_push()
        
    except Exception as e:
        logging.error(f"خطای fatal: {e}")
        logging.error(traceback.format_exc())
        color_print(f"\n[ERROR] {e}", Fore.RED)
        sys.exit(1)

if __name__ == "__main__":
    main()
