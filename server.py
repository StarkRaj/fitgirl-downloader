"""
FitGirl Fetch — Flask local server
Run:  python server.py
"""

import html as html_module
import re
import time
import uuid
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen

from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS

from fitgirl_fetcher import FitgirlFetcher

fitgirl_fetcher = FitgirlFetcher()
# ── config ─────────────────────────────────────────────────────────────────

MAX_CONCURRENT_WORKERS = 2
FETCH_TIMEOUT          = 25
JOB_TTL                = 3600

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

DOWNLOAD_HEADERS = {
        "User-Agent":      HEADERS["User-Agent"],
        "Accept":          "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",   # disable gzip so we stream raw bytes
        # "Referer":         page_url,     # critical — CDN checks this
        # "Origin":          origin,       # critical — CDN checks this
        "Connection":      "keep-alive",
        "Sec-Fetch-Dest":  "empty",
        "Sec-Fetch-Mode":  "cors",
        "Sec-Fetch-Site":  "cross-site",
    }

# ── in-memory store ────────────────────────────────────────────────────────

jobs: dict   = {}
queue: deque = deque()
semaphore    = threading.Semaphore(MAX_CONCURRENT_WORKERS)
queue_lock   = threading.Lock()

# ── known direct-download hosts ────────────────────────────────────────────

# FF-only: other mirrors disabled (uncomment to restore).
DD_HOSTS = {
    # "1fichier.com":      "1fichier",
    # "gofile.io":         "GoFile",
    # "pixeldrain.com":    "PixelDrain",
    # "buzzheavier.com":   "BuzzHeavier",
    # "datanodes.to":      "DataNodes",
    # "filecrypt.cc":      "FileCrypt",
    # "rapidgator.net":    "RapidGator",
    # "filejoker.net":     "FileJoker",
    # "nitroflare.com":    "NitroFlare",
    # "turbobit.net":      "TurboBit",
    # "hitfile.net":       "HitFile",
    # "ddownload.com":     "DDownload",
    # "mega.nz":           "MEGA",
    # "mediafire.com":     "MediaFire",
    # "drive.google.com":  "Google Drive",
    # "onedrive.live.com": "OneDrive",
    # "dropbox.com":       "Dropbox",
    # "uploadhaven.com":   "UploadHaven",
    # "hexupload.net":     "HexUpload",
    # "send.cm":           "Send.cm",
    # "bowfile.com":       "BowFile",
    "fuckingfast.co":    "FuckingFast",
    "ff.io":             "FuckingFast",
}

FF_DOMAINS = {"fuckingfast.co", "ff.io"}

ALLOWED_PROXY_HOSTS = {
    "fuckingfast.co",
    "ff.io",
    "cdn.fuckingfast.co",
    "cdn.ff.io",
}

# ── link helpers ───────────────────────────────────────────────────────────

def classify_link(href):
    try:
        host = urlparse(href).netloc.lower().lstrip("www.")
        for domain, label in DD_HOSTS.items():
            if host == domain or host.endswith("." + domain):
                return label
    except Exception:
        pass
    return None


def is_ff_link(href):
    try:
        host = urlparse(href).netloc.lower().lstrip("www.")
        return host in FF_DOMAINS
    except Exception:
        return False


def _ff_direct_url_score(u: str) -> int:
    """Prefer CDN file URLs over random data-url (icons, API, etc.)."""
    try:
        p = urlparse(u)
        host = p.netloc.lower()
        path = (p.path or "").lower()
    except Exception:
        return 0
    score = 0
    if "cdn." in host or host.startswith("cdn"):
        score += 8
    if "fuckingfast" in host or host.endswith(".ff.io") or host == "ff.io":
        score += 3
    for ext in (".rar", ".zip", ".7z", ".iso", ".bin"):
        if ext in path:
            score += 12
            break
    if "/api/" in path or "javascript:" in u.lower():
        score -= 50
    return score


def _collect_quoted_after(raw: str, needle: str, start_at: int = 0) -> list:
    """Find needle, then read the next single- or double-quoted string."""
    out = []
    pos = start_at
    while True:
        idx = raw.find(needle, pos)
        if idx == -1:
            break
        after = raw[idx + len(needle) :].lstrip()
        if not after or after[0] not in ('"', "'"):
            pos = idx + len(needle)
            continue
        q = after[0]
        end = after.find(q, 1)
        if end == -1:
            break
        val = html_module.unescape(after[1:end].strip())
        if val.startswith("http"):
            out.append(val)
        pos = idx + len(needle)
    return out


