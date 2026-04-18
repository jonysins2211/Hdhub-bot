"""
HubDrive bypass — resolves hubdrive.space/file/ID links to direct download URLs.

How it works:
  1. Fetch the file page to grab the _token hidden field
  2. POST to the /download API endpoint with that token
  3. Parse the JSON response which contains direct server links
     (FSL, Pixeldrain, etc.)

If bypass fails we fall back to returning the original HubDrive URL.
"""

import asyncio
import re
import logging
from typing import List, Dict, Optional
import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HUBDRIVE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://hubdrive.space/",
}

# Map raw server labels from the API to friendly display names
SERVER_LABELS = {
    "fslv2":      "FSLv2 Server",
    "fsl":        "FSL Server",
    "pixeldrain": "Pixeldrain",
    "10gbps":     "10Gbps Server",
    "gdrive":     "Google Drive",
    "mediafire":  "MediaFire",
    "mega":       "Mega",
}


async def _get(session: aiohttp.ClientSession, url: str, **kwargs) -> Optional[aiohttp.ClientResponse]:
    try:
        resp = await session.get(url, timeout=aiohttp.ClientTimeout(total=20), **kwargs)
        return resp
    except Exception as e:
        logger.error(f"GET {url} failed: {e}")
        return None


async def _post(session: aiohttp.ClientSession, url: str, **kwargs) -> Optional[dict]:
    try:
        async with session.post(url, timeout=aiohttp.ClientTimeout(total=20), **kwargs) as resp:
            if resp.content_type == "application/json" or "json" in resp.content_type:
                return await resp.json(content_type=None)
            text = await resp.text()
            import json
            return json.loads(text)
    except Exception as e:
        logger.error(f"POST {url} failed: {e}")
        return None


async def resolve_hubdrive(hubdrive_url: str) -> List[Dict]:
    """
    Given a HubDrive URL like https://hubdrive.space/file/2317359411,
    return a list of direct download dicts:
        [{"label": "FSLv2 Server", "url": "https://..."}]

    Falls back to [{"label": "HubDrive", "url": hubdrive_url}] on failure.
    """
    fallback = [{"label": "HubDrive", "url": hubdrive_url}]

    # Extract file ID
    match = re.search(r"/file/(\d+)", hubdrive_url)
    if not match:
        logger.warning(f"Cannot parse HubDrive URL: {hubdrive_url}")
        return fallback

    file_id = match.group(1)
    base = re.match(r"(https?://[^/]+)", hubdrive_url)
    if not base:
        return fallback
    base_url = base.group(1)  # e.g. https://hubdrive.space

    async with aiohttp.ClientSession(headers=HUBDRIVE_HEADERS) as session:
        # Step 1 — fetch the file page to grab _token
        resp = await _get(session, hubdrive_url)
        if resp is None:
            return fallback

        async with resp:
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")

        # Look for hidden _token input
        token_input = soup.find("input", {"name": "_token"})
        if not token_input:
            # Try meta tag
            meta = soup.find("meta", {"name": "csrf-token"})
            token = meta["content"] if meta else None
        else:
            token = token_input.get("value")

        if not token:
            logger.warning(f"No _token found on HubDrive page: {hubdrive_url}")
            return fallback

        logger.debug(f"HubDrive token: {token[:10]}...")

        # Step 2 — POST to the download API
        api_url = f"{base_url}/ajax/download"
        payload = {"id": file_id, "_token": token}

        data = await _post(
            session,
            api_url,
            data=payload,
            headers={**HUBDRIVE_HEADERS, "Origin": base_url},
        )

        if not data:
            # Try alternative endpoint
            api_url2 = f"{base_url}/dl"
            data = await _post(session, api_url2, data=payload,
                               headers={**HUBDRIVE_HEADERS, "Origin": base_url})

        if not data:
            logger.warning(f"No data from HubDrive API for {hubdrive_url}")
            return fallback

        logger.debug(f"HubDrive API response keys: {list(data.keys())}")

        # Step 3 — parse response
        results = []

        # Common response shapes:
        # {"url": "...", "servers": {"fsl": "url", "pixeldrain": "url"}}
        # {"links": [{"server": "fsl", "url": "..."}]}
        # {"data": {"download_url": "..."}}

        servers = data.get("servers") or data.get("server") or {}
        if isinstance(servers, dict):
            for key, url in servers.items():
                if url and isinstance(url, str) and url.startswith("http"):
                    label = SERVER_LABELS.get(key.lower(), key.title() + " Server")
                    results.append({"label": label, "url": url})

        links_list = data.get("links") or []
        for entry in links_list:
            if isinstance(entry, dict):
                url = entry.get("url") or entry.get("link", "")
                server = entry.get("server") or entry.get("name", "Download")
                if url and url.startswith("http"):
                    label = SERVER_LABELS.get(server.lower(), server)
                    results.append({"label": label, "url": url})

        # Single direct URL
        direct = data.get("url") or data.get("download_url") or data.get("direct_url")
        if direct and isinstance(direct, str) and direct.startswith("http"):
            results.append({"label": "Direct Download", "url": direct})

        if results:
            logger.info(f"HubDrive bypass OK for {file_id}: {len(results)} links")
            return results

        logger.warning(f"HubDrive API returned no usable links for {file_id}, raw: {data}")
        return fallback


async def resolve_links_batch(raw_links: List[Dict]) -> List[Dict]:
    """
    Takes the list of raw links from scraper.get_download_links() and,
    for any HubDrive URLs, resolves them to direct links.
    Non-HubDrive links are passed through unchanged.

    Returns a flat list of {"label": ..., "url": ..., "quality": ...} dicts.
    """
    resolved = []
    tasks = []
    indices = []

    for i, link in enumerate(raw_links):
        url = link.get("url", "")
        if "hubdrive" in url.lower():
            tasks.append(resolve_hubdrive(url))
            indices.append(i)
        else:
            # Pass through: pixeldrain, mediafire, mega, etc.
            resolved.append({
                "label": _friendly_label(link),
                "url": url,
                "quality": link.get("quality", ""),
            })

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in zip(indices, results):
            original = raw_links[idx]
            if isinstance(result, Exception):
                logger.error(f"Bypass exception: {result}")
                resolved.append({
                    "label": "HubDrive",
                    "url": original["url"],
                    "quality": original.get("quality", ""),
                })
            else:
                for r in result:
                    resolved.append({
                        "label": r["label"],
                        "url": r["url"],
                        "quality": original.get("quality", ""),
                    })

    return resolved


def _friendly_label(link: Dict) -> str:
    url = link.get("url", "").lower()
    text = link.get("text", "") or link.get("quality", "") or ""

    if "pixeldrain" in url: return "Pixeldrain"
    if "mediafire" in url:  return "MediaFire"
    if "mega.nz" in url:    return "Mega"
    if "drive.google" in url: return "Google Drive"
    if "hubstream" in url:  return "HubStream"
    if "hubcloud" in url:   return "HubCloud"
    if "hdstream4u" in url: return "HDStream4u"

    server = link.get("server", "")
    if server and server != "Download":
        return server

    quality = link.get("quality", "")
    if quality and quality != "Download":
        return f"{quality} Download"

    return "Download"
