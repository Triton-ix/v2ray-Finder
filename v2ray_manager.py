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
import warnings
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
import urllib3

# ============================================================
#                     تنظیمات قابل تغییر
# ============================================================

# تنظیمات جستجوی لینک‌های اشتراک
SUBSCRIPTION_SEARCH_DAYS_BACK = 2          # جستجوی لینک‌هایی که در X روز گذشته بروز شده‌اند
SUBSCRIPTION_MAX_SEARCH_PAGES = 10         # حداکثر تعداد صفحات جستجو (هر صفحه 30 نتیجه)
SUBSCRIPTION_MAX_WORKERS = 3               # تعداد همزمانی برای بررسی ریپازیتوری‌ها

# تنظیمات تست کانفیگ
RANDOM_SAMPLE_SIZE = 20000                 # تعداد کانفیگ‌هایی که به صورت رندوم تست می‌شوند
MAX_FASTEST_CONFIGS = 2000                 # تعداد کانفیگ‌های نهایی که ذخیره می‌شوند
TEST_TIMEOUT_SECONDS = 2                   # حداکثر زمان تست هر کانفیگ (ثانیه)
TEST_MAX_WORKERS = 10                      # تعداد همزمانی برای تست کانفیگ‌ها
TEST_BATCH_SIZE = 1000                     # اندازه دسته برای نمایش پیشرفت

# تنظیمات فایل خروجی
OUTPUT_FILE = "Triton-ix.txt"              # نام فایل خروجی نهایی

# کلمات کلیدی برای جستجوی ریپازیتوری‌های ایرانی
IRAN_KEYWORDS = ['iran', 'ایران', 'ir', 'persia', 'فارسی', 'farsi']

# الگوهای تشخیص لینک اشتراک
SUBSCRIPTION_PATTERNS = [
    r'(https?://raw\.githubusercontent\.com/[^\s"\'<>]+\.(txt|json|yml|yaml|link))',
    r'(https?://github\.com/[^\s"\'<>]+/raw/[^\s"\'<>]+)',
    r'https?://[^\s"\']+\.(txt|json|link)',
]

# هدرهای درخواست
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# ============================================================
# ============================================================

warnings.filterwarnings('ignore')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

stop_processing = False

def signal_handler(sig, frame):
    global stop_processing
    stop_processing = True
    print("\n[!] Stopping...")

signal.signal(signal.SIGINT, signal_handler)


def install_packages():
    """Install required packages if not present"""
    for pkg in ['colorama', 'requests', 'urllib3']:
        try:
            __import__(pkg)
        except ImportError:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg])

install_packages()
from colorama import init, Fore, Style
init(autoreset=True)


def color_print(text, color=Fore.WHITE, style=Style.NORMAL):
    print(f"{style}{color}{text}{Style.RESET_ALL}")


# ============================================================
# بخش 1: یافتن لینک‌های اشتراک
# ============================================================

def is_within_days(date_obj, days):
    """Check if date is within specified days"""
    if not date_obj:
        return False
    now = datetime.now(date_obj.tzinfo) if date_obj.tzinfo else datetime.now()
    return (now - date_obj) <= timedelta(days=days)


def search_github_repos(session, seen_repos):
    """Search GitHub for Iran-related repositories"""
    repos = []
    search_queries = [
        'v2ray subscription iran',
        'v2ray config iran',
        'کانفیگ v2ray ایران',
        'v2ray free config',
        'iran v2ray',
    ]
    
    for q in search_queries:
        for page in range(1, SUBSCRIPTION_MAX_SEARCH_PAGES + 1):
            if stop_processing:
                break
            try:
                url = f'https://api.github.com/search/repositories?q={q}&page={page}&per_page=30&sort=updated&order=desc'
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
                                })
                elif resp.status_code == 403:
                    print(f"Rate limit hit for query '{q}', stopping...")
                    break
                
                time.sleep(0.5)
            except Exception as e:
                print(f"Search error for '{q}': {e}")
                continue
    
    return repos


