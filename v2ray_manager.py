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
SUBSCRIPTION_MAX_SEARCH_PAGES = 5          # حداکثر تعداد صفحات جستجو (قابل تنظیم: 1 تا 10)
SUBSCRIPTION_REQUEST_DELAY = 5             # تاخیر بین درخواست‌های جستجو به گیت‌هاب (ثانیه)
SUBSCRIPTION_MAX_WORKERS = 1               # تعداد همزمانی برای بررسی ریپازیتوری‌ها

# تنظیمات فایل‌های خروجی
OUTPUT_FILE_UNIQUE = "Full_uniqe-config.txt"    # فایل حاوی همه کانفیگ‌های یونیک
RANDOM_CONFIG_COUNT = 2000                      # تعداد کانفیگ‌های رندوم خروجی (قابل تنظیم)

# کلمات کلیدی اصلی برای جستجوی ریپازیتوری‌های ایرانی (باید در توضیحات باشند)
IRAN_KEYWORDS = ['iran', 'ایران', 'ir', 'persia', 'فارسی', 'farsi']

# الگوهای تشخیص لینک اشتراک (کاهش یافته)
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

# تنظیم لاگینگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("debug.log", mode='w')
    ]
)

stop_processing = False

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
# بخش 1: یافتن لینک‌های اشتراک
# ============================================================

def is_within_days(date_obj, days):
    if not date_obj:
        return False
    now = datetime.now(date_obj.tzinfo) if date_obj.tzinfo else datetime.now()
    return (now - date_obj) <= timedelta(days=days)


def build_search_queries() -> List[str]:
    """ساخت جستجوهای بسیار محدود برای جلوگیری از Rate Limit"""
    queries = [
        "v2ray iran",
        "v2ray ایران",
        "v2ray config iran",
        "کانفیگ v2ray ایران",
    ]
    return queries


def search_github_repos(session, seen_repos):
    """Search GitHub with delays to avoid rate limiting"""
    repos = []
    search_queries = build_search_queries()
    
    logging.info(f"Starting search with {len(search_queries)} queries (delayed to avoid rate limit)")
    
    for q in search_queries:
        if stop_processing:
            break
        
        # تاخیر قبل از هر جستجو برای جلوگیری از Rate Limit (قابل تنظیم)
        logging.info(f"Waiting {SUBSCRIPTION_REQUEST_DELAY} seconds before query: '{q}'")
        time.sleep(SUBSCRIPTION_REQUEST_DELAY)
        
        for page in range(1, SUBSCRIPTION_MAX_SEARCH_PAGES + 1):
            if stop_processing:
                break
            try:
                url = f'https://api.github.com/search/repositories?q={q}&page={page}&per_page=20&sort=updated&order=desc'
                logging.info(f"Requesting: {url[:80]}...")
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
                elif resp.status_code == 422:
                    logging.warning(f"Validation error (422) for query: {q}. Skipping.")
                    break
                
                # تاخیر بین صفحات
                if page < SUBSCRIPTION_MAX_SEARCH_PAGES:
                    time.sleep(2)
                    
            except requests.exceptions.RequestException as e:
                logging.error(f"Network error for '{q}': {e}")
                continue
            except Exception as e:
                logging.error(f"Search error for '{q}': {e}")
                continue
    
    logging.info(f"Found {len(repos)} candidate repositories")
    return repos


def has_iran_keywords_in_text(text: str) -> bool:
    """Check if text contains any of the Iran keywords"""
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
                    config_count = len([line for line in content.splitlines() if line.strip() and ('://' in line or 'vless' in line or 'vmess' in line)])
                    
                    for pattern in patterns:
                        matches = re.findall(pattern, content, re.IGNORECASE)
                        for m in matches:
                            if isinstance(m, tuple):
                                m = m[0] if m else ''
                            if m and ('raw.githubusercontent.com' in m or m.endswith(('.txt', '.json'))):
                                if 'github.com' in m and '/raw/' in m:
                                    if m not in all_links or config_count > all_links[m][1]:
                                        all_links[m] = (raw_url, config_count)
            except:
                continue
    
    if all_links:
        best_link = max(all_links.items(), key=lambda x: x[1][1])
        return {best_link[0]}
    return set()


