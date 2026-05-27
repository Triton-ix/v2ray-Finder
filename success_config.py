import subprocess
import sys
import os
import json
import time
import random
import signal
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from colorama import init, Fore, Style

# ========== تنظیمات قابل تغییر توسط شما ==========
MAX_FASTEST_CONFIGS = 2000    # حداکثر تعداد کانفیگ‌هایی که ذخیره می‌شوند
MAX_RESPONSE_TIME_MS = 150    # حداکثر زمان پاسخ به میلی‌ثانیه (فقط کانفیگ‌های سریع‌تر از این ذخیره می‌شوند)
# =================================================

warnings.filterwarnings('ignore')
init(autoreset=True)

def install_packages():
    for pkg in ['colorama', 'requests', 'urllib3']:
        try:
            __import__(pkg)
        except ImportError:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg])

install_packages()

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
stop_testing = False

# لیست برای ذخیره کانفیگ‌های سریع (مرتب شده)
fastest_configs = []  # هر عنصر: (response_time_ms, config_line)

def signal_handler(sig, frame):
    global stop_testing
    stop_testing = True
    print("\n" + Fore.YELLOW + "[!] Stopping...")

signal.signal(signal.SIGINT, signal_handler)

def color_print(text, color=Fore.WHITE, style=Style.NORMAL):
    print(f"{style}{color}{text}{Style.RESET_ALL}")

def test_single_config(config_line, timeout=2):
    """تست یک کانفیگ و برگرداندن (کانفیگ, سالم, زمان پاسخ به میلی‌ثانیه)"""
    if stop_testing:
        return config_line, False, None
    try:
        if not config_line.strip():
            return config_line, False, None
        host, port = None, None
        if config_line.startswith('vless://'):
            from urllib.parse import urlparse
            parsed = urlparse(config_line)
            if '@' in parsed.netloc:
                hp = parsed.netloc.split('@')[1]
                if ':' in hp:
                    host, port = hp.split(':')
        elif config_line.startswith('vmess://'):
            import base64
            enc = config_line.replace('vmess://', '')
            try:
                dec = base64.b64decode(enc).decode('utf-8')
                cfg = json.loads(dec)
                host = cfg.get('add')
                port = str(cfg.get('port'))
            except:
                pass
        elif config_line.startswith('trojan://') or config_line.startswith('ss://'):
            from urllib.parse import urlparse
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
                    return config_line, True, elapsed_ms
        return config_line, False, None
    except Exception:
        return config_line, False, None

def add_to_fastest_list(config_line, response_time_ms):
    """اضافه کردن کانفیگ به لیست سریع‌ترین‌ها (مرتب شده) - فقط اگر زیر آستانه باشد"""
    global fastest_configs
    
    # اگر زمان پاسخ بیشتر از حد مجاز باشد، نادیده بگیر
    if response_time_ms > MAX_RESPONSE_TIME_MS:
        return False
    
    # اضافه کردن به لیست
    fastest_configs.append((response_time_ms, config_line))
    
    # مرتب‌سازی بر اساس زمان پاسخ (سریع‌ترین اول)
    fastest_configs.sort(key=lambda x: x[0])
    
    # اگر بیش از حد مجاز شد، اضافه‌ها را حذف کن
    if len(fastest_configs) > MAX_FASTEST_CONFIGS:
        fastest_configs = fastest_configs[:MAX_FASTEST_CONFIGS]
    
    return True

def save_fastest_configs(output_file='success_config.txt'):
    """ذخیره کانفیگ‌های سریع در فایل"""
    if not fastest_configs:
        color_print("[!] No fast configs found!", Fore.YELLOW)
        return 0
    
    with open(output_file, 'w', encoding='utf-8') as f:
        for response_time, config_line in fastest_configs:
            f.write(config_line + '\n')
    
    # نمایش آمار
    fastest_time = fastest_configs[0][0] if fastest_configs else 0
    slowest_in_list = fastest_configs[-1][0] if fastest_configs else 0
    
    color_print(f"\n[✓] Saved {len(fastest_configs)} fastest configs (under {MAX_RESPONSE_TIME_MS}ms) to {output_file}", Fore.GREEN)
    color_print(f"[*] Fastest: {fastest_time:.1f}ms | Slowest in list: {slowest_in_list:.1f}ms", Fore.CYAN)
    
    return len(fastest_configs)

