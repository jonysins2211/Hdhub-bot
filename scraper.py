"""
HDhub4u Content Scraper
"""

import asyncio
import aiohttp
import re
import logging
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from datetime import datetime

logger = logging.getLogger(__name__)


class HDhub4uScraper:
    def __init__(self):
        self.main_url = "https://hdhub4u.rehab"
        self.headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/131.0.0.0 Safari/537.36'
            ),
            'Cookie': 'xla=s4t'
        }
        # No persistent session — create fresh per call to avoid event-loop issues

    async def _fetch(self, url: str, timeout: int = 30) -> Optional[str]:
        """Fetch URL and return HTML text, or None on failure."""
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    allow_redirects=True,
                ) as response:
                    if response.status != 200:
                        logger.warning(f"HTTP {response.status} for {url}")
                        return None
                    return await response.text()
        except asyncio.TimeoutError:
            logger.error(f"Timeout fetching {url}")
            return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None

    async def get_latest_content(self, cache_manager) -> List[Dict]:
        """Get latest content from HDhub4u with caching."""
        cached = cache_manager.get('latest_content')
        if cached:
            logger.info("Returning cached content")
            return cached

        html = await self._fetch(f"{self.main_url}/page/1/")
        if not html:
            return []

        soup = BeautifulSoup(html, 'html.parser')
        content_items = []

        for item in soup.select('.recent-movies > li.thumb')[:10]:
            parsed = self._parse_item(item)
            if parsed:
                content_items.append(parsed)

        cache_manager.set('latest_content', content_items, ttl=300)
        logger.info(f"Scraped {len(content_items)} items")
        return content_items

    def _parse_item(self, item) -> Optional[Dict]:
        try:
            title_elem = item.select_one(
                'figcaption:nth-child(2) > a:nth-child(1) > p:nth-child(1)'
            )
            if not title_elem:
                return None
            title_text = title_elem.get_text(strip=True)

            url_elem = item.select_one('figure:nth-child(1) > a:nth-child(2)')
            if not url_elem:
                return None
            url = url_elem.get('href', '')

            poster_elem = item.select_one('figure:nth-child(1) > img:nth-child(1)')
            poster_url = poster_elem.get('src', '') if poster_elem else ''

            return {
                'title': self._clean_title(title_text),
                'url': url,
                'poster_url': poster_url,
                'quality': self._get_quality(title_text),
                'scraped_at': datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Error parsing item: {e}")
            return None

    def _clean_title(self, title: str) -> str:
        cleaned = re.sub(
            r'\b(480p|720p|1080p|2160p|4K|HEVC|x264|x265|HDRip|WEB-DL|BluRay)\b',
            '', title, flags=re.IGNORECASE
        )
        return re.sub(r'\s+', ' ', cleaned).strip()

    def _get_quality(self, text: str) -> str:
        patterns = [
            (r'\b(4k|uhd|2160p)\b', '4K UHD'),
            (r'\b(1080p)\b', '1080p FHD'),
            (r'\b(720p)\b', '720p HD'),
            (r'\b(480p)\b', '480p'),
            (r'\b(bluray)\b', 'BluRay'),
            (r'\b(web-?dl|webrip)\b', 'WEB-DL'),
        ]
        for pattern, quality in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return quality
        return 'HD'

    async def get_download_links(self, url: str, cache_manager) -> List[Dict]:
        """Get download links for a specific content item with caching."""
        cache_key = f'links_{url}'
        cached = cache_manager.get(cache_key)
        if cached:
            return cached

        html = await self._fetch(url)
        if not html:
            return []

        soup = BeautifulSoup(html, 'html.parser')
        links = []
        seen_urls = set()

        valid_domains = [
            'hdstream4u', 'hubstream', 'hubdrive', 'hubcloud',
            'hubcdn', 'pixeldrain', 'hblinks', 'buzzserver',
            'mega.nz', 'mediafire', 'drive.google',
        ]

        for elem in soup.select('h3 a, h4 a, h5 a, .page-body > div a, .entry-content a'):
            link_url = elem.get('href', '')
            link_text = elem.get_text(strip=True)

            if link_url in seen_urls:
                continue

            if any(domain in link_url.lower() for domain in valid_domains):
                quality = self._extract_quality_from_text(link_text)
                links.append({
                    'url': link_url,
                    'quality': quality,
                    'text': link_text,
                    'server': self._extract_server_name(link_url),
                })
                seen_urls.add(link_url)

        quality_order = {'4K': 0, '2160p': 0, '1080p': 1, '720p': 2, '480p': 3, 'Download': 4}
        links.sort(key=lambda x: quality_order.get(x['quality'], 5))

        cache_manager.set(cache_key, links, ttl=3600)
        return links

    def _extract_quality_from_text(self, text: str) -> str:
        u = text.upper()
        if '2160' in text or '4K' in u or 'UHD' in u:
            return '4K'
        if '1440' in text or 'QHD' in u:
            return '1440p'
        if '1080' in text or 'FHD' in u:
            return '1080p'
        if '720' in text:
            return '720p'
        if '480' in text or 'SD' in u:
            return '480p'
        if '360' in text:
            return '360p'
        if 'HD' in u:
            return '720p'
        return 'Download'

    def _extract_server_name(self, url: str) -> str:
        u = url.lower()
        if 'hubdrive' in u:     return 'HubDrive'
        if 'hubcloud' in u:     return 'HubCloud'
        if 'hubstream' in u:    return 'HubStream'
        if 'hdstream4u' in u:   return 'HDStream4u'
        if 'pixeldrain' in u:   return 'PixelDrain'
        if 'hubcdn' in u:       return 'HubCDN'
        if 'mega.nz' in u:      return 'Mega'
        if 'mediafire' in u:    return 'MediaFire'
        if 'drive.google' in u: return 'Google Drive'
        return 'Download'

    async def check_for_updates(self, existing_urls: List[str], cache_manager) -> List[Dict]:
        updated_items = []
        for url in existing_urls:
            try:
                new_links = await self.get_download_links(url, cache_manager)
                cache_key = f'links_prev_{url}'
                old_links = cache_manager.get(cache_key)
                if old_links and new_links != old_links:
                    updated_items.append({'url': url, 'new_links': new_links})
                cache_manager.set(cache_key, new_links, ttl=86400)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error checking updates for {url}: {e}")
        return updated_items