def check_repository(session, repo_info, patterns, subscription_links):
    """Check a single repository and extract the best subscription link"""
    repo_url = repo_info['url']
    repo_name = repo_info['name']
    description = repo_info.get('description', '') or ''
    
    try:
        # بررسی توضیحات ریپازیتوری
        if not has_iran_keywords_in_text(description):
            # اگر توضیحات نداشت، README را چک کن
            found = False
            for branch in ['main', 'master']:
                try:
                    readme_url = f'https://raw.githubusercontent.com/{repo_name}/{branch}/README.md'
                    readme_resp = session.get(readme_url, timeout=10)
                    if readme_resp.status_code == 200:
                        readme_content = readme_resp.text[:1000]
                        if has_iran_keywords_in_text(readme_content):
                            found = True
                            break
                except:
                    continue
            if not found:
                return False
        
        # استخراج لینک
        links = extract_links_from_repo(session, repo_url, patterns)
        
        if links:
            subscription_links.update(links)
            logging.info(f"Found link from {repo_name}")
            return True
            
    except Exception as e:
        logging.error(f"Error checking {repo_name}: {e}")
    
    return False


def find_subscription_links():
    """Main function to find subscription links"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 1: Finding subscription links from GitHub", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    print(f"[*] Searching for Iran-related repos (last {SUBSCRIPTION_SEARCH_DAYS_BACK} days)...")
    print(f"[*] Using {SUBSCRIPTION_MAX_SEARCH_PAGES} pages per query with {len(build_search_queries())} queries")
    print(f"[*] Waiting {SUBSCRIPTION_REQUEST_DELAY} seconds between each request to avoid rate limiting...\n")
    
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
    
    # اعتبارسنجی لینک‌ها
    valid_links = []
    for link in list(subscription_links):
        if stop_processing:
            break
        try:
            resp = session.head(link, timeout=10)
            if resp.status_code < 400:
                valid_links.append(link)
            else:
                logging.warning(f"Link validation failed (HTTP {resp.status_code}): {link[:80]}")
        except requests.exceptions.RequestException as e:
            logging.warning(f"Link validation error for {link[:80]}: {e}")
        except Exception as e:
            logging.warning(f"Unexpected error for {link[:80]}: {e}")
    
    unique_links = list(set(valid_links))
    color_print(f"\n[✓] Found {len(unique_links)} valid subscription links", Fore.GREEN)
    
    return unique_links


# ============================================================
# بخش 2: استخراج کانفیگ از لینک‌ها
# ============================================================

def fetch_configs_from_link(session, url, retries=2):
    """Fetch configs from a link with retry mechanism"""
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20, headers=HEADERS)
            resp.raise_for_status()
            content = resp.text.strip().splitlines()
            return [line.strip() for line in content if line.strip()]
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logging.warning(f"Link not found (404) for {url[:50]}. Skipping.")
                return []
            if attempt < retries - 1:
                logging.warning(f"HTTP error {e.response.status_code} for {url[:50]}, retry {attempt+1}")
                time.sleep(2)
            else:
                logging.error(f"Failed to fetch {url[:50]}: {e}")
        except Exception as e:
            if attempt < retries - 1:
                logging.warning(f"Retry {attempt+1} for {url[:50]}")
                time.sleep(2)
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
        print(f"[{i}/{len(subscription_links)}] Fetching...")
        configs = fetch_configs_from_link(session, link)
        if configs:
            print(f"    Found {len(configs)} configs")
            total_fetched += len(configs)
            all_configs.extend(configs)
        time.sleep(1)  # تاخیر بین درخواست‌ها
    
    print(f"\n[*] Total configs fetched: {total_fetched}")
    
    unique_configs = list(set(all_configs))
    duplicates_removed = total_fetched - len(unique_configs)
    print(f"[*] Unique configs: {len(unique_configs)}")
    print(f"[*] Duplicates removed: {duplicates_removed}")
    
    return unique_configs


# ============================================================
# بخش 3: ذخیره فایل‌های خروجی
# ============================================================

def save_output_files(unique_configs: List[str]):
    """Save unique configs to file and create a random sample"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 3: Saving output files", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    # 1. Save all unique configs
    with open(OUTPUT_FILE_UNIQUE, 'w', encoding='utf-8') as f:
        for config in unique_configs:
            f.write(config + '\n')
    
    unique_count = len(unique_configs)
    color_print(f"[✓] Saved {unique_count} unique configs to {OUTPUT_FILE_UNIQUE}", Fore.GREEN)
    
    # 2. Create random sample
    if unique_count == 0:
        color_print("[!] No configs to create random sample.", Fore.RED)
        return
    
    sample_size = min(RANDOM_CONFIG_COUNT, unique_count)
    random_sample = random.sample(unique_configs, sample_size)
    
    output_file_random = f"{RANDOM_CONFIG_COUNT}-random-config.txt"
    with open(output_file_random, 'w', encoding='utf-8') as f:
        for config in random_sample:
            f.write(config + '\n')
    
    color_print(f"[✓] Saved {sample_size} random configs to {output_file_random}", Fore.GREEN)


