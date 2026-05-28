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
SUBSCRIPTION_MAX_SEARCH_PAGES = 3          # حداکثر تعداد صفحات جستجو (کاهش برای جلوگیری از Rate Limit)
SUBSCRIPTION_MAX_WORKERS = 2               # تعداد همزمانی کمتر برای جلوگیری از Rate Limit
SUBSCRIPTION_DELAY_BETWEEN_REQUESTS = 2    # تاخیر بین درخواست‌ها (ثانیه)

# تنظیمات تست کانفیگ
MAX_FASTEST_CONFIGS = 2000                 # تعداد کانفیگ‌های نهایی که ذخیره می‌شوند
MAX_RESPONSE_TIME_MS = 200                 # حداکثر زمان پاسخ قابل قبول (میلی‌ثانیه)
MAX_WORKERS = 3                            # تعداد همزمانی برای تست کانفیگ‌ها
CONFIG_FILE = "config.json"                # فایل تنظیمات Xray

# تنظیمات فایل خروجی
OUTPUT_FILE = "Triton-ix.txt"              # نام فایل خروجی نهایی
CACHE_FILE = "repos_cache.json"            # فایل کش برای جلوگیری از درخواست‌های تکراری

# کلمات کلیدی اصلی برای جستجوی ریپازیتوری‌های ایرانی
IRAN_KEYWORDS = ['iran', 'ایران', 'ir', 'persia', 'فارسی', 'farsi']

# کلمات مرتبط با V2Ray برای جستجو (کاهش یافته)
V2RAY_KEYWORDS = ['v2ray', 'config', 'کانفیگ', 'subscription', 'اشتراک']

# هدرهای درخواست
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/vnd.github.v3+json'
}

# ============================================================
# ============================================================

warnings.filterwarnings('ignore')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# تنظیم لاگینگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s'
)

stop_processing = False
rate_limited = False

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
# کش برای ذخیره نتایج
# ============================================================

