import os
import json
from datetime import datetime, timezone, timedelta
import subprocess
import sys

def install_jdatetime():
    try:
        import jdatetime
        return jdatetime
    except ImportError:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'jdatetime'])
        import jdatetime
        return jdatetime

def to_jalali(gregorian_dt):
    jdatetime = install_jdatetime()
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    local = gregorian_dt.astimezone(tehran_tz)
    return jdatetime.datetime.fromgregorian(datetime=local).strftime("%Y/%m/%d %H:%M:%S")

def read_lines_count(fname):
    if not os.path.exists(fname):
        return 0
    with open(fname, 'r', encoding='utf-8') as f:
        return sum(1 for _ in f if _.strip())

def get_link_stats():
    if not os.path.exists("link_stats.json"):
        return 0, 0, 0
    with open("link_stats.json", 'r', encoding='utf-8') as f:
        data = json.load(f)
    total = len(data)
    working = sum(1 for v in data.values() if v.get("success"))
    return total, working, total - working

def get_raw_total():
    if not os.path.exists("link_stats.json"):
        return 0
    with open("link_stats.json", 'r', encoding='utf-8') as f:
        data = json.load(f)
    total = 0
    for v in data.values():
        if v.get("success"):
            total += v.get("configs_found", 0)
    return total

def generate_readme():
    # آمار لینک‌ها
    total_links, working_links, dead_links = get_link_stats()
    # آمار کانفیگ‌ها
    raw_total = get_raw_total()
    unique_total = read_lines_count("cleaned_configs.txt")
    working_total = read_lines_count("success_config.txt")  # این همان 2000 است
    duplicate_total = raw_total - unique_total if raw_total > unique_total else 0
    dead_configs = unique_total - working_total if unique_total > working_total else 0

    # درصدها
    perc_links_working = (working_links / total_links * 100) if total_links else 0
    perc_links_dead = (dead_links / total_links * 100) if total_links else 0
    perc_raw = 100.0
    perc_duplicate = (duplicate_total / raw_total * 100) if raw_total else 0
    perc_unique = (unique_total / raw_total * 100) if raw_total else 0
    perc_dead_configs = (dead_configs / unique_total * 100) if unique_total else 0
    perc_working = (working_total / unique_total * 100) if unique_total else 0

    # زمان بروزرسانی به شمسی و تهران
    now_utc = datetime.now(timezone.utc)
    jalali_str = to_jalali(now_utc)

    # لینک با نام کاربری جدید (Triton-ix)
    repo_url = "https://github.com/Triton-ix/sub-link-checker/blob/main/success_config.txt"
    
    # تولید بارکد (با استفاده از API ساده)
    barcode_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={repo_url}"

    readme_content = f"""
<div dir="rtl" style="text-align: justify;">

این کدها با کمک **Vibe Coding** و با نظارت انسان با هوش مصنوعی ساخته شده‌اند.

## نحوه جمع‌آوری خودکار لینک‌های اشتراک
لینک‌های سابسکریپشن (`pool_address.txt`) هر ۵ روز یکبار با جستجو در ریپازیتوری‌های گیت‌هاب مرتبط با ایران یافت می‌شوند ( فقط لینک‌هایی که در ۵ روز گذشته بروز شده باشند انتخاب می‌گردند). ( بنابراین همیشه لینکهای جدید جایگزین لینکهای قدیمی میشوند)

کانفیگ‌های درون همه لینکها استخراج شده و همه کانفیگ‌های تکراری حذف می‌شوند سپس تمام کانفیگ‌های باقیمانده تست می‌شوند و در نهایت **۲۰۰۰ تا از سریعترین کانفیگها** در فایل `success_config.txt` ذخیره می‌گردد تا شما بتوانید به‌راحتی از آنها استفاده کنید.

لینک زیر شامل **۲۰۰۰ کانفیگ تست‌شده و فعال** است که از **{working_links} لینک سابسکریپشن** و در مجموع از بین **{raw_total} کانفیگ مختلف** تکراری و خراب تست و استخراج شده‌اند.

برای استفاده از کانفیگ‌های سالم، فقط کافی است لینک زیر را در نرم‌افزار های کلاینت V2Ray خود وارد کنید:

<div style="text-align: center; margin: 20px 0;">
    <img src="{barcode_url}" alt="QR Code" style="display: inline-block;">
</div>

<div style="text-align: center; direction: ltr; background-color: #f5f5f5; padding: 10px; border-radius: 5px; display: inline-block; width: 100%;">
    <code style="font-size: 14px; word-break: break-all;">{repo_url}</code>
    <button onclick="navigator.clipboard.writeText('{repo_url}')" style="margin-right: 10px; padding: 5px 10px; background-color: #007bff; color: white; border: none; border-radius: 3px; cursor: pointer;">📋 کپی</button>
</div>

## گزارش خودکار وضعیت کانفیگ‌ها

**📅 آخرین بروزرسانی :** {jalali_str}

<div dir="rtl" style="text-align: center; overflow-x: auto;">
<table style="margin-left: auto; margin-right: auto; border-collapse: collapse; width: 80%; text-align: center;">
<thead>
<tr style="background-color: #f2f2f2;">
<th>آیتم</th>
<th>✅ لینک‌های سالم</th>
<th>📥 کل کانفیگ‌ها</th>
<th>🔄 کانفیگ‌ ها بدون تکرار</th>
</tr>
</thead>
<tbody>
<tr>
<td style="font-weight: bold;">تعداد</td>
<td>{working_links}</td>
<td>{raw_total}</td>
<td>{unique_total}</td>
</tr>
</tbody>
</table>
</div>

## فایل‌های خروجی
- `pool_address.txt` : لینک های سابسکرایب پیدا شده
- `cleaned_configs.txt` : کانفیگ‌های یکتا
- `success_config.txt` : تعداد ۲۰۰۰ کانفیگ منتخب با بالاترین پینگ (تست شده)

</div>
"""
    with open("README.md", "w", encoding='utf-8') as f:
        f.write(readme_content)
    print("README.md updated successfully with new design!")

if __name__ == "__main__":
    generate_readme()
