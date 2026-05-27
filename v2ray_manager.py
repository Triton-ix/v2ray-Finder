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
SUBSCRIPTION_SEARCH_DAYS_BACK = 2          # جستجوی لینک‌هایی که در X روز گذشته بروز شده‌اند
SUBSCRIPTION_MAX_SEARCH_PAGES = 10         # حداکثر تعداد صفحات جستجو (هر صفحه 30 نتیجه)
SUBSCRIPTION_MAX_WORKERS = 3               # تعداد همزمانی برای بررسی ریپازیتوری‌ها

# تنظیمات تست کانفیگ
MAX_FASTEST_CONFIGS = 2000                 # تعداد کانفیگ‌های نهایی که ذخیره می‌شوند
MAX_RESPONSE_TIME_MS = 150                 # حداکثر زمان پاسخ قابل قبول (میلی‌ثانیه)
MAX_WORKERS = 5                            # تعداد همزمانی برای تست کانفیگ‌ها
CONFIG_FILE = "config.json"                # فایل تنظیمات Xray

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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')
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
                return default_config
        except Exception as e:
            print(f"[!] Error loading config.json: {e}, using defaults")
    
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


def search_github_repos(session, seen_repos):
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
    try:
        resp = session.get(url, timeout=15, headers=HEADERS)
        resp.raise_for_status()
        content = resp.text.strip().splitlines()
        return [line.strip() for line in content if line.strip()]
    except Exception as e:
        print(f"  Failed: {url[:50]}...")
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
        time.sleep(random.uniform(0.3, 0.8))
    
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
    """Download Xray core binary - optimized for GitHub Actions"""
    try:
        color_print("[*] Downloading Xray core for Linux x86_64...", Fore.CYAN)
        
        # Simple download using requests
        download_url = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
        resp = requests.get(download_url, timeout=120)
        resp.raise_for_status()
        
        zip_path = vendor_path / "xray.zip"
        with open(zip_path, 'wb') as f:
            f.write(resp.content)
        
        # Extract
        with zipfile.ZipFile(zip_path, 'r') as zipf:
            zipf.extractall(vendor_path)
        
        zip_path.unlink()
        
        # Make executable
        xray_path = vendor_path / "xray"
        xray_path.chmod(0o755)
        
        # Test it
        result = subprocess.run([str(xray_path), "-version"], capture_output=True)
        if result.returncode == 0:
            color_print("[✓] Xray core downloaded and working", Fore.GREEN)
            return True
        else:
            color_print("[!] Xray binary test failed", Fore.RED)
            return False
            
    except Exception as e:
        color_print(f"[!] Failed to download Xray: {e}", Fore.RED)
        return False


def parse_v2ray_uri(uri: str) -> Optional[dict]:
    """Parse different config URI types (vless, vmess, trojan)"""
    try:
        if uri.startswith('vless://'):
            parsed = urlparse(uri)
            return {
                'protocol': 'vless',
                'address': parsed.hostname,
                'port': parsed.port,
                'id': parsed.username or '',
                'encryption': 'none',
                'flow': '',
                'original_uri': uri
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
                'original_uri': uri
            }
        elif uri.startswith('trojan://'):
            parsed = urlparse(uri)
            return {
                'protocol': 'trojan',
                'address': parsed.hostname,
                'port': parsed.port,
                'password': parsed.username or '',
                'original_uri': uri
            }
    except Exception:
        pass
    return None


