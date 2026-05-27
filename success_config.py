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

# لیست برای ذخیره کانفیگ‌ها با زمان پاسخشان
working_configs_with_time = []

def signal_handler(sig, frame):
    global stop_testing
    stop_testing = True
    print("\n" + Fore.YELLOW + "[!] Stopping...")

signal.signal(signal.SIGINT, signal_handler)

def color_print(text, color=Fore.WHITE, style=Style.NORMAL):
    print(f"{style}{color}{text}{Style.RESET_ALL}")

def test_single_config(line, timeout=1):
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
                elapsed_ms = (time.time() - start_time) * 1000  # تبدیل به میلی‌ثانیه
                if r.status_code < 500:
                    time.sleep(random.uniform(0.05, 0.2))
                    return line, True, elapsed_ms
        return line, False, None
    except Exception as e:
        return line, False, None

def read_configs(fname):
    try:
        with open(fname, 'r', encoding='utf-8') as f:
            return [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        color_print(f"Error: {fname} not found!", Fore.RED)
        return []

def save_top_configs(output_file, top_n=2000):
    """ذخیره N کانفیگ برتر بر اساس زمان پاسخ (سریع‌ترین‌ها)"""
    global working_configs_with_time
    if not working_configs_with_time:
        color_print("[!] No working configs found!", Fore.YELLOW)
        return 0
    
    # مرتب‌سازی بر اساس زمان پاسخ (صعودی - سریع‌ترین اول)
    working_configs_with_time.sort(key=lambda x: x[1])
    
    # گرفتن N تای اول
    top_configs = working_configs_with_time[:top_n]
    
    with open(output_file, 'w', encoding='utf-8') as f:
        for config, response_time, _ in top_configs:
            f.write(config + '\n')
    
    color_print(f"[✓] Saved {len(top_configs)} fastest configs (out of {len(working_configs_with_time)} working) to {output_file}", Fore.GREEN)
    if len(working_configs_with_time) > top_n:
        color_print(f"[*] Fastest response time: {top_configs[0][1]:.1f}ms | Slowest in top {top_n}: {top_configs[-1][1]:.1f}ms", Fore.CYAN)
    
    return len(top_configs)

def git_commit_push():
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False, capture_output=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False, capture_output=True)
        subprocess.run(["git", "add", "cleaned_configs.txt", "success_config.txt", "link_stats.json", "README.md"], check=True, capture_output=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if result.returncode != 0:
            commit_msg = f"Auto-update after batch - {time.strftime('%Y-%m-%d %H:%M:%S')}"
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
    global working_configs_with_time
    working_configs_with_time = []  # reset
    
    color_print("="*60, Fore.CYAN)
    color_print("V2RAY TESTER (SAVE TOP 2000 FASTEST CONFIGS)", Fore.YELLOW, Style.BRIGHT)
    color_print("="*60, Fore.CYAN)

    input_file = 'cleaned_configs.txt'
    out_file = 'success_config.txt'
    
    configs = read_configs(input_file)
    if not configs:
        color_print("No configs to test!", Fore.RED)
        sys.exit(1)

    total = len(configs)
    color_print(f"[*] Total unique configs to test: {total}", Fore.GREEN)
    color_print(f"[*] Will save only the {2000} fastest working configs", Fore.CYAN)

    BATCH = 7000
    WORKERS = 10
    TIMEOUT = 1

    working_total = 0
    processed = 0
    batch_num = 1

    for start in range(0, total, BATCH):
        if stop_testing:
            break
        end = min(start+BATCH, total)
        batch_configs = configs[start:end]
        batch_working = 0
        color_print(f"\n[Batch {batch_num}] Testing {start+1}-{end} ({len(batch_configs)} items)...", Fore.CYAN)

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
                if ok:
                    working_total += 1
                    batch_working += 1
                    working_configs_with_time.append((cfg, response_time, 0))
                
                pct = (working_total / processed * 100) if processed else 0
                mark = "✓" if ok else "✗"
                col = Fore.GREEN if ok else Fore.RED
                print(f"\r[{processed}/{total} ({pct:.1f}%)] Working found: {working_total}  {col}{mark}{Style.RESET_ALL}", end='', flush=True)

        color_print(f"\n[Batch {batch_num}] Working in batch: {batch_working}/{len(batch_configs)}", Fore.MAGENTA)
        batch_num += 1

        # بعد از هر بسته، وضعیت فعلی را نشان بده (بدون ذخیره نهایی)
        color_print(f"[*] Fastest config so far: {min([t for _, t, _ in working_configs_with_time]) if working_configs_with_time else 0:.1f}ms", Fore.CYAN)

        if end < total:
            slp = random.uniform(1.0, 2.0)
            color_print(f"[*] Sleeping {slp:.1f}s...", Fore.CYAN)
            time.sleep(slp)

    print()
    color_print(f"\n[✓] Testing completed. Total working configs found: {working_total}/{total}", Fore.GREEN)
    
    # ذخیره فقط ۲۰۰۰ کانفیگ سریعتر
    saved_count = save_top_configs(out_file, top_n=2000)
    
    # به‌روزرسانی README و commit نهایی
    color_print("[*] Updating README and committing final results...", Fore.CYAN)
    subprocess.run([sys.executable, "update_readme.py"], check=False)
    git_commit_push()
    
    color_print("="*60, Fore.CYAN)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        color_print("\n[!] Interrupted, saving partial results...", Fore.YELLOW)
        save_top_configs('success_config.txt', top_n=2000)
        git_commit_push()
    except Exception as e:
        color_print(f"\n[ERROR] {e}", Fore.RED)
        sys.exit(1)