def extract_links_from_repo(session, repo_url, patterns):
    """Extract subscription links from repository files"""
    links = set()
    repo_path = repo_url.replace('https://github.com/', '')
    
    paths_to_check = [
        'README.md', 'sub.txt', 'subscription.txt', 'config.txt',
        'v2ray.txt', 'links.txt', 'urls.txt'
    ]
    
    for branch in ['main', 'master']:
        for path in paths_to_check:
            raw_url = f'https://raw.githubusercontent.com/{repo_path}/{branch}/{path}'
            try:
                resp = session.get(raw_url, timeout=10)
                if resp.status_code == 200:
                    content = resp.text
                    for pattern in patterns:
                        matches = re.findall(pattern, content, re.IGNORECASE)
                        for m in matches:
                            if isinstance(m, tuple):
                                m = m[0] if m else ''
                            if m and ('raw.githubusercontent.com' in m or m.endswith(('.txt', '.json'))):
                                if 'github.com' in m and '/raw/' in m:
                                    links.add(m)
            except:
                continue
    return links


def check_repository(session, repo_info, patterns, keywords, subscription_links):
    """Check a single repository and extract links"""
    repo_url = repo_info['url']
    try:
        repo_path = repo_url.replace('https://github.com/', '')
        api_url = f'https://api.github.com/repos/{repo_path}'
        resp = session.get(api_url, timeout=10)
        
        if resp.status_code != 200:
            return False
        
        repo_data = resp.json()
        description = repo_data.get('description', '') or ''
        topics = ' '.join(repo_data.get('topics', []))
        full_text = (description + ' ' + topics).lower()
        
        has_iran = any(kw.lower() in full_text for kw in keywords)
        if not has_iran:
            return False
        
        links = extract_links_from_repo(session, repo_url, patterns)
        
        if links:
            subscription_links.update(links)
            print(f"  Found {len(links)} links in {repo_path}")
            return True
    except Exception as e:
        print(f"  Error checking {repo_url}: {e}")
    return False