# ============================================================
# بخش اصلی
# ============================================================

def git_commit_and_push():
    """Commit and push the output files"""
    try:
        output_file_random = f"{RANDOM_CONFIG_COUNT}-random-config.txt"
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False)
        subprocess.run(["git", "add", OUTPUT_FILE_UNIQUE, output_file_random], check=False)
        
        # Check if there are changes to commit
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if result.returncode != 0:
            commit_msg = f"Auto-update configs - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            subprocess.run(["git", "commit", "-m", commit_msg], check=False)
            subprocess.run(["git", "push"], check=False)
            color_print("[✓] Committed and pushed to GitHub", Fore.GREEN)
        else:
            color_print("[*] No changes to commit", Fore.CYAN)
    except Exception as e:
        logging.error(f"Git error: {e}")


def main():
    global stop_processing
    stop_processing = False
    
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("V2RAY MANAGER - GitHub Actions", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    start_time = time.time()
    
    try:
        # Step 1: Find subscription links
        subscription_links = find_subscription_links()
        if not subscription_links:
            color_print("\n[!] No subscription links found", Fore.RED)
            sys.exit(1)
        
        # Step 2: Fetch and deduplicate configs
        unique_configs = fetch_all_configs(subscription_links)
        if not unique_configs:
            color_print("\n[!] No configs extracted", Fore.RED)
            sys.exit(1)
        
        # Step 3: Save output files
        save_output_files(unique_configs)
        
        # Summary
        elapsed = time.time() - start_time
        color_print("\n" + "="*60, Fore.CYAN)
        color_print("SUMMARY", Fore.YELLOW, Style.BRIGHT)
        color_print("="*60, Fore.CYAN)
        print(f"  Subscription links found: {len(subscription_links)}")
        print(f"  Total unique configs: {len(unique_configs)}")
        print(f"  Random sample size: {min(RANDOM_CONFIG_COUNT, len(unique_configs))}")
        print(f"  Time: {elapsed:.1f}s")
        color_print("="*60, Fore.CYAN)
        
        # Commit and push to GitHub
        git_commit_and_push()
        
        # Remove debug log on success
        if os.path.exists("debug.log"):
            os.remove("debug.log")
        
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        logging.error(traceback.format_exc())
        color_print(f"\n[ERROR] {e}", Fore.RED)
        color_print(f"Check debug.log for details", Fore.YELLOW)
        sys.exit(1)


if __name__ == "__main__":
    main()