def read_configs(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        color_print(f"Error: {filename} not found!", Fore.RED)
        return []

def git_commit_push():
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False, capture_output=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False, capture_output=True)
        subprocess.run(["git", "add", "cleaned_configs.txt", "success_config.txt", "link_stats.json", "README.md"], check=True, capture_output=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if result.returncode != 0:
            commit_msg = f"Auto-update - {time.strftime('%Y-%m-%d %H:%M:%S')}"
            subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
            push_result = subprocess.run(["git", "push"], capture_output=True)
            if push_result.returncode != 0:
                color_print(f"[!] Push failed: {push_result.stderr.decode()}", Fore.YELLOW)
            else:
                color_print("[✓] Committed and pushed.", Fore.GREEN)
        else:
            color_print("[*] No changes to commit.", Fore.CYAN)
    except subprocess.CalledProcessError as e:
        color_print(f"[!] Git error: {e}", Fore.RED)

def main():
    global fastest_configs, stop_testing
    fastest_configs = []
    stop_testing = False
    
    color_print("="*60, Fore.CYAN)
    color_print(f"V2RAY TESTER (Save top {MAX_FASTEST_CONFIGS} configs under {MAX_RESPONSE_TIME_MS}ms)", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)

    input_file = 'cleaned_configs.txt'
    out_file = 'success_config.txt'
    
    # حذف فایل قبلی
    if os.path.exists(out_file):
        os.remove(out_file)
    
    configs = read_configs(input_file)
    if not configs:
        color_print("No configs to test!", Fore.RED)
        sys.exit(1)

    total = len(configs)
    color_print(f"[*] Total unique configs to test: {total}", Fore.GREEN)
    color_print(f"[*] Goal: Find {MAX_FASTEST_CONFIGS} configs with response time < {MAX_RESPONSE_TIME_MS}ms", Fore.CYAN)
    color_print(f"[*] Will stop early once target is reached!\n", Fore.CYAN)

    BATCH = 7000
    WORKERS = 10
    TIMEOUT = 2  # 2 ثانیه برای تست
    
    processed = 0
    tested_count = 0
    batch_num = 1
    found_target = False
    
    for start in range(0, total, BATCH):
        if stop_testing or found_target:
            break
        end = min(start+BATCH, total)
        batch_configs = configs[start:end]
        batch_fast = 0
        
        color_print(f"[Batch {batch_num}] Testing {start+1}-{end} ({len(batch_configs)} items)...", Fore.CYAN)
        
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = {ex.submit(test_single_config, cfg, TIMEOUT): cfg for cfg in batch_configs}
            for fut in as_completed(futures):
                if stop_testing or found_target:
                    ex.shutdown(wait=False)
                    break
                try:
                    cfg, ok, response_time = fut.result(timeout=TIMEOUT+0.5)
                except:
                    cfg = futures[fut]
                    ok = False
                    response_time = None
                
                processed += 1
                if ok and response_time:
                    tested_count += 1
                    if add_to_fastest_list(cfg, response_time):
                        batch_fast += 1
                    
                    # اگر به تعداد مورد نظر رسیدیم، متوقف شو
                    if len(fastest_configs) >= MAX_FASTEST_CONFIGS:
                        found_target = True
                        color_print(f"\n[✓] Target reached! Found {len(fastest_configs)} fast configs. Stopping early...", Fore.GREEN)
                        break
                
                # نمایش پیشرفت
                if len(fastest_configs) > 0:
                    print(f"\r[Processed: {processed}/{total}] Fast configs found: {len(fastest_configs)}/{MAX_FASTEST_CONFIGS} (target) | Tested working: {tested_count}", end='', flush=True)
                else:
                    print(f"\r[Processed: {processed}/{total}] Fast configs found: 0/{MAX_FASTEST_CONFIGS}", end='', flush=True)
        
        color_print(f"\n[Batch {batch_num}] Fast in this batch: {batch_fast} | Total fast so far: {len(fastest_configs)}/{MAX_FASTEST_CONFIGS}", Fore.MAGENTA)
        batch_num += 1
        
        # اگر به هدف رسیدیم، حلقه را بشکن
        if found_target:
            break
        
        if end < total:
            slp = random.uniform(0.5, 1.0)
            time.sleep(slp)
    
    print()
    color_print(f"\n[✓] Testing completed.", Fore.GREEN)
    
    # ذخیره کانفیگ‌های سریع در فایل
    saved_count = save_fastest_configs(out_file)
    
    # به‌روزرسانی README و commit نهایی
    color_print("[*] Updating README and committing final results...", Fore.CYAN)
    subprocess.run([sys.executable, "update_readme.py"], check=False)
    git_commit_push()
    
    color_print("="*60, Fore.CYAN)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        color_print("\n[!] Interrupted, saving current results...", Fore.YELLOW)
        save_fastest_configs('success_config.txt')
        git_commit_push()
    except Exception as e:
        color_print(f"\n[ERROR] {e}", Fore.RED)
        sys.exit(1)