def find_subscription_links():
    """Main function to find subscription links"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 1: Finding subscription links from GitHub", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    print(f"[*] Searching for Iran-related repos (last {SUBSCRIPTION_SEARCH_DAYS_BACK} days)...")
    
    seen_repos = set()
    repos = search_github_repos(session, seen_repos)
    print(f"[*] Found {len(repos)} candidate repositories")
    
    if not repos:
        print("[!] No repositories found")
        return []
    
    print("[*] Checking repositories for subscription links...")
    subscription_links = set()
    
    with ThreadPoolExecutor(max_workers=SUBSCRIPTION_MAX_WORKERS) as executor:
        futures = {executor.submit(check_repository, session, repo, SUBSCRIPTION_PATTERNS, IRAN_KEYWORDS, subscription_links): repo for repo in repos}
        for future in as_completed(futures):
            if stop_processing:
                break
            try:
                future.result()
            except:
                pass
    
    # Validate links
    print(f"[*] Validating {len(subscription_links)} extracted links...")
    valid_links = []
    
    for link in list(subscription_links):
        if stop_processing:
            break
        try:
            resp = session.head(link, timeout=8)
            if resp.status_code < 400:
                valid_links.append(link)
        except:
            pass
    
    unique_links = list(set(valid_links))
    print(f"[✓] Found {len(unique_links)} valid subscription links")
    
    return unique_links


# ============================================================
# بخش 2: استخراج کانفیگ از لینک‌ها
# ============================================================

def fetch_configs_from_link(session, url):
    """Fetch configs from a single subscription link"""
    try:
        resp = session.get(url, timeout=15, headers=HEADERS)
        resp.raise_for_status()
        content = resp.text.strip().splitlines()
        return [line.strip() for line in content if line.strip()]
    except Exception as e:
        print(f"  Failed: {url[:50]}... - {str(e)[:50]}")
        return []


def fetch_all_configs(subscription_links):
    """Fetch configs from all subscription links and deduplicate"""
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
        time.sleep(random.uniform(0.3, 0.8))
    
    print(f"\n[*] Total configs fetched: {total_fetched}")
    
    # Deduplicate
    unique_configs = list(set(all_configs))
    duplicates_removed = total_fetched - len(unique_configs)
    print(f"[*] Unique configs: {len(unique_configs)}")
    print(f"[*] Duplicates removed: {duplicates_removed}")
    
    return unique_configs, total_fetched, len(unique_configs), duplicates_removed


# ============================================================
# بخش 3: تست کانفیگ و انتخاب سریعترین‌ها
# ============================================================

def test_single_config(config_line, timeout=TEST_TIMEOUT_SECONDS):
    """Test a single config and return (config, response_time_ms)"""
    if stop_processing or not config_line.strip():
        return config_line, None
    
    try:
        host, port = None, None
        
        if config_line.startswith('vless://'):
            parsed = urlparse(config_line)
            if '@' in parsed.netloc:
                hp = parsed.netloc.split('@')[1]
                if ':' in hp:
                    host, port = hp.split(':')
        elif config_line.startswith('vmess://'):
            encoded = config_line.replace('vmess://', '')
            try:
                decoded = base64.b64decode(encoded).decode('utf-8')
                cfg = json.loads(decoded)
                host = cfg.get('add')
                port = str(cfg.get('port'))
            except:
                pass
        elif config_line.startswith('trojan://') or config_line.startswith('ss://'):
            parsed = urlparse(config_line)
            host = parsed.hostname
            port = parsed.port
        
        if host and port:
            test_url = f"http://{host}:{port}/"
            with requests.Session() as sess:
                sess.headers.update(HEADERS)
                sess.verify = False
                start_time = time.time()
                r = sess.get(test_url, timeout=timeout)
                elapsed_ms = (time.time() - start_time) * 1000
                if r.status_code < 500:
                    time.sleep(random.uniform(0.05, 0.2))
                    return config_line, elapsed_ms
        return config_line, None
    except Exception:
        return config_line, None


def test_configs_and_save(unique_configs):
    """Test random sample of configs and save fastest ones"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 3: Testing configs and selecting fastest", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    total_available = len(unique_configs)
    print(f"[*] Total unique configs available: {total_available}")
    
    # Random sampling
    if total_available <= RANDOM_SAMPLE_SIZE:
        sample_configs = unique_configs
        print(f"[*] Testing ALL {total_available} configs")
    else:
        sample_configs = random.sample(unique_configs, RANDOM_SAMPLE_SIZE)
        print(f"[*] Randomly selected {RANDOM_SAMPLE_SIZE} configs out of {total_available}")
    
    print(f"[*] Goal: Find fastest {MAX_FASTEST_CONFIGS} configs\n")
    
    tested_configs = []  # (response_time_ms, config_line)
    processed = 0
    working_count = 0
    batch_num = 1
    total = len(sample_configs)
    
    for start in range(0, total, TEST_BATCH_SIZE):
        if stop_processing:
            break
        end = min(start + TEST_BATCH_SIZE, total)
        batch_configs = sample_configs[start:end]
        batch_working = 0
        
        print(f"[Batch {batch_num}] Testing {start+1}-{end} ({len(batch_configs)} items)...")
        
        with ThreadPoolExecutor(max_workers=TEST_MAX_WORKERS) as executor:
            futures = {executor.submit(test_single_config, cfg): cfg for cfg in batch_configs}
            for future in as_completed(futures):
                if stop_processing:
                    executor.shutdown(wait=False)
                    break
                try:
                    cfg, response_time = future.result(timeout=TEST_TIMEOUT_SECONDS + 0.5)
                except:
                    cfg = futures[future]
                    response_time = None
                
                processed += 1
                if response_time is not None:
                    working_count += 1
                    batch_working += 1
                    tested_configs.append((response_time, cfg))
                
                if working_count > 0:
                    print(f"\r[Progress: {processed}/{total}] Working configs found: {working_count}", end='', flush=True)
                else:
                    print(f"\r[Progress: {processed}/{total}] Working configs found: 0", end='', flush=True)
        
        print(f"\n[Batch {batch_num}] Working in batch: {batch_working} | Total so far: {working_count}")
        batch_num += 1
        
        if end < total and not stop_processing:
            time.sleep(random.uniform(0.5, 1.0))
    
    print()
    
    if not tested_configs:
        color_print("[!] No working configs found!", Fore.RED)
        return 0, 0, 0
    
    # Sort by response time (fastest first)
    tested_configs.sort(key=lambda x: x[0])
    
    # Take top N
    top_configs = tested_configs[:MAX_FASTEST_CONFIGS]
    
    # Save to file
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for response_time, config_line in top_configs:
            f.write(config_line + '\n')
    
    fastest = top_configs[0][0] if top_configs else 0
    slowest_in_top = top_configs[-1][0] if top_configs else 0
    
    color_print(f"\n[✓] Tested {len(tested_configs)} working configs out of {RANDOM_SAMPLE_SIZE} random samples", Fore.GREEN)
    color_print(f"[✓] Saved {len(top_configs)} fastest configs to {OUTPUT_FILE}", Fore.GREEN)
    color_print(f"[*] Fastest: {fastest:.1f}ms | Slowest in top {len(top_configs)}: {slowest_in_top:.1f}ms", Fore.CYAN)
    
    return len(top_configs), len(tested_configs), len(sample_configs)


