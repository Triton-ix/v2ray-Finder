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
import socket
import logging
import base64
import urllib.parse
import tempfile
import zipfile
import platform
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
SUBSCRIPTION_MAX_SEARCH_PAGES = 5          # حداکثر تعداد صفحات جستجو (پیشفرض 5 صفحه)
SUBSCRIPTION_REQUEST_DELAY_SECONDS = 3     # تاخیر بین درخواست‌های جستجو (ثانیه)
SUBSCRIPTION_MAX_WORKERS = 1               # تعداد همزمانی برای بررسی ریپازیتوری‌ها

# تنظیمات فایل‌های خروجی
OUTPUT_FULL_CONFIGS = "Full_uniqe-config.txt"    # تمام کانفیگ‌های یکتا (بدون تکرار)
OUTPUT_RANDOM_2000 = "2000-config.txt"           # 2000 کانفیگ کاملاً رندوم

# کلمات کلیدی اصلی برای جستجوی ریپازیتوری‌های ایرانی (باید در توضیحات باشند)
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
    """ساخت جستجوهای بسیار محدود برای جلوگیری از Rate Limit"""
    return [
        "v2ray iran",
        "v2ray ایران",
        "v2ray config iran",
        "کانفیگ v2ray ایران",
    ]


def search_github_repos(session, seen_repos):
    """Search GitHub with delays to avoid rate limiting"""
    repos = []
    search_queries = build_search_queries()
    
    logging.info(f"Starting search with {len(search_queries)} queries (delayed {SUBSCRIPTION_REQUEST_DELAY_SECONDS}s)")
    
    for q in search_queries:
        if stop_processing:
            break
        
        logging.info(f"Waiting {SUBSCRIPTION_REQUEST_DELAY_SECONDS} seconds before query: '{q}'")
        time.sleep(SUBSCRIPTION_REQUEST_DELAY_SECONDS)
        
        for page in range(1, SUBSCRIPTION_MAX_SEARCH_PAGES + 1):
            if stop_processing:
                break
            try:
                url = f'https://api.github.com/search/repositories?q={q}&page={page}&per_page=20&sort=updated&order=desc'
                logging.info(f"Requesting page {page}: {url[:80]}...")
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
                    logging.warning(f"Rate limit hit for '{q}', waiting 90 seconds...")
                    time.sleep(90)
                    break
                
                # تاخیر بین صفحات
                if page < SUBSCRIPTION_MAX_SEARCH_PAGES:
                    time.sleep(SUBSCRIPTION_REQUEST_DELAY_SECONDS)
                    
            except Exception as e:
                logging.error(f"Search error for '{q}': {e}")
                continue
    
    logging.info(f"Found {len(repos)} candidate repositories")
    return repos


def has_iran_keywords_in_text(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for keyword in IRAN_KEYWORDS:
        if keyword.lower() in text_lower:
            return True
    return False


def extract_links_from_repo(session, repo_url, patterns):
    """Extract subscription links from repository files"""
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
                    
                    for pattern in patterns:
                        matches = re.findall(pattern, content, re.IGNORECASE)
                        for m in matches:
                            if isinstance(m, tuple):
                                m = m[0] if m else ''
                            if m and 'raw.githubusercontent.com' in m:
                                if m not in all_links or config_count > all_links[m][1]:
                                    all_links[m] = (raw_url, config_count)
            except:
                continue
    
    if all_links:
        best_link = max(all_links.items(), key=lambda x: x[1][1])
        return {best_link[0]}
    return set()


def check_repository(session, repo_info, patterns, subscription_links):
    repo_url = repo_info['url']
    repo_name = repo_info['name']
    description = repo_info.get('description', '') or ''
    
    try:
        if not has_iran_keywords_in_text(description):
            try:
                for branch in ['main', 'master']:
                    readme_url = f'https://raw.githubusercontent.com/{repo_name}/{branch}/README.md'
                    readme_resp = session.get(readme_url, timeout=10)
                    if readme_resp.status_code == 200:
                        readme_content = readme_resp.text[:1000]
                        if has_iran_keywords_in_text(readme_content):
                            break
                    else:
                        return False
            except:
                return False
        
        links = extract_links_from_repo(session, repo_url, patterns)
        if links:
            subscription_links.update(links)
            logging.info(f"Found link from {repo_name}")
            return True
    except Exception as e:
        logging.error(f"Error checking {repo_name}: {e}")
    return False


def find_subscription_links():
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 1: Finding subscription links from GitHub", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    print(f"[*] Searching (last {SUBSCRIPTION_SEARCH_DAYS_BACK} days, {SUBSCRIPTION_MAX_SEARCH_PAGES} pages, {SUBSCRIPTION_REQUEST_DELAY_SECONDS}s delay)...")
    
    seen_repos = set()
    repos = search_github_repos(session, seen_repos)
    
    if not repos:
        print("[!] No repositories found")
        return []
    
    print(f"[*] Checking {len(repos)} repositories...")
    subscription_links = set()
    
    with ThreadPoolExecutor(max_workers=SUBSCRIPTION_MAX_WORKERS) as executor:
        futures = {executor.submit(check_repository, session, repo, SUBSCRIPTION_PATTERNS, subscription_links): repo for repo in repos}
        for future in as_completed(futures):
            if stop_processing:
                break
            try:
                future.result(timeout=45)
            except Exception as e:
                logging.error(f"Future error: {e}")
    
    valid_links = []
    for link in list(subscription_links):
        try:
            # بررسی ساده برای جلوگیری از خطای 404
            resp = session.head(link, timeout=10, allow_redirects=True)
            if resp.status_code < 400:
                valid_links.append(link)
            else:
                logging.warning(f"Skipping unreachable link: {link[:80]} (status {resp.status_code})")
        except Exception as e:
            logging.warning(f"Skipping problematic link: {link[:80]} - {str(e)[:50]}")
            continue
    
    unique_links = list(set(valid_links))
    color_print(f"\n[✓] Found {len(unique_links)} valid subscription links", Fore.GREEN)
    return unique_links


# ============================================================
# بخش 2: استخراج کانفیگ از لینک‌ها
# ============================================================

def fetch_configs_from_link(session, url, retries=2):
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20, headers=HEADERS)
            resp.raise_for_status()
            content = resp.text.strip().splitlines()
            return [line.strip() for line in content if line.strip()]
        except Exception as e:
            if attempt < retries - 1:
                logging.warning(f"Retry {attempt+1} for {url[:50]}")
                time.sleep(2)
            else:
                if hasattr(e, 'response') and e.response is not None and e.response.status_code == 404:
                    logging.warning(f"Skipping 404 link: {url[:80]}")
                else:
                    logging.error(f"Failed to fetch {url[:50]}: {e}")
    return []


