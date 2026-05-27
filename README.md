
<div dir="rtl" style="text-align: justify;">

این کدها با کمک **Vibe Coding** و با نظارت انسان با هوش مصنوعی ساخته شده‌اند.

## نحوه جمع‌آوری خودکار لینک‌های اشتراک
لینک‌های سابسکریپشن (`pool_address.txt`) هر ۵ روز یکبار با جستجو در ریپازیتوری‌های گیت‌هاب مرتبط با ایران یافت می‌شوند ( فقط لینک‌هایی که در ۵ روز گذشته بروز شده باشند انتخاب می‌گردند). ( بنابراین همیشه لینکهای جدید جایگزین لینکهای قدیمی میشوند)

کانفیگ‌های درون همه لینکها استخراج شده و همه کانفیگ‌های تکراری حذف می‌شوند سپس تمام کانفیگ‌های باقیمانده تست می‌شوند و در نهایت **۲۰۰۰ تا از سریعترین کانفیگها** در فایل `success_config.txt` ذخیره می‌گردد تا شما بتوانید به‌راحتی از آنها استفاده کنید.

لینک زیر شامل **۲۰۰۰ کانفیگ تست‌شده و فعال** است که از **26 لینک سابسکریپشن** و در مجموع از بین **110587 کانفیگ مختلف** تکراری و خراب تست و استخراج شده‌اند.

برای استفاده از کانفیگ‌های سالم، فقط کافی است لینک زیر را در نرم‌افزار های کلاینت V2Ray خود وارد کنید:

<div style="text-align: center; margin: 20px 0;">
    <img src="https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=https://github.com/Triton-ix/sub-link-checker/blob/main/success_config.txt" alt="QR Code" style="display: inline-block;">
</div>

<div style="text-align: center; direction: ltr; background-color: #f5f5f5; padding: 10px; border-radius: 5px; display: inline-block; width: 100%;">
    <code style="font-size: 14px; word-break: break-all;">https://github.com/Triton-ix/sub-link-checker/blob/main/success_config.txt</code>
    <button onclick="navigator.clipboard.writeText('https://github.com/Triton-ix/sub-link-checker/blob/main/success_config.txt')" style="margin-right: 10px; padding: 5px 10px; background-color: #007bff; color: white; border: none; border-radius: 3px; cursor: pointer;">📋 کپی</button>
</div>

## گزارش خودکار وضعیت کانفیگ‌ها

**📅 آخرین بروزرسانی :** 1405/03/07 01:19:10

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
<td>26</td>
<td>110587</td>
<td>109613</td>
</tr>
</tbody>
</table>
</div>

## فایل‌های خروجی
- `pool_address.txt` : لینک های سابسکرایب پیدا شده
- `cleaned_configs.txt` : کانفیگ‌های یکتا
- `success_config.txt` : تعداد ۲۰۰۰ کانفیگ منتخب با بالاترین پینگ (تست شده)

</div>
