import requests
import re
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

# ========== تنظیمات قابل تغییر توسط شما ==========
DAYS_TO_CHECK = 2              # تعداد روزهای گذشته برای جستجو (لینک‌های بروز شده در این مدت)
MAX_PAGES_PER_QUERY = 2        # تعداد صفحات جستجو (کمتر = سریعتر، اما لینک‌های کمتری پیدا می‌شود)
MAX_WORKERS = 10               # تعداد همزمانی برای بررسی ریپازیتوری‌ها
# =================================================

class V2RaySubscriptionFinder:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })
        self.subscription_links = set()
        self.seen_repos = set()
        
        self.iran_keywords = ['iran', 'ایران', 'ir', 'persia', 'فارسی', 'farsi']
        
        self.sub_patterns = [
            r'(https?://raw\.githubusercontent\.com/[^\s"\'<>]+\.(txt|json|yml|yaml|link))',
            r'(https?://github\.com/[^\s"\'<>]+/raw/[^\s"\'<>]+)',
            r'subscription[\s:]*[\'"]?(https?://[^\s\'"]+)[\'"]?',
            r'sub[\s:]*[\'"]?(https?://[^\s\'"]+)[\'"]?',
            r'اشتراک[\s:]*[\'"]?(https?://[^\s\'"]+)[\'"]?',
            r'لینک[\s:]*[\'"]?(https?://[^\s\'"]+)[\'"]?',
            r'v2ray[\s:]*[\'"]?(https?://[^\s\'"]+)[\'"]?',
            r'vless[\s:]*[\'"]?(https?://[^\s\'"]+)[\'"]?',
            r'vmess[\s:]*[\'"]?(https?://[^\s\'"]+)[\'"]?',
            r'config.*[\'"]?(https?://raw\.githubusercontent\.com[^\s\'"]+)[\'"]?',
        ]

    def _parse_relative_date(self, text):
        text = text.lower()
        now = datetime.now()
        match = re.search(r'(\d+)\s+day[s]?\s+ago', text)
        if match:
            return now - timedelta(days=int(match.group(1)))
        match = re.search(r'(\d+)\s+month[s]?\s+ago', text)
        if match:
            return now - timedelta(days=int(match.group(1))*30)
        match = re.search(r'(\d+)\s+year[s]?\s+ago', text)
        if match:
            return now - timedelta(days=int(match.group(1))*365)
        if 'yesterday' in text:
            return now - timedelta(days=1)
        if 'today' in text:
            return now
        return None

    def _parse_absolute_date(self, date_str):
        date_str = date_str.strip()
        try:
            if 'T' in date_str:
                return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except:
            pass
        patterns = [
            (r'(\d{4})-(\d{1,2})-(\d{1,2})', '%Y-%m-%d'),
            (r'(\d{1,2})/(\d{1,2})/(\d{4})', '%m/%d/%Y'),
            (r'(\d{1,2})/(\d{1,2})/(\d{4})', '%d/%m/%Y'),
            (r'(\d{1,2})-(\d{1,2})-(\d{4})', '%m-%d-%Y'),
            (r'(\d{1,2})-(\d{1,2})-(\d{4})', '%d-%m-%Y'),
            (r'(\d{4})/(\d{1,2})/(\d{1,2})', '%Y/%m/%d'),
            (r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', '%d %b %Y'),
            (r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', '%b %d %Y'),
        ]
        for pat, fmt in patterns:
            match = re.search(pat, date_str)
            if match:
                try:
                    return datetime.strptime(match.group(0), fmt)
                except:
                    continue
        return None

    def parse_date(self, date_string):
        if not date_string:
            return None
        rel = self._parse_relative_date(date_string)
        if rel:
            return rel
        return self._parse_absolute_date(date_string)

    def is_within_days(self, date_obj):
        """بررسی بروز بودن در تعداد روزهای مشخص شده"""
        if not date_obj:
            return False
        now = datetime.now(date_obj.tzinfo) if date_obj.tzinfo else datetime.now()
        return (now - date_obj) <= timedelta(days=DAYS_TO_CHECK)

    def search_github_repos(self):
        repos = []
        search_queries = [
            'v2ray subscription iran', 'v2ray config iran',
            'کانفیگ v2ray ایران', 'v2ray subscription link',
            'v2ray collector', 'iran v2ray configs',
            'v2ray sub', 'vless subscription', 'vmess subscription',
        ]
        
        print(f"Searching GitHub for Iran-related repos (last {DAYS_TO_CHECK} days)...")
        
        for q in search_queries:
            for page in range(1, MAX_PAGES_PER_QUERY + 1):
                try:
                    url = f'https://api.github.com/search/repositories?q={q}&page={page}&per_page=30&sort=updated&order=desc'
                    resp = self.session.get(url, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        for repo in data.get('items', []):
                            name = repo['full_name']
                            if name not in self.seen_repos:
                                self.seen_repos.add(name)
                                repos.append({
                                    'name': name,
                                    'url': repo['html_url'],
                                    'description': repo.get('description', ''),
                                    'updated_at': repo.get('updated_at', ''),
                                })
                    elif resp.status_code == 403:
                        # اگر API rate limit خورد، از scraping استفاده کن
                        self._scrape_github_search(q, page, repos)
                    time.sleep(0.5)  # کاهش تاخیر بین درخواست‌ها
                except Exception as e:
                    print(f"Search error: {e}")
        return repos

    def _scrape_github_search(self, query, page, repos):
        try:
            url = f'https://github.com/search?q={query}&type=repositories&p={page}'
            resp = self.session.get(url, timeout=15)
            soup = BeautifulSoup(resp.text, 'html.parser')
            for link in soup.select('a[href^="/"][href*="/"]'):
                href = link.get('href')
                if href and '/tree/' not in href and len(href.split('/')) == 3:
                    full = href.strip('/')
                    if full not in self.seen_repos:
                        self.seen_repos.add(full)
                        repos.append({
                            'name': full,
                            'url': f'https://github.com/{full}',
                            'description': '',
                            'updated_at': ''
                        })
        except Exception as e:
            print(f"Scraping error: {e}")

    def _has_iran_or_persian(self, text, soup):
        lower = text.lower()
        if any(kw in lower for kw in self.iran_keywords):
            return True
        if re.search(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]', text):
            return True
        title = soup.find('title')
        if title and re.search(r'[\u0600-\u06FF]', title.get_text()):
            return True
        return False

    def _get_last_commit_date(self, repo_url):
        try:
            api_url = repo_url.replace('github.com', 'api.github.com/repos')
            commits_url = f'{api_url}/commits?per_page=1'
            resp = self.session.get(commits_url, timeout=10)
            if resp.status_code == 200:
                commits = resp.json()
                if commits:
                    date_str = commits[0]['commit']['author']['date']
                    return self.parse_date(date_str)
        except:
            pass
        return None

    def _get_last_update_from_page(self, soup):
        rel_time = soup.find('relative-time')
        if rel_time and rel_time.get('datetime'):
            return self.parse_date(rel_time['datetime'])
        for text in soup.stripped_strings:
            if 'ago' in text or 'yesterday' in text or 'today' in text:
                d = self.parse_date(text)
                if d:
                    return d
            d = self._parse_absolute_date(text)
            if d:
                return d
        return None

    def _extract_links_from_repo(self, repo_url):
        links = set()
        repo_path = repo_url.replace('https://github.com/', '')
        paths = [
            'README.md', 'README_Fa.md', 'README_fa.md', 'README.fa.md',
            'subscription.txt', 'sub.txt', 'config.txt', 'v2ray.txt',
            'links.txt', 'urls.txt', 'sub.json', 'config.json', 'subscription.json',
            'README', 'docs/README.md', 'docs/subscription.txt'
        ]
        for branch in ['main', 'master']:
            for path in paths:
                raw = f'https://raw.githubusercontent.com/{repo_path}/{branch}/{path}'
                try:
                    resp = self.session.get(raw, timeout=8)
                    if resp.status_code == 200:
                        content = resp.text
                        for pat in self.sub_patterns:
                            matches = re.findall(pat, content, re.IGNORECASE)
                            for m in matches:
                                if isinstance(m, tuple):
                                    m = m[0] if m else ''
                                if m and 'raw.githubusercontent.com' in m:
                                    links.add(m)
                except:
                    continue
        return links

    def _scan_repo_contents(self, repo_url):
        links = set()
        api_base = repo_url.replace('github.com', 'api.github.com/repos')
        try:
            resp = self.session.get(api_base, timeout=10)
            default_branch = resp.json().get('default_branch', 'main') if resp.status_code == 200 else 'main'
        except:
            default_branch = 'main'
        dirs = ['', 'config', 'subscription', 'sub', 'links', 'docs', 'data', 'files']
        for subdir in dirs:
            url = f'{api_base}/contents/{subdir}?ref={default_branch}'
            try:
                resp = self.session.get(url, timeout=8)
                if resp.status_code == 200:
                    items = resp.json()
                    if isinstance(items, list):
                        for item in items:
                            if item['type'] == 'file' and item['name'].lower().endswith(('.txt','.json','.yaml','.yml','.md','.link')):
                                file_resp = self.session.get(item['download_url'], timeout=8)
                                if file_resp.status_code == 200:
                                    for pat in self.sub_patterns:
                                        matches = re.findall(pat, file_resp.text, re.IGNORECASE)
                                        for m in matches:
                                            if isinstance(m, tuple):
                                                m = m[0] if m else ''
                                            if m and 'raw.githubusercontent.com' in m:
                                                links.add(m)
            except:
                continue
        return links

    def check_repository(self, repo_info):
        repo_url = repo_info['url']
        try:
            resp = self.session.get(repo_url, timeout=10)
            if resp.status_code != 200:
                return False
            soup = BeautifulSoup(resp.text, 'html.parser')
            page_text = soup.get_text()
            if not self._has_iran_or_persian(page_text, soup):
                return False
            last_date = self._get_last_commit_date(repo_url)
            if not last_date:
                last_date = self._get_last_update_from_page(soup)
            if not last_date:
                last_date = self.parse_date(repo_info.get('updated_at', ''))
            if not last_date or not self.is_within_days(last_date):
                return False
            links = self._extract_links_from_repo(repo_url)
            links.update(self._scan_repo_contents(repo_url))
            if links:
                self.subscription_links.update(links)
                print(f"✓ Found {len(links)} links in {repo_url[:50]}...")
                return True
        except Exception as e:
            pass
        return False

    def find_valid_subscriptions(self):
        print("="*50)
        print("V2RAY SUBSCRIPTION FINDER")
        print(f"Looking for repos updated in last {DAYS_TO_CHECK} days")
        print("="*50)
        
        repos = self.search_github_repos()
        print(f"Found {len(repos)} candidate repositories. Checking...")
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(self.check_repository, repo): repo for repo in repos}
            for f in as_completed(futures):
                try:
                    f.result()
                except:
                    pass
        
        print(f"Found {len(self.subscription_links)} subscription links. Validating...")
        
        valid = []
        for link in self.subscription_links:
            try:
                head = self.session.head(link, timeout=5)
                if head.status_code < 400:
                    valid.append(link)
            except:
                pass
        
        unique = list(set(valid))
        with open('pool_address.txt', 'w', encoding='utf-8') as f:
            for line in unique:
                f.write(line + '\n')
        
        print(f"\n✅ Done! Saved {len(unique)} unique valid subscription links to pool_address.txt")
        return unique

if __name__ == "__main__":
    finder = V2RaySubscriptionFinder()
    finder.find_valid_subscriptions()