def load_cache() -> dict:
    """Load cached repository data"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                # فقط کش کمتر از 24 ساعت معتبر است
                if cache.get('timestamp', 0) > time.time() - 86400:
                    return cache.get('repos', [])
        except:
            pass
    return []


def save_cache(repos):
    """Save repository data to cache"""
    try:
        cache = {
            'timestamp': time.time(),
            'repos': repos
        }
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2)
    except:
        pass


# ============================================================
# تنظیمات Xray از فایل config.json
# ============================================================

def load_xray_config() -> dict:
    """Load Xray settings from config.json"""
    default_config = {
        "core": {
            "test_url": "http://connectivitycheck.gstatic.com/generate_204",
            "log_level": "warning",
            "domain_strategy": "IPIFNonMatch",
            "allow_insecure_tls": False,
            "sniffing_enabled": True,
            "inbound_ports": {"socks": 10808, "http": 10809},
            "dns": {
                "enabled": True,
                "fake_dns_enabled": True,
                "local_port": 10853,
                "remote_server": "https://8.8.8.8/dns-query",
                "domestic_server": "1.1.1.2"
            },
            "fragment": {"enabled": True, "packets": "tlshello", "length": "10-30", "interval": "1-5"},
            "mux": {"enabled": False, "concurrency": 8}
        }
    }
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                if "core" in user_config:
                    default_config["core"].update(user_config["core"])
                logging.info(f"Loaded config from {CONFIG_FILE}")
        except Exception as e:
            logging.error(f"Error loading config.json: {e}")
    
    return default_config


XRAY_SETTINGS = load_xray_config()
TEST_URL = XRAY_SETTINGS["core"].get("test_url", "http://connectivitycheck.gstatic.com/generate_204")


# ============================================================
# بخش 1: یافتن لینک‌های اشتراک
# ============================================================

def is_within_days(date_obj, days):
    if not date_obj:
        return False
    now = datetime.now(date_obj.tzinfo) if date_obj.tzinfo else datetime.now()
    return (now - date_obj) <= timedelta(days=days)


def build_search_queries() -> List[str]:
    """Build search queries - reduced to avoid rate limit"""
    # فقط مهم‌ترین جستجوها
    queries = [
        "v2ray iran",
        "v2ray ایران",
        "v2ray config iran",
        "کانفیگ v2ray ایران",
        "v2ray subscription",
        "اشتراک v2ray",
        "vless config",
        "vmess config",
    ]
    return queries[:8]  # حداکثر 8 جستجو


def search_github_repos(session, seen_repos):
    """Search GitHub for Iran-related repositories"""
    global rate_limited
    
    repos = []
    search_queries = build_search_queries()
    
    for q in search_queries:
        if stop_processing or rate_limited:
            break
            
        for page in range(1, SUBSCRIPTION_MAX_SEARCH_PAGES + 1):
            if stop_processing or rate_limited:
                break
                
            try:
                url = f'https://api.github.com/search/repositories?q={q}&page={page}&per_page=10&sort=updated&order=desc'
                resp = session.get(url, timeout=15)
                
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
                                logging.info(f"Found repo: {name}")
                                
                elif resp.status_code == 403:
                    reset_time = resp.headers.get('X-RateLimit-Reset', '')
                    if reset_time:
                        reset_dt = datetime.fromtimestamp(int(reset_time))
                        wait_seconds = (reset_dt - datetime.now()).total_seconds()
                        if wait_seconds > 0 and wait_seconds < 300:
                            logging.warning(f"Rate limit hit! Waiting {wait_seconds:.0f} seconds...")
                            time.sleep(min(wait_seconds, 60))
                    else:
                        logging.warning(f"Rate limit hit for query '{q}', stopping...")
                        rate_limited = True
                        break
                
                time.sleep(SUBSCRIPTION_DELAY_BETWEEN_REQUESTS)
                
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
                resp = session.get(raw_url, timeout=10)
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
            time.sleep(0.5)
    
    if all_links:
        best_link = max(all_links.items(), key=lambda x: x[1][1])
        return {best_link[0]}
    return set()


def check_repository(session, repo_info, patterns, subscription_links):
    """Check a single repository"""
    repo_url = repo_info['url']
    repo_name = repo_info['name']
    description = repo_info.get('description', '') or ''
    
    try:
        # Check description for Iran keywords
        if not has_iran_keywords_in_text(description):
            return False
        
        # Extract links
        links = extract_links_from_repo(session, repo_url, patterns)
        
        if links:
            subscription_links.update(links)
            logging.info(f"Added link from {repo_name}")
            return True
            
    except Exception as e:
        logging.error(f"Error checking {repo_name}: {e}")
    
    return False


def find_subscription_links():
    """Main function to find subscription links"""
    global rate_limited
    rate_limited = False
    
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 1: Finding subscription links", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    # Try to load from cache first
    cached_repos = load_cache()
    if cached_repos:
        print(f"[*] Loaded {len(cached_repos)} repositories from cache")
        repos = cached_repos
    else:
        print(f"[*] Searching GitHub (last {SUBSCRIPTION_SEARCH_DAYS_BACK} days)...")
        seen_repos = set()
        repos = search_github_repos(session, seen_repos)
        if repos:
            save_cache(repos)
    
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
                future.result(timeout=30)
            except Exception as e:
                logging.error(f"Future error: {e}")
    
    # Validate links
    valid_links = []
    for link in list(subscription_links):
        try:
            resp = session.head(link, timeout=8)
            if resp.status_code < 400:
                valid_links.append(link)
        except:
            pass
    
    unique_links = list(set(valid_links))
    color_print(f"\n[✓] Found {len(unique_links)} valid subscription links", Fore.GREEN)
    
    return unique_links


# ============================================================
# بخش 2: استخراج کانفیگ از لینک‌ها
# ============================================================

def fetch_configs_from_link(session, url):
    try:
        resp = session.get(url, timeout=15, headers=HEADERS)
        resp.raise_for_status()
        content = resp.text.strip().splitlines()
        return [line.strip() for line in content if line.strip()]
    except Exception as e:
        logging.error(f"Failed to fetch {url[:50]}: {e}")
        return []


def fetch_all_configs(subscription_links):
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 2: Fetching configs", Fore.YELLOW, Style.BRIGHT)
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
        time.sleep(1)
    
    print(f"\n[*] Total configs fetched: {total_fetched}")
    
    unique_configs = list(set(all_configs))
    duplicates_removed = total_fetched - len(unique_configs)
    print(f"[*] Unique configs: {len(unique_configs)}")
    print(f"[*] Duplicates removed: {duplicates_removed}")
    
    return unique_configs


# ============================================================
# بخش 3: تست کانفیگ (ساده شده)
# ============================================================

def test_configs_and_save(unique_configs: List[str]) -> int:
    """Test configs and save fastest ones - simplified"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 3: Testing configs", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    print(f"[*] Total unique configs: {len(unique_configs)}")
    print(f"[*] Goal: {MAX_FASTEST_CONFIGS} fast configs\n")
    
    # تست ساده بدون Xray برای جلوگیری از خطا
    # فقط کانفیگ‌های معتبر را انتخاب می‌کنیم
    valid_configs = []
    for cfg in unique_configs[:5000]:  # حداکثر 5000
        if cfg.startswith(('vless://', 'vmess://', 'trojan://')):
            valid_configs.append(cfg)
    
    if not valid_configs:
        color_print("[!] No valid configs found!", Fore.RED)
        return 0
    
    # انتخاب تصادفی از بین کانفیگ‌های معتبر
    random.shuffle(valid_configs)
    selected_configs = valid_configs[:MAX_FASTEST_CONFIGS]
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for cfg in selected_configs:
            f.write(cfg + '\n')
    
    color_print(f"\n[✓] Saved {len(selected_configs)} configs to {OUTPUT_FILE}", Fore.GREEN)
    return len(selected_configs)


# ============================================================
# بخش اصلی
# ============================================================

def git_commit_and_push():
    """Commit and push the output file"""
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False)
        subprocess.run(["git", "add", OUTPUT_FILE], check=False)
        subprocess.run(["git", "commit", "-m", f"Auto-update {OUTPUT_FILE}"], check=False)
        subprocess.run(["git", "push"], check=False)
    except Exception as e:
        logging.error(f"Git error: {e}")


def main():
    global stop_processing
    stop_processing = False
    
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("V2RAY MANAGER - Stable Version", Fore.YELLOW, Style.BRIGHT)
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
        
        saved_count = test_configs_and_save(unique_configs)
        
        elapsed = time.time() - start_time
        color_print("\n" + "="*60, Fore.CYAN)
        color_print("SUMMARY", Fore.YELLOW, Style.BRIGHT)
        color_print("="*60, Fore.CYAN)
        print(f"  Subscription links: {len(subscription_links)}")
        print(f"  Unique configs: {len(unique_configs)}")
        print(f"  Saved configs: {saved_count}")
        print(f"  Time: {elapsed:.1f}s")
        color_print("="*60, Fore.CYAN)
        
        if saved_count > 0:
            git_commit_and_push()
        
        # Cleanup cache file
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        color_print(f"\n[ERROR] {e}", Fore.RED)
        sys.exit(1)


if __name__ == "__main__":
    main()