def fetch_all_configs(subscription_links):
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 2: Fetching and deduplicating configs", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    all_configs = []
    total_fetched = 0
    
    for i, link in enumerate(subscription_links, 1):
        if stop_processing:
            break
        print(f"[{i}/{len(subscription_links)}] Fetching: {link[:60]}...")
        configs = fetch_configs_from_link(session, link)
        if configs:
            print(f"    Found {len(configs)} configs")
            total_fetched += len(configs)
            all_configs.extend(configs)
        time.sleep(1)
    
    print(f"\n[*] Total configs fetched: {total_fetched}")
    
    unique_configs = list(set(all_configs))
    duplicates_removed = total_fetched - len(unique_configs)
    print(f"[*] Unique configs: {len(unique_configs)}")
    print(f"[*] Duplicates removed: {duplicates_removed}")
    
    # ذخیره تمام کانفیگ‌های یکتا در فایل اصلی
    with open(OUTPUT_FULL_CONFIGS, 'w', encoding='utf-8') as f:
        for cfg in unique_configs:
            f.write(cfg + '\n')
    color_print(f"[✓] Saved all unique configs to {OUTPUT_FULL_CONFIGS}", Fore.GREEN)
    
    return unique_configs


# ============================================================
# بخش 3: ساخت فایل 2000 کانفیگ رندوم
# ============================================================

def create_random_2000_configs(unique_configs: List[str]):
    """Create a file with 2000 completely random configs"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 3: Creating random 2000 configs", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    total_available = len(unique_configs)
    print(f"[*] Total unique configs available: {total_available}")
    
    if total_available == 0:
        color_print("[!] No configs available!", Fore.RED)
        return 0
    
    # اگر تعداد کانفیگ‌ها کمتر از 2000 است، همه را بگیر
    sample_size = min(2000, total_available)
    
    # انتخاب کاملاً رندوم (بدون ترتیب)
    random_configs = random.sample(unique_configs, sample_size)
    
    # ذخیره در فایل
    with open(OUTPUT_RANDOM_2000, 'w', encoding='utf-8') as f:
        for cfg in random_configs:
            f.write(cfg + '\n')
    
    color_print(f"[✓] Saved {len(random_configs)} random configs to {OUTPUT_RANDOM_2000}", Fore.GREEN)
    color_print(f"[*] These configs are randomly selected and will be different on each update", Fore.CYAN)
    
    return len(random_configs)


# ============================================================
# بخش اصلی
# ============================================================

def git_commit_and_push():
    """Commit and push the output files"""
    files_to_commit = [OUTPUT_FULL_CONFIGS, OUTPUT_RANDOM_2000]
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False)
        
        for f in files_to_commit:
            if os.path.exists(f):
                subprocess.run(["git", "add", f], check=False)
        
        subprocess.run(["git", "commit", "-m", f"Auto-update configs - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"], check=False)
        subprocess.run(["git", "push"], check=False)
        color_print("[✓] Files committed and pushed", Fore.GREEN)
    except Exception as e:
        logging.error(f"Git error: {e}")


def main():
    global stop_processing
    stop_processing = False
    
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("V2RAY MANAGER - Subscription Link Finder", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    start_time = time.time()
    
    try:
        subscription_links = find_subscription_links()
        if not subscription_links:
            color_print("\n[!] No subscription links found", Fore.RED)
            sys.exit(1)
        
        unique_configs = fetch_all_configs(subscription_links)
        if not unique_configs:
            color_print("\n[!] No configs extracted", Fore.RED)
            sys.exit(1)
        
        random_count = create_random_2000_configs(unique_configs)
        
        elapsed = time.time() - start_time
        color_print("\n" + "="*60, Fore.CYAN)
        color_print("SUMMARY", Fore.YELLOW, Style.BRIGHT)
        color_print("="*60, Fore.CYAN)
        print(f"  Subscription links found: {len(subscription_links)}")
        print(f"  Unique configs: {len(unique_configs)}")
        print(f"  Random 2000 configs created: {random_count}")
        print(f"  Total time: {elapsed:.1f}s")
        color_print("="*60, Fore.CYAN)
        
        git_commit_and_push()
        
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        logging.error(traceback.format_exc())
        color_print(f"\n[ERROR] {e}", Fore.RED)
        sys.exit(1)


if __name__ == "__main__":
    main()