def build_xray_config(parsed: dict, inbound_port: int) -> Optional[dict]:
    """Build Xray configuration with settings from config.json"""
    core_settings = XRAY_SETTINGS["core"]
    
    config = {
        "log": {"loglevel": core_settings.get("log_level", "warning")},
        "inbounds": [{
            "port": inbound_port,
            "protocol": "socks",
            "tag": "socks-inbound",
            "settings": {"auth": "noauth", "udp": True},
            "sniffing": {
                "enabled": core_settings.get("sniffing_enabled", True),
                "destOverride": ["http", "tls"]
            } if core_settings.get("sniffing_enabled", True) else None
        }],
        "outbounds": [],
        "routing": {
            "domainStrategy": core_settings.get("domain_strategy", "IPIFNonMatch"),
            "rules": [{
                "type": "field",
                "inboundTag": ["socks-inbound"],
                "outboundTag": "proxy"
            }]
        }
    }
    
    # Remove None values
    if config["inbounds"][0]["sniffing"] is None:
        del config["inbounds"][0]["sniffing"]
    
    # Build outbound
    if parsed['protocol'] == 'vless':
        outbound = {
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": parsed['address'],
                    "port": parsed['port'],
                    "users": [{
                        "id": parsed['id'],
                        "encryption": parsed.get('encryption', 'none'),
                        "flow": parsed.get('flow', '')
                    }]
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
                    "users": [{
                        "id": parsed['id'],
                        "alterId": parsed.get('aid', 0),
                        "security": parsed.get('security', 'auto')
                    }]
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
    
    # Add fragment if enabled
    if core_settings.get("fragment", {}).get("enabled", False):
        frag = core_settings["fragment"]
        outbound["streamSettings"] = {
            "sockopt": {
                "tcpFragment": {
                    "packets": frag.get("packets", "tlshello"),
                    "length": frag.get("length", "10-30"),
                    "interval": frag.get("interval", "1-5")
                }
            }
        }
    
    config["outbounds"].append(outbound)
    return config


def test_config_with_xray(config_line: str, xray_path: Path, local_port: int) -> Tuple[Optional[str], Optional[float]]:
    """Test a single config using Xray core"""
    parsed = parse_v2ray_uri(config_line)
    if not parsed or not parsed.get('address') or not parsed.get('port'):
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
        
        # Wait for port
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
    """Test configs using Xray core and save fastest ones"""
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("STEP 3: Testing configs with Xray Core", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)
    
    total_available = len(unique_configs)
    print(f"[*] Total unique configs: {total_available}")
    print(f"[*] Goal: {MAX_FASTEST_CONFIGS} configs under {MAX_RESPONSE_TIME_MS}ms")
    print(f"[*] Will stop when target reached\n")
    
    # Setup Xray
    project_root = Path(__file__).parent.resolve()
    vendor_path = project_root / "vendor"
    vendor_path.mkdir(exist_ok=True)
    
    xray_path = vendor_path / "xray"
    
    if not xray_path.exists():
        if not download_xray_core(vendor_path):
            color_print("[!] Xray setup failed", Fore.RED)
            return 0
    
    # Shuffle and test
    random.shuffle(unique_configs)
    
    fastest_configs = []
    tested_count = 0
    base_port = 20800
    
    for config_line in unique_configs:
        if stop_processing or len(fastest_configs) >= MAX_FASTEST_CONFIGS:
            break
        
        tested_count += 1
        local_port = base_port + (tested_count % 1000)
        
        result, response_time = test_config_with_xray(config_line, xray_path, local_port)
        
        if result and response_time:
            fastest_configs.append((response_time, result))
            fastest_configs.sort(key=lambda x: x[0])
            print(f"\r[Tested: {tested_count}] ✓ Found! {response_time:.1f}ms | Total: {len(fastest_configs)}/{MAX_FASTEST_CONFIGS}", flush=True)
        else:
            print(f"\r[Tested: {tested_count}] Working: {len(fastest_configs)}/{MAX_FASTEST_CONFIGS}", end='', flush=True)
        
        time.sleep(random.uniform(0.3, 0.7))
    
    print()
    
    if not fastest_configs:
        color_print("[!] No working configs found!", Fore.RED)
        return 0
    
    # Save results
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
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False, capture_output=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False, capture_output=True)
        subprocess.run(["git", "add", OUTPUT_FILE], check=True, capture_output=True)
        
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if result.returncode != 0:
            commit_msg = f"Auto-update {OUTPUT_FILE} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
            subprocess.run(["git", "push"], check=True, capture_output=True)
            color_print("[✓] Committed and pushed", Fore.GREEN)
        else:
            color_print("[*] No changes", Fore.CYAN)
    except Exception as e:
        color_print(f"[!] Git error: {e}", Fore.RED)


def main():
    global stop_processing
    stop_processing = False
    
    color_print("\n" + "="*60, Fore.CYAN)
    color_print("V2RAY MANAGER - GitHub Actions Optimized", Fore.YELLOW, Style.BRIGHT)
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
        
        # Step 3: Test configs and save fastest
        saved_count = test_configs_and_save(unique_configs)
        
        # Summary
        elapsed = time.time() - start_time
        color_print("\n" + "="*60, Fore.CYAN)
        color_print("SUMMARY", Fore.YELLOW, Style.BRIGHT)
        color_print("="*60, Fore.CYAN)
        print(f"  Subscription links: {len(subscription_links)}")
        print(f"  Unique configs: {len(unique_configs)}")
        print(f"  Fast configs saved: {saved_count}")
        print(f"  Time: {elapsed:.1f}s")
        color_print("="*60, Fore.CYAN)
        
        if saved_count > 0:
            git_commit_and_push()
        
    except Exception as e:
        color_print(f"\n[ERROR] {e}", Fore.RED)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