# ============================================================
# بخش اصلی
# ============================================================

def git_commit_and_push():
    """Commit and push the output file to GitHub"""
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False, capture_output=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False, capture_output=True)
        subprocess.run(["git", "add", OUTPUT_FILE], check=True, capture_output=True)
        
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if result.returncode != 0:
            commit_msg = f"Auto-update {OUTPUT_FILE} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
            push_result = subprocess.run(["git", "push"], capture_output=True)
            if push_result.returncode != 0:
                color_print(f"[!] Push failed: {push_result.stderr.decode()}", Fore.YELLOW)
            else:
                color_print("[✓] Committed and pushed to GitHub", Fore.GREEN)
        else:
            color_print("[*] No changes to commit", Fore.CYAN)
    except subprocess.CalledProcessError as e:
        color_print(f"[!] Git error: {e}", Fore.RED)


def main():
    global stop_processing
    stop_processing = False
    
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("V2RAY MANAGER - Complete Automation", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    start_time = time.time()
    
    try:
        # Step 1: Find subscription links
        subscription_links = find_subscription_links()
        
        if not subscription_links:
            color_print("\n[!] No subscription links found. Exiting.", Fore.RED)
            sys.exit(1)
        
        # Step 2: Fetch and deduplicate configs
        unique_configs, total_fetched, unique_count, duplicates_removed = fetch_all_configs(subscription_links)
        
        if not unique_configs:
            color_print("\n[!] No configs extracted. Exiting.", Fore.RED)
            sys.exit(1)
        
        # Step 3: Test configs and save fastest
        saved_count, working_count, tested_count = test_configs_and_save(unique_configs)
        
        # Summary
        elapsed = time.time() - start_time
        color_print("\n" + "="*60, Fore.CYAN)
        color_print("SUMMARY", Fore.YELLOW, Style.BRIGHT)
        color_print("="*60, Fore.CYAN)
        print(f"  Subscription links found: {len(subscription_links)}")
        print(f"  Total configs fetched: {total_fetched}")
        print(f"  Duplicates removed: {duplicates_removed}")
        print(f"  Unique configs: {unique_count}")
        print(f"  Configs tested: {tested_count}")
        print(f"  Working configs found: {working_count}")
        print(f"  Fastest configs saved: {saved_count}")
        print(f"  Output file: {OUTPUT_FILE}")
        print(f"  Total time: {elapsed:.1f} seconds")
        color_print("="*60, Fore.CYAN)
        
        # Commit and push to GitHub
        if saved_count > 0:
            git_commit_and_push()
        
    except KeyboardInterrupt:
        color_print("\n[!] Interrupted by user", Fore.YELLOW)
    except Exception as e:
        color_print(f"\n[ERROR] {e}", Fore.RED)
        sys.exit(1)


if __name__ == "__main__":
    main()
