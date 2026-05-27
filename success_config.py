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

warnings.filterwarnings('ignore')
init(autoreset=True)

# ========== تنظیمات قابل تغییر ==========
MAX_CONFIGS = 2000      # حداکثر تعداد کانفیگ‌های نهایی
MAX_PING_MS = 150       # حداکثر پینگ مجاز (میلی‌ثانیه)
# ======================================

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

# لیست برای ذخیره کانفیگ‌های با پینگ خوب
working_configs_with_time = []

def signal_handler(sig, frame):
    global stop_testing
    stop_testing = True
    print("\n" + Fore.YELLOW + "[!] Stopping...")

signal.signal(signal.SIGINT, signal_handler)

def color_print(text, color=Fore.WHITE, style=Style.NORMAL):
    print(f"{style}{color}{text}{Style.RESET_ALL}")

def test_single_config(line, timeout=2):
    """تست کانفیگ و برگرداندن (کانفیگ, سالم, زمان پاسخ به میلی‌ثانیه)"""
    if stop_testing or not line.strip():
        return line, False, None
    try:
        host, port = None, None
        if line.startswith('vless://'):
            from urllib.parse import urlparse
            parsed = urlparse(line)
            if '@' in parsed.netloc:
                hp = parsed.netloc.split('@')[1]
                if ':' in hp:
                    host, port = hp.split(':')
        elif line.startswith('vmess://'):
            import base64
            enc = line.replace('vmess://', '')
            try:
                dec = base64.b64decode(enc).decode('utf-8')
                cfg = json.loads(dec)
                host = cfg.get('add')
                port = str(cfg.get('port'))
            except:
                pass
        elif line.startswith('trojan://') or line.startswith('ss://'):
            from urllib.parse import urlparse
            parsed = urlparse(line)
            host = parsed.hostname
            port = parsed.port
        if host and port:
            url = f"http://{host}:{port}/"
            with requests.Session() as sess:
                sess.headers.update(HEADERS)
                sess.verify = False
                start_time = time.time()
                r = sess.get(url, timeout=timeout)
                elapsed_ms = (time.time() - start_time) * 1000
                if r.status_code < 500 and elapsed_ms <= MAX_PING_MS:
                    time.sleep(random.uniform(0.05, 0.2))
                    return line, True, elapsed_ms
        return line, False, None
    except Exception:
        return line, False, None

def read_configs(fname):
    try:
        with open(fname, 'r', encoding='utf-8') as f:
            return [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        color_print(f"Error: {fname} not found!", Fore.RED)
        return []

def add_config_if_qualified(config, response_time):
    """اضافه کردن کانفیگ به لیست اگر شرایط را داشته باشد و لیست کامل نشده باشد"""
    global working_configs_with_time
    if len(working_configs_with_time) >= MAX_CONFIGS:
        return False
    
    working_configs_with_time.append((config, response_time))
    
    # اگر به تعداد مورد نظر رسیدیم، تست را متوقف کن
    if len(working_configs_with_time) >= MAX_CONFIGS:
        global stop_testing
        stop_testing = True
        color_print(f"\n[✓] Reached target of {MAX_CONFIGS} configs! Stopping further tests.", Fore.GREEN)
        return True
    return False

def save_configs(output_file):
    """ذخیره کانفیگ‌ها در فایل خروجی"""
    global working_configs_with_time
    if not working_configs_with_time:
        color_print("[!] No working configs found!", Fore.YELLOW)
        return 0
    
    # مرتب‌سازی بر اساس زمان پاسخ (سریع‌ترین اول)
    working_configs_with_time.sort(key=lambda x: x[1])
    
    with open(output_file, 'w', encoding='utf-8') as f:
        for config, response_time in working_configs_with_time:
            f.write(config + '\n')
    
    fastest = working_configs_with_time[0][1]
    slowest = working_configs_with_time[-1][1]
    color_print(f"[✓] Saved {len(working_configs_with_time)} configs to {output_file}", Fore.GREEN)
    color_print(f"[*] Response time range: {fastest:.1f}ms - {slowest:.1f}ms", Fore.CYAN)
    color_print(f"[*] All configs have ping ≤ {MAX_PING_MS}ms", Fore.CYAN)
    
    return len(working_configs_with_time)

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
    global working_configs_with_time, stop_testing
    working_configs_with_time = []
    stop_testing = False
    
    color_print("="*60, Fore.CYAN)
    color_print(f"V2RAY TESTER (Save top {MAX_CONFIGS} configs with ping ≤ {MAX_PING_MS}ms)", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)

    input_file = 'cleaned_configs.txt'
    out_file = 'success_config.txt'
    
    # پاک کردن فایل قبلی
    if os.path.exists(out_file):
        os.remove(out_file)
    
    configs = read_configs(input_file)
    if not configs:
        color_print("No configs to test!", Fore.RED)
        sys.exit(1)

    total = len(configs)
    color_print(f"[*] Total unique configs available: {total}", Fore.GREEN)
    color_print(f"[*] Target: Find {MAX_CONFIGS} configs with ping ≤ {MAX_PING_MS}ms", Fore.CYAN)
    color_print(f"[*] Testing will stop automatically when target is reached\n", Fore.CYAN)

    BATCH = 500
    WORKERS = 10
    TIMEOUT = 2

    processed = 0
    batch_num = 1

    for start in range(0, total, BATCH):
        if stop_testing:
            break
        end = min(start+BATCH, total)
        batch_configs = configs[start:end]
        batch_found = 0
        
        color_print(f"[Batch {batch_num}] Testing {start+1}-{end}...", Fore.CYAN)

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = {ex.submit(test_single_config, cfg, TIMEOUT): cfg for cfg in batch_configs}
            for fut in as_completed(futures):
                if stop_testing:
                    ex.shutdown(wait=False)
                    break
                try:
                    cfg, ok, response_time = fut.result(timeout=TIMEOUT+0.5)
                except:
                    cfg = futures[fut]
                    ok = False
                    response_time = None
                
                processed += 1
                
                if ok and response_time is not None:
                    batch_found += 1
                    add_config_if_qualified(cfg, response_time)
                
                # نمایش پیشرفت
                current_count = len(working_configs_with_time)
                target_status = f"[{current_count}/{MAX_CONFIGS}]"
                print(f"\r{target_status} Tested: {processed}/{total} | Found in batch: {batch_found}", end='', flush=True)
                
                if stop_testing:
                    break
        
        color_print(f"\n[Batch {batch_num}] Found {batch_found} qualified configs (Total: {len(working_configs_with_time)}/{MAX_CONFIGS})", Fore.MAGENTA)
        batch_num += 1

        # اگر به هدف رسیدیم، break کن
        if len(working_configs_with_time) >= MAX_CONFIGS:
            color_print(f"\n[✓] Target reached! Stopping early.", Fore.GREEN)
            break

        if end < total and not stop_testing:
            slp = random.uniform(0.5, 1.0)
            time.sleep(slp)

    print()
    saved_count = save_configs(out_file)
    
    # به‌روزرسانی README و commit
    color_print("[*] Updating README and committing...", Fore.CYAN)
    subprocess.run([sys.executable, "update_readme.py"], check=False)
    git_commit_push()
    
    color_print("="*60, Fore.CYAN)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        color_print("\n[!] Interrupted, saving partial results...", Fore.YELLOW)
        save_configs('success_config.txt')
        git_commit_push()
    except Exception as e:
        color_print(f"\n[ERROR] {e}", Fore.RED)
        sys.exit(1)
