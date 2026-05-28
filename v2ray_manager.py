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
SUBSCRIPTION_MAX_SEARCH_PAGES = 5          # حداکثر تعداد صفحات جستجو (کاهش برای سرعت بیشتر)
SUBSCRIPTION_MAX_WORKERS = 3               # تعداد همزمانی برای بررسی ریپازیتوری‌ها

# تنظیمات تست کانفیگ
MAX_FASTEST_CONFIGS = 2000                 # تعداد کانفیگ‌های نهایی که ذخیره می‌شوند
MAX_RESPONSE_TIME_MS = 200                 # حداکثر زمان پاسخ قابل قبول (میلی‌ثانیه)
MAX_WORKERS = 3                            # تعداد همزمانی برای تست کانفیگ‌ها
CONFIG_FILE = "config.json"                # فایل تنظیمات Xray

# تنظیمات فایل خروجی
OUTPUT_FILE = "Triton-ix.txt"              # نام فایل خروجی نهایی
DEBUG_LOG = "debug.log"                    # فایل لاگ برای دیباگ

# کلمات کلیدی اصلی برای جستجوی ریپازیتوری‌های ایرانی (باید در توضیحات باشند)
IRAN_KEYWORDS = ['iran', 'ایران', 'ir', 'persia', 'فارسی', 'farsi']

# کلمات مرتبط با V2Ray برای جستجو (ترکیب با کلمات کلیدی اصلی)
V2RAY_KEYWORDS = [
    'v2ray', 'subscription', 'config', 'کانفیگ', 'اشتراک',
    'vless', 'vmess', 'trojan', 'proxy', 'پروکسی', 'فیلترشکن'
]

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

# تنظیم لاگینگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(DEBUG_LOG, mode='w')
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
                return default_config
        except Exception as e:
            logging.error(f"Error loading config.json: {e}, using defaults")
    
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
    """ساخت جستجوهای ترکیبی با کلمات کلیدی اصلی + کلمات مرتبط با V2Ray"""
    queries = []
    
    # ترکیب کلمات کلیدی اصلی با کلمات مرتبط V2Ray
    for iran_word in IRAN_KEYWORDS:
        for v2ray_word in V2RAY_KEYWORDS:
            queries.append(f"{iran_word} {v2ray_word}")
    
    # جستجوهای خاص و پرکاربرد
    specific_queries = [
        "v2ray subscription iran",
        "v2ray config iran",
        "کانفیگ v2ray ایران",
        "اشتراک v2ray ایران",
        "v2ray iran free",
        "v2ray iran config",
        "v2ray ایران",
        "v2ray free subscription iran",
    ]
    
    queries.extend(specific_queries)
    
    # حذف تکراری‌ها و محدود کردن تعداد
    unique_queries = list(set(queries))[:20]  # حداکثر 20 جستجو
    
    logging.info(f"Built {len(unique_queries)} search queries")
    return unique_queries


def search_github_repos(session, seen_repos):
    """Search GitHub for Iran-related repositories using combined keywords"""
    repos = []
    search_queries = build_search_queries()
    
    for q in search_queries:
        if stop_processing:
            break
            
        for page in range(1, SUBSCRIPTION_MAX_SEARCH_PAGES + 1):
            if stop_processing:
                break
            try:
                url = f'https://api.github.com/search/repositories?q={q}&page={page}&per_page=20&sort=updated&order=desc'
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
                elif resp.status_code == 403:
                    logging.warning(f"Rate limit hit for query '{q}', skipping...")
                    break
                
                time.sleep(0.5)
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
                future.result(timeout=30)
            except Exception as e:
                logging.error(f"Future error: {e}")
    
    # اعتبارسنجی لینک‌ها
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
        time.sleep(0.5)
    
    print(f"\n[*] Total configs fetched: {total_fetched}")
    
    unique_configs = list(set(all_configs))
    duplicates_removed = total_fetched - len(unique_configs)
    print(f"[*] Unique configs: {len(unique_configs)}")
    print(f"[*] Duplicates removed: {duplicates_removed}")
    
    return unique_configs


# ============================================================
# بخش 3: تست کانفیگ با Xray Core
# ============================================================

def download_xray_core(vendor_path: Path) -> bool:
    """Download Xray core binary"""
    try:
        color_print("[*] Downloading Xray core...", Fore.CYAN)
        
        download_url = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
        resp = requests.get(download_url, timeout=120)
        resp.raise_for_status()
        
        zip_path = vendor_path / "xray.zip"
        with open(zip_path, 'wb') as f:
            f.write(resp.content)
        
        with zipfile.ZipFile(zip_path, 'r') as zipf:
            zipf.extractall(vendor_path)
        
        zip_path.unlink()
        
        xray_path = vendor_path / "xray"
        xray_path.chmod(0o755)
        
        result = subprocess.run([str(xray_path), "-version"], capture_output=True)
        if result.returncode == 0:
            color_print("[✓] Xray core ready", Fore.GREEN)
            return True
            
    except Exception as e:
        logging.error(f"Xray download failed: {e}")
    
    return False


def parse_v2ray_uri(uri: str) -> Optional[dict]:
    """Parse config URI"""
    try:
        if uri.startswith('vless://'):
            parsed = urlparse(uri)
            return {
                'protocol': 'vless',
                'address': parsed.hostname,
                'port': parsed.port,
                'id': parsed.username or '',
                'encryption': 'none',
            }
        elif uri.startswith('vmess://'):
            encoded = uri.replace('vmess://', '')
            encoded += '=' * (-len(encoded) % 4)
            decoded = base64.b64decode(encoded).decode('utf-8')
            data = json.loads(decoded)
            return {
                'protocol': 'vmess',
                'address': data.get('add', ''),
                'port': int(data.get('port', 0)),
                'id': data.get('id', ''),
                'aid': data.get('aid', 0),
                'security': data.get('scy', 'auto'),
            }
        elif uri.startswith('trojan://'):
            parsed = urlparse(uri)
            return {
                'protocol': 'trojan',
                'address': parsed.hostname,
                'port': parsed.port,
                'password': parsed.username or '',
            }
    except Exception:
        pass
    return None


