import requests
import re
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== تنظیمات قابل تغییر توسط شما ==========
DAYS_BACK = 2                    # جستجوی لینک‌هایی که در X روز گذشته بروز شده‌اند
MAX_SEARCH_PAGES = 10            # حداکثر تعداد صفحات جستجو (هر صفحه 30 نتیجه)
MAX_WORKERS = 3                  # تعداد همزمانی برای بررسی ریپازیتوری‌ها (کمتر = کندتر اما پایدارتر)
# =================================================

class V2RaySubscriptionFinder:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/vnd.github.v3+json',
        })
        self.subscription_links = set()
        self.seen_repos = set()
        
        # فقط کلمات کلیدی مورد نظر
        self.iran_keywords = ['iran', 'ایران', 'ir', 'persia', 'فارسی', 'farsi']
        
        self.sub_patterns = [
            r'(https?://raw\.githubusercontent\.com/[^\s"\'<>]+\.(txt|json|yml|yaml|link))',
            r'(https?://github\.com/[^\s"\'<>]+/raw/[^\s"\'<>]+)',
            r'https?://[^\s"\']+\.(txt|json|link)',
        ]

    def is_within_days(self, date_obj):
        """بررسی اینکه تاریخ در X روز گذشته باشد"""
        if not date_obj:
            return False
        now = datetime.now(date_obj.tzinfo) if date_obj.tzinfo else datetime.now()
        return (now - date_obj) <= timedelta(days=DAYS_BACK)

    def search_github_repos(self):
        """جستجوی ریپازیتوری‌های مرتبط با ایران"""
        repos = []
        search_queries = [
            'v2ray subscription iran',
            'v2ray config iran',
            'کانفیگ v2ray ایران',
            'v2ray free config',
            'iran v2ray',
        ]
        
        for q in search_queries:
            for page in range(1, MAX_SEARCH_PAGES + 1):
                try:
                    url = f'https://api.github.com/search/repositories?q={q}&page={page}&per_page=30&sort=updated&order=desc'
                    resp = self.session.get(url, timeout=15)
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data.get('items', [])
                        if not items:
                            break
                            
                        for repo in items:
                            name = repo['full_name']
                            if name not in self.seen_repos:
                                self.seen_repos.add(name)
                                updated_at = repo.get('updated_at', '')
                                last_update = None
                                if updated_at:
                                    try:
                                        last_update = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                                    except:
                                        pass
                                
                                # فقط ریپازیتوری‌هایی که در DAYS_BACK روز گذشته بروز شده‌اند
                                if last_update and self.is_within_days(last_update):
                                    repos.append({
                                        'name': name,
                                        'url': repo['html_url'],
                                        'updated_at': updated_at,
                                    })
                    
                    elif resp.status_code == 403:
                        print(f"Rate limit hit for query '{q}', stopping...")
                        break
                    
                    # تاخیر بین درخواست‌ها برای جلوگیری از محدودیت
                    time.sleep(0.5)
                    
                except Exception as e:
                    print(f"Search error for '{q}': {e}")
                    continue
        
        return repos

    def _extract_links_from_repo(self, repo_url):
        """استخراج لینک‌های اشتراک از فایل‌های README و txt"""
        links = set()
        repo_path = repo_url.replace('https://github.com/', '')
        
        # فایل‌هایی که ممکن است حاوی لینک باشند
        paths_to_check = [
            'README.md', 'sub.txt', 'subscription.txt', 'config.txt', 
            'v2ray.txt', 'links.txt', 'urls.txt'
        ]
        
        for branch in ['main', 'master']:
            for path in paths_to_check:
                raw_url = f'https://raw.githubusercontent.com/{repo_path}/{branch}/{path}'
                try:
                    resp = self.session.get(raw_url, timeout=10)
                    if resp.status_code == 200:
                        content = resp.text
                        for pattern in self.sub_patterns:
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

    def check_repository(self, repo_info):
        """بررسی یک ریپازیتوری و استخراج لینک‌ها"""
        repo_url = repo_info['url']
        try:
            # فقط ریپازیتوری‌های ایرانی را بررسی کن
            repo_path = repo_url.replace('https://github.com/', '')
            api_url = f'https://api.github.com/repos/{repo_path}'
            resp = self.session.get(api_url, timeout=10)
            
            if resp.status_code != 200:
                return False
            
            repo_data = resp.json()
            description = repo_data.get('description', '') or ''
            topic = ' '.join(repo_data.get('topics', []))
            full_text = (description + ' ' + topic).lower()
            
            # بررسی وجود کلمات کلیدی ایرانی
            has_iran = any(kw.lower() in full_text for kw in self.iran_keywords)
            if not has_iran:
                return False
            
            # استخراج لینک‌ها
            links = self._extract_links_from_repo(repo_url)
            
            if links:
                self.subscription_links.update(links)
                print(f"✓ Found {len(links)} links in {repo_path}")
                return True
                
        except Exception as e:
            print(f"Error checking {repo_url}: {e}")
        return False

    def find_valid_subscriptions(self):
        """تابع اصلی برای یافتن لینک‌های معتبر"""
        print(f"🔍 Searching GitHub for Iran-related V2Ray repos (last {DAYS_BACK} days)...")
        repos = self.search_github_repos()
        print(f"📊 Found {len(repos)} candidate repositories from last {DAYS_BACK} days. Checking...")
        
        # بررسی ریپازیتوری‌ها با همزمانی محدود
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(self.check_repository, repo): repo for repo in repos}
            for f in as_completed(futures):
                try:
                    f.result()
                except:
                    pass
        
        # اعتبارسنجی نهایی لینک‌ها
        valid_links = []
        print(f"\n🔗 Validating {len(self.subscription_links)} extracted links...")
        
        for link in list(self.subscription_links):
            try:
                head = self.session.head(link, timeout=8)
                if head.status_code < 400:
                    valid_links.append(link)
            except:
                pass
        
        # ذخیره در فایل
        unique_links = list(set(valid_links))
        with open('pool_address.txt', 'w', encoding='utf-8') as f:
            for link in unique_links:
                f.write(link + '\n')
        
        print(f"\n✅ Done! Saved {len(unique_links)} unique valid subscription links to pool_address.txt")
        return unique_links

def main():
    finder = V2RaySubscriptionFinder()
    links = finder.find_valid_subscriptions()
    if links:
        print(f"\n📋 Sample links (first 5):")
        for link in links[:5]:
            print(f"  {link}")

if __name__ == "__main__":
    main()