def resolve_ff_direct_url(ff_page_url):
    """
    Fetch a fuckingfast.co / ff.io landing page and extract the real CDN file URL.
    Collects all candidates; the first data-url on the page is often NOT the file.
    """
    req = Request(ff_page_url, headers=HEADERS)
    try:
        with urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    candidates = []

    # All data-url="..." (multiple on page)
    candidates.extend(_collect_quoted_after(raw, "data-url="))

    for varname in ("downloadUrl", "download_url", "downloadURL", "fileUrl", "file_url"):
        for sep in (" = ", "= ", " =", "="):
            needle = varname + sep
            pos = 0
            while True:
                idx = raw.find(needle, pos)
                if idx == -1:
                    break
                after = raw[idx + len(needle) :].lstrip()
                if after and after[0] in ('"', "'"):
                    q = after[0]
                    end = after.find(q, 1)
                    if end != -1:
                        val = html_module.unescape(after[1:end].strip())
                        if val.startswith("http"):
                            candidates.append(val)
                pos = idx + len(needle)

    # href="https://...archive..."
    for ext in (".rar", ".zip", ".7z", ".iso", ".bin"):
        search = 'href="https://'
        start = 0
        while True:
            idx = raw.find(search, start)
            if idx == -1:
                break
            url_start = idx + 6
            url_end = raw.find('"', url_start)
            if url_end == -1:
                break
            candidate = html_module.unescape(raw[url_start:url_end].strip())
            if ext in candidate.lower():
                candidates.append(candidate)
            start = url_end

    # Single-quoted href
    for ext in (".rar", ".zip", ".7z", ".iso", ".bin"):
        search = "href='https://"
        start = 0
        while True:
            idx = raw.find(search, start)
            if idx == -1:
                break
            url_start = idx + 6
            url_end = raw.find("'", url_start)
            if url_end == -1:
                break
            candidate = html_module.unescape(raw[url_start:url_end].strip())
            if ext in candidate.lower():
                candidates.append(candidate)
            start = url_end

    if not candidates:
        return None

    # Dedupe, keep order for stable sort
    seen = set()
    uniq = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            uniq.append(c)

    uniq.sort(key=_ff_direct_url_score, reverse=True)
    return uniq[0] if uniq else None


# ── HTML parser ────────────────────────────────────────────────────────────

class FitgirlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title        = ""
        self.magnets      = []
        self.torrents     = []
        self.direct_links = []
        self._in_h1       = False
        self._h1_done     = False
        self._in_title    = False
        self._seen        = set()

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "h1" and not self._h1_done:
            self._in_h1 = True
        if tag == "a":
            href = d.get("href", "")
            if not href or href in self._seen:
                return
            self._seen.add(href)
            # FF-only scrape: skip magnets / torrents.
            # if href.startswith("magnet:"):
            #     self.magnets.append(href)
            # elif href.endswith(".torrent"):
            #     self.torrents.append(href)
            if href.startswith("magnet:") or href.endswith(".torrent"):
                pass
            else:
                label = classify_link(href)
                if label:
                    self.direct_links.append({"host": label, "url": href})

    def handle_endtag(self, tag):
        if tag == "title": self._in_title = False
        if tag == "h1":    self._in_h1 = False; self._h1_done = True

    def handle_data(self, data):
        if self._in_h1 and not self._h1_done:
            self.title += data.strip()
        elif self._in_title and not self.title:
            self.title = (data
                .replace("- FitGirl Repacks", "")
                .replace("-- FitGirl Repacks", "")
                .strip())