def build_xray_config(parsed: dict, inbound_port: int) -> Optional[dict]:
    """Build Xray config"""
    core_settings = XRAY_SETTINGS["core"]
    
    config = {
        "log": {"loglevel": core_settings.get("log_level", "warning")},
        "inbounds": [{
            "port": inbound_port,
            "protocol": "socks",
            "tag": "socks-inbound",
            "settings": {"auth": "noauth", "udp": True},
        }],
        "outbounds": [],
        "routing": {
            "rules": [{
                "type": "field",
                "inboundTag": ["socks-inbound"],
                "outboundTag": "proxy"
            }]
        }
    }
    
    if parsed['protocol'] == 'vless':
        outbound = {
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": parsed['address'],
                    "port": parsed['port'],
                    "users": [{"id": parsed['id'], "encryption": "none"}]
                }]
            },
            "tag": "proxy"
        }
    elif parsed['protocol'] == 'vmess':
        outbound = {
            "protocol": "vmess",
            "settings": {
                "vnext": [{
                    "address": parsed['address'],
                    "port": parsed['port'],
                    "users": [{"id": parsed['id'], "alterId": parsed.get('aid', 0)}]
                }]
            },
            "tag": "proxy"
        }
    elif parsed['protocol'] == 'trojan':
        outbound = {
            "protocol": "trojan",
            "settings": {
                "servers": [{
                    "address": parsed['address'],
                    "port": parsed['port'],
                    "password": parsed['password']
                }]
            },
            "tag": "proxy"
        }
    else:
        return None
    
    config["outbounds"].append(outbound)
    return config


def test_config_with_xray(config_line: str, xray_path: Path, local_port: int) -> Tuple[Optional[str], Optional[float]]:
    """Test config with Xray"""
    parsed = parse_v2ray_uri(config_line)
    if not parsed or not parsed.get('address'):
        return None, None
    
    config = build_xray_config(parsed, local_port)
    if not config:
        return None, None
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config, f)
        config_path = f.name
    
    process = None
    try:
        process = subprocess.Popen(
            [str(xray_path), "-config", config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        time.sleep(2)
        
        proxies = {
            "http": f"socks5h://127.0.0.1:{local_port}",
            "https": f"socks5h://127.0.0.1:{local_port}"
        }
        
        start_time = time.time()
        response = requests.get(TEST_URL, proxies=proxies, timeout=10)
        elapsed_ms = (time.time() - start_time) * 1000
        
        if response.status_code < 500 and elapsed_ms <= MAX_RESPONSE_TIME_MS:
            return config_line, elapsed_ms
        
    except Exception:
        pass
    finally:
        if process:
            process.terminate()
            time.sleep(0.5)
            process.kill()
        try:
            os.unlink(config_path)
        except:
            pass
    
    return None, None


def test_configs_and_save(unique_configs: List[str]) -> int:
    """Test and save fastest configs"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 3: Testing configs", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    print(f"[*] Total unique configs: {len(unique_configs)}")
    print(f"[*] Goal: {MAX_FASTEST_CONFIGS} configs under {MAX_RESPONSE_TIME_MS}ms\n")
    
    project_root = Path(__file__).parent.resolve()
    vendor_path = project_root / "vendor"
    vendor_path.mkdir(exist_ok=True)
    
    xray_path = vendor_path / "xray"
    
    if not xray_path.exists():
        if not download_xray_core(vendor_path):
            color_print("[!] Xray setup failed", Fore.RED)
            return 0
    
    random.shuffle(unique_configs)
    
    fastest_configs = []
    tested_count = 0
    base_port = 20800
    
    for config_line in unique_configs[:5000]:  # فقط 5000 تا برای سرعت
        if stop_processing or len(fastest_configs) >= MAX_FASTEST_CONFIGS:
            break
        
        tested_count += 1
        local_port = base_port + (tested_count % 1000)
        
        result, response_time = test_config_with_xray(config_line, xray_path, local_port)
        
        if result and response_time:
            fastest_configs.append((response_time, result))
            fastest_configs.sort(key=lambda x: x[0])
            print(f"\r✓ Found! {response_time:.0f}ms | Total: {len(fastest_configs)}/{MAX_FASTEST_CONFIGS}", flush=True)
        else:
            print(f"\rTested: {tested_count} | Working: {len(fastest_configs)}/{MAX_FASTEST_CONFIGS}", end='', flush=True)
        
        time.sleep(0.5)
    
    print()
    
    if not fastest_configs:
        color_print("[!] No working configs found!", Fore.RED)
        return 0
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for _, config_line in fastest_configs:
            f.write(config_line + '\n')
    
    color_print(f"\n[✓] Saved {len(fastest_configs)} configs to {OUTPUT_FILE}", Fore.GREEN)
    return len(fastest_configs)


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
    color_print("V2RAY MANAGER - GitHub Actions", Fore.YELLOW, Style.BRIGHT)
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
        
        # حذف فایل لاگ در صورت موفقیت
        if os.path.exists(DEBUG_LOG):
            os.remove(DEBUG_LOG)
        
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        logging.error(traceback.format_exc())
        color_print(f"\n[ERROR] {e}", Fore.RED)
        color_print(f"Check {DEBUG_LOG} for details", Fore.YELLOW)
        sys.exit(1)


if __name__ == "__main__":
    main()
