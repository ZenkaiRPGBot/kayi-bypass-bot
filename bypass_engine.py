import re
import base64
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import aiohttp
from bs4 import BeautifulSoup if False else None

# Tracking query parameters to strip
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "gbraid", "wbraid", "igsh", "si", "feature",
    "ref", "ref_src", "mc_cid", "mc_eid", "_ga", "_gl", "yclid"
}

# Regex for finding URLs
URL_PATTERN = re.compile(
    r'https?://[a-zA-Z0-9\-\._~:/\?#\[\]@!$&\'\(\)\*\+,;=%]+',
    re.IGNORECASE
)

def extract_urls(text: str) -> list[str]:
    """Finds all URLs inside text."""
    if not text:
        return []
    return URL_PATTERN.findall(text)

def clean_tracking_params(url_str: str) -> tuple[str, list[str]]:
    """Strips common marketing and spyware tracking parameters from URL query string."""
    parsed = urlparse(url_str)
    if not parsed.query:
        return url_str, []

    qs = parse_qs(parsed.query, keep_blank_values=True)
    removed = []
    cleaned_qs = {}

    for k, v in qs.items():
        if k.lower() in TRACKING_PARAMS:
            removed.append(k)
        else:
            cleaned_qs[k] = v

    # Reconstruct query string
    new_query = urlencode(cleaned_qs, doseq=True)
    cleaned_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))
    return cleaned_url, removed

def check_embedded_destination(url_str: str) -> str | None:
    """Checks if destination URL is base64 encoded or passed as a query parameter."""
    parsed = urlparse(url_str)
    qs = parse_qs(parsed.query)

    for param in ["url", "dest", "destination", "link", "target", "r", "to", "goto"]:
        if param in qs and qs[param]:
            val = qs[param][0]
            # 1. Plain URL
            if val.startswith("http://") or val.startswith("https://"):
                return val
            # 2. Base64 URL
            try:
                decoded = base64.b64decode(val).decode("utf-8", errors="ignore")
                if decoded.startswith("http://") or decoded.startswith("https://"):
                    return decoded
            except Exception:
                pass
    return None

async def resolve_url_chain(session: aiohttp.ClientSession, start_url: str, max_hops: int = 15) -> dict:
    """
    Follows HTTP redirect chains, expands shortened links, and returns the final cleaned URL.
    """
    current_url = start_url
    hops = 0
    history = [current_url]
    page_title = None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    try:
        while hops < max_hops:
            # Check for embedded target before request
            embedded = check_embedded_destination(current_url)
            if embedded and embedded != current_url:
                current_url = embedded
                history.append(current_url)
                hops += 1

            async with session.get(current_url, headers=headers, allow_redirects=False, ssl=False, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                # 301, 302, 303, 307, 308 redirects
                if resp.status in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location")
                    if not loc:
                        break
                    
                    # Handle relative redirect URLs
                    if loc.startswith("/"):
                        parsed_cur = urlparse(current_url)
                        loc = f"{parsed_cur.scheme}://{parsed_cur.netloc}{loc}"
                    elif not (loc.startswith("http://") or loc.startswith("https://")):
                        break

                    current_url = loc
                    history.append(current_url)
                    hops += 1
                else:
                    # Final destination reached, extract page title if HTML
                    if "text/html" in resp.headers.get("Content-Type", ""):
                        chunk = await resp.content.read(4096)
                        text = chunk.decode("utf-8", errors="ignore")
                        title_match = re.search(r'<title[^>]*>(.*?)</title>', text, re.IGNORECASE | re.DOTALL)
                        if title_match:
                            page_title = title_match.group(1).strip()
                    break
    except Exception as e:
        print(f"[bypass_engine] Redirect notice for {current_url}: {e}")

    # Check embedded destination one final time
    final_embedded = check_embedded_destination(current_url)
    if final_embedded:
        current_url = final_embedded
        hops += 1

    clean_url, removed_trackers = clean_tracking_params(current_url)

    return {
        "original_url": start_url,
        "final_url": current_url,
        "clean_url": clean_url,
        "hops": hops,
        "removed_trackers": removed_trackers,
        "page_title": page_title
    }