def extract_meta(html):
    meta = {}
    m = re.search(r"(?:repack\s*size|size)[:\s]*([0-9][0-9.,]*\s*(?:GB|MB))", html, re.I)
    if m: meta["repack_size"] = m.group(1).strip()
    m = re.search(r"original\s*size[:\s]*([0-9][0-9.,]*\s*(?:GB|MB))", html, re.I)
    if m: meta["original_size"] = m.group(1).strip()
    m = re.search(r"(?:genres?|tags?)[:/\s]+([^\n<]{3,80})", html, re.I)
    if m: meta["genre"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()[:80]
    return meta


def extract_cover_url(html: str, page_url: str):
    """Best-effort cover from Open Graph / Twitter meta (FitGirl uses WordPress)."""
    patterns = (
        r'<meta[^>]+property\s*=\s*["\']og:image["\'][^>]+content\s*=\s*["\']([^"\']+)["\']',
        r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+property\s*=\s*["\']og:image["\']',
        r'<meta[^>]+name\s*=\s*["\']twitter:image["\'][^>]+content\s*=\s*["\']([^"\']+)["\']',
        r'<meta[^>]+name\s*=\s*["\']twitter:image:src["\'][^>]+content\s*=\s*["\']([^"\']+)["\']',
        r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+name\s*=\s*["\']twitter:image["\']',
    )
    for pat in patterns:
        m = re.search(pat, html, re.I | re.S)
        if not m:
            continue
        raw = html_module.unescape(m.group(1).strip())
        if not raw or raw.startswith("data:"):
            continue
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        if raw.startswith("//"):
            return "https:" + raw
        if raw.startswith("/"):
            return urljoin(page_url, raw)
    return None


# ── scraper ────────────────────────────────────────────────────────────────

def scrape(url):
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            html = resp.read().decode(charset, errors="replace")
    except HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} -- page unavailable")
    except URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")

    parser = FitgirlParser()
    parser.feed(html)
    meta = extract_meta(html)
    cover_url = extract_cover_url(html, url)

    if not parser.direct_links:
        raise RuntimeError("No FuckingFast links found -- page may need JS rendering")

    # Resolve FuckingFast page URLs -> direct CDN URLs in parallel
    def resolve_entry(entry):
        if is_ff_link(entry["url"]):
            direct = resolve_ff_direct_url(entry["url"])
            if direct:
                return {
                    "host":     entry["host"],
                    "url":      direct,
                    "resolved": True,
                    "page_url": entry["url"],
                }
        return entry

    with ThreadPoolExecutor(max_workers=6) as ex:
        resolved_links = list(ex.map(resolve_entry, parser.direct_links))

    grouped = {}
    for entry in resolved_links:
        grouped.setdefault(entry["host"], []).append(entry["url"])

    return {
        "title":         parser.title or "Unknown Game",
        # FF-only: magnets / torrents not collected.
        "magnets":       [],
        "torrents":      [],
        "direct_links":  resolved_links,
        "by_host":       grouped,
        "cover_url":     cover_url,
        "repack_size":   meta.get("repack_size"),
        "original_size": meta.get("original_size"),
        "genre":         meta.get("genre"),
    }


# ── worker ─────────────────────────────────────────────────────────────────

def process_job(job):
    job_id = job["job_id"]
    with semaphore:
        jobs[job_id]["status"]     = "processing"
        jobs[job_id]["started_at"] = int(time.time())
        try:
            data = scrape(job["url"])
            jobs[job_id].update({"status": "done", "completed_at": int(time.time()), **data})
        except Exception as exc:
            jobs[job_id].update({"status": "error", "error": str(exc), "failed_at": int(time.time())})


def worker_loop():
    while True:
        with queue_lock:
            job = queue.popleft() if queue else None
        if job:
            threading.Thread(target=process_job, args=(job,), daemon=True).start()
        time.sleep(0.5)


def evict_old_jobs():
    cutoff = time.time() - JOB_TTL
    stale = [jid for jid, j in list(jobs.items()) if j.get("created_at", 0) < cutoff]
    for jid in stale:
        jobs.pop(jid, None)


# ── Flask app ──────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)

@app.post("/fetch")
def post_fetch():
    body = request.get_json(silent=True) or {}
    url  = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Missing url"}), 400
    try:
        parsed = urlparse(url)
        assert "fitgirl-repacks" in parsed.netloc and parsed.path not in ("", "/")
    except Exception:
        return jsonify({"error": "URL must be from fitgirl-repacks.site"}), 400

    evict_old_jobs()
    job_id = str(uuid.uuid4())
    job = {"job_id": job_id, "status": "pending", "url": url, "created_at": int(time.time())}
    jobs[job_id] = job
    with queue_lock:
        queue.append(job)
    return jsonify({"job_id": job_id, "status": "pending", "queue_depth": len(queue)}), 202


@app.get("/status/<job_id>")
def get_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.get("/health")
def health():
    return jsonify({
        "status":         "ok",
        "queue_depth":    len(queue),
        "jobs_in_memory": len(jobs),
    })


@app.get("/resolve-ff")
def resolve_ff():
    """Resolve a FuckingFast page URL to its direct CDN URL."""
    ff_url = request.args.get("url", "").strip()
    if not ff_url or not is_ff_link(ff_url):
        return jsonify({"error": "Not a FuckingFast URL"}), 400
    direct = resolve_ff_direct_url(ff_url)
    if not direct:
        return jsonify({"error": "Could not resolve -- page structure may have changed"}), 502
    return jsonify({"direct_url": direct})


def _allowed_referer(referer: str) -> bool:
    """Only pass Referer to CDN if it looks like a legitimate source page."""
    if not referer or not referer.startswith("http"):
        return False
    try:
        h = urlparse(referer).netloc.lower().lstrip("www.")
    except Exception:
        return False
    allowed_suffixes = (
        "fuckingfast.co",
        "ff.io",
        "fitgirl-repacks.site",
    )
    return any(h == s or h.endswith("." + s) for s in allowed_suffixes)


@app.get("/download")
def proxy_download():
    """Stream a remote file through the local server so the browser saves it directly."""
    target_url = request.args.get("url", "").strip()
    download_url = fitgirl_fetcher.fetch_file_url(target_url)
    return jsonify({"download_url": download_url})


# ── main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=worker_loop, daemon=True).start()
    print("  FitGirl Fetch running on http://localhost:8000")
    print(f"  Max concurrent workers: {MAX_CONCURRENT_WORKERS}")
    app.run(host="0.0.0.0", port=8000, debug=False)