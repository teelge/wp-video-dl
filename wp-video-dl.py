#!/usr/bin/env python3
"""wp-video-dl — download videos from a WordPress-hosted site, month by month,
interactively or from the CLI. Pure stdlib, no installs needed.

How it works
------------
You name the target website (any WordPress site with the WP REST API enabled —
it queries /wp-json/wp/v2/posts for the chosen month), it resolves every post to
its real .mp4, probes the true file sizes without downloading anything, shows a
count + estimated gigabytes, then asks whether to download. Every run is logged
to a file for debugging (full transcript + tracebacks).

Commands:
    wp-video-dl month                 site + month prompts, survey, then download
    wp-video-dl survey YEAR MONTH     just show count + total GB
    wp-video-dl dl-month YEAR MONTH   survey + download (prompts unless -y)
    wp-video-dl list [PAGE]           list post links on a listing page
    wp-video-dl info <POST_URL>       show the resolved .mp4 for one post
    wp-video-dl dl <POST_URL>         download one post's video

Ways to give the target site (any command):
    wp-video-dl month --site https://example.com
    export WPVIDL_SITE=https://example.com ; wp-video-dl month
    (or just run `wp-video-dl month` and answer the site prompt)

YEAR MONTH forms:  '2026 8'  |  '2026-08'  |  '2026/8'
Options:  --site URL    target website
          --out DIR     default $WPVIDL_OUT or ~/wp-video-dl/downloads
          --log FILE    log to this exact file
          --ua STR
          -y/--yes      skip the download confirm
Env:
    WPVIDL_SITE     default target website
    WPVIDL_LOG_DIR  where per-run logs go   (~/wp-video-dl/logs)
    WPVIDL_OUT      default download folder
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
import urllib.request
import urllib.parse

# ---- mutable globals set once the target site is known ----
SITE = None            # e.g. "https://example.com"
API_URL = None         # SITE + "/wp-json/wp/v2/posts"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
IGNORE = {"categories", "actors", "report-abuse", "page", "tag",
          "author", "privacy", "terms", "contact", "video", "watch", "n"}


# ---------------- target site ----------------

def normalize_site(raw: str) -> str:
    s = raw.strip()
    if not s:
        raise ValueError("empty site")
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    s = re.split(r"/wp-json|/wp-admin|/wp-login", s)[0]
    return s.rstrip("/")


def set_site(raw: str):
    global SITE, API_URL
    SITE = normalize_site(raw)
    API_URL = SITE + "/wp-json/wp/v2/posts"


def derive_site_from_url(url: str) -> str | None:
    if not isinstance(url, str) or "://" not in url:
        return None
    p = urllib.parse.urlparse(url)
    if p.scheme in ("http", "https") and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return None


def pick_site(args, allow_prompt: bool) -> str | None:
    if getattr(args, "site", None):
        set_site(args.site)
        return SITE
    if os.environ.get("WPVIDL_SITE"):
        set_site(os.environ["WPVIDL_SITE"])
        return SITE
    if allow_prompt:
        while True:
            try:
                raw = input("Target website (WordPress video site, "
                            "e.g. https://example.com): ").strip()
            except EOFError:
                return None
            if raw:
                try:
                    set_site(raw)
                    return SITE
                except ValueError:
                    print("  that doesn't look like a URL; try again.")
    return None


# ---------------- logging ----------------

class _Tee:
    def __init__(self, out, fileh):
        self.out, self.fileh = out, fileh

    def write(self, b):
        try:
            self.out.write(b)
        except Exception:
            pass
        try:
            self.fileh.write(b)
            self.fileh.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self.out.flush()
        except Exception:
            pass


def enable_log(path: str):
    try:
        fh = open(path, "a", encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"[warn] cannot open log {path}: {e}")
        return
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)

    def _hook(t, v, tb):
        sys.stderr.write("".join(traceback.format_exception(t, v, tb)) + "\n")
    sys.excepthook = _hook
    print(f"[log] session log -> {path}\n")


def default_log() -> str:
    d = os.environ.get("WPVIDL_LOG_DIR") or os.path.join(
        os.path.expanduser("~"), "wp-video-dl", "logs")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, time.strftime("wp_video_dl_%Y%m%d_%H%M%S.log"))


# ---------------- http ----------------

def http(url: str, headers: dict):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(), r.headers


def get(url: str, referer: str | None = None) -> bytes:
    return http(url, {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                      "Referer": referer or (SITE or "")})[0]


def text(url: str, referer: str | None = None) -> str:
    return get(url, referer).decode("utf-8", errors="replace")


def resolve_mp4(url: str, referer: str | None = None) -> str | None:
    """Fetch a post page and return the real .mp4 (prefer the <source> tag)."""
    try:
        html = text(url, referer)
    except Exception:
        return None
    pats = (
        r'<source[^>]+src="([^"]+\.mp4)"',
        r'itemprop="contentURL"\s+content="([^"]+\.mp4)"',
        r'(https?://[^"\']+?\.mp4)',
    )
    for p in pats:
        m = re.search(p, html, re.I)
        if m:
            u = m.group(1).strip()
            if u.startswith("//"):
                u = "https:" + u
            return u.rstrip("/")
    return None


def resolve_post_url(post: str) -> str | None:
    return resolve_mp4(post)


def probe_size(mp4: str) -> int:
    """Total file size in bytes via a zero-length Range request (no body)."""
    try:
        req = urllib.request.Request(mp4, headers={
            "User-Agent": UA, "Referer": SITE or "", "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            _, h = r.read(), r.headers
            if h.get("Content-Range"):
                m = re.search(r"bytes \d+-\d+/(\d+)$", h["Content-Range"])
                if m:
                    return int(m.group(1))
            cl = h.get("Content-Length")
            if cl and cl.isdigit() and r.status == 200:
                return int(cl)
    except Exception:
        pass
    return 0


def list_posts(page: str) -> list[str]:
    if not page.startswith("http"):
        n = int(page) if page.isdigit() else 1
        base_url = SITE + (f"/page/{n}/" if n > 1 else "/")
        page = base_url
    html = text(page)
    posts, seen = [], set()
    host = urllib.parse.urlparse(SITE).netloc
    pat = re.compile(r'href="https?://(?:www\.)?' + re.escape(host) +
                     r'(?!\?)[^"#]+?"', re.I)
    for m in pat.finditer(html):
        url = m.group(0).split('href="')[1].strip('"')
        path = urllib.parse.urlparse(url).path.strip("/")
        top = path.split("/", 1)[0].lower()
        if not path or top in IGNORE or url in seen:
            continue
        seen.add(url)
        posts.append(url)
    return posts


def wp_posts_for_month(year: int, month: int) -> list[tuple[str, str, str]]:
    """[(title, link, date)] published in the given month (site time)."""
    first = f"{year:04d}-{month:02d}-01T00:00:00"
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    before = f"{ny:04d}-{nm:02d}-01T00:00:00"
    prefix = f"{year:04d}-{month:02d}"
    out, page = [], 1
    while True:
        url = (f"{API_URL}?after={urllib.parse.quote(first)}"
               f"&before={urllib.parse.quote(before)}"
               f"&per_page=100&page={page}&orderby=date&order=desc")
        try:
            data, headers = http(url, {"User-Agent": UA, "Accept": "application/json"})
        except Exception as e:
            print(f"  [error] API page {page} failed for {SITE}: {e}")
            break
        try:
            posts = json.loads(data)
        except Exception as e:
            print(f"  [error] not a parseable JSON response — is {SITE} a "
                  f"WordPress site with the REST API enabled? ({e})")
            break
        if not posts:
            break
        for p in posts:
            if p.get("status") == "publish" and p.get("date", "").startswith(prefix):
                title = re.sub(r"<[^>]+>", "", p.get("title", {}).get("rendered", ""))
                out.append((title.strip(), p["link"], p["date"]))
        total = int(headers.get("X-WP-TotalPages") or page)
        if page >= total:
            break
        page += 1
    return out


def safe_name(s: str, maxlen: int = 90) -> str:
    s = re.sub(r'[^\w\-.() ]+', "_", s)
    s = re.sub(r"\s+", "_", s).strip("_.")
    return s[:maxlen] or "video"


def filename_for(mp4: str) -> str:
    base = os.path.basename(urllib.parse.urlparse(mp4).path)
    return safe_name(base[:-4] if base.lower().endswith(".mp4") else base) + ".mp4"


def title_filename(title: str, seen: dict | None = None) -> str:
    """Build a filename from the site's own title, deduped against `seen`."""
    fname = safe_name(title or "video") + ".mp4"
    if seen is not None:
        n = seen.get(fname, 0)
        seen[fname] = n + 1
        if n:                            # collision -> name_2.mp4, name_3.mp4, ...
            stem, ext = os.path.splitext(fname)
            fname = f"{stem}_{n + 1}{ext}"
    return fname


def page_title(url: str, referer: str | None = None) -> str:
    """Best-effort page <title> for a single post URL (cleaned of site suffix)."""
    try:
        html = text(url, referer)
    except Exception:
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not m:
        return ""
    t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return re.split(r"\s*(?:–|—|-|\|)\s*", t, maxsplit=1)[0].strip() or ""


def human_gb(n: int, digits: int = 1) -> str:
    return f"{n / (2**30):.{digits}f} GB"


def stream_download(mp4: str, dest: str) -> int:
    """Sequential single-connection download -- the reliable baseline."""
    req = urllib.request.Request(mp4, headers={
        "User-Agent": UA, "Referer": SITE or "",
        "Accept": "video/*,*/*;q=0.8", "Range": "bytes=0-"})
    with urllib.request.urlopen(req, timeout=IDLE_TIMEOUT) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 17)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                pct = got * 100 // max(total, 1)
                sys.stdout.write(f"\r  {got/1e6:7.1f}/{total/1e6:7.1f} MB ({pct:3d}%)")
                sys.stdout.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()
    if total and got < total:
        raise RuntimeError(f"short read: {got}/{total} bytes")
    return got


PARALLEL_MIN = 3 * 1024 * 1024   # below this, single stream (parallel not worth it)
WORKERS = int(os.environ.get("WPVIDL_JOBS") or 16)
IDLE_TIMEOUT = 90                 # drop a socket idle this long -> resumable retry
SEG_ATTEMPTS = 15                 # retries per segment (resume makes them cheap)
BACKOFF = (2, 4, 8, 15, 30, 60, 60, 60)  # seconds between retries, capped at 30


def stream_download_to(mp4: str, dest: str, start: int, end: int) -> int:
    """Fetch one byte range, resuming from however much is already on disk.

    A slow/flaky link is recovered by re-opening `Range: bytes=<next>-<end>`
    and appending, so a stalled socket never loses what already arrived."""
    want = end - start + 1
    for i in range(SEG_ATTEMPTS):
        got = os.path.getsize(dest) if os.path.exists(dest) else 0
        if got >= want:
            return want                       # already complete (resume)
        req = urllib.request.Request(mp4, headers={
            "User-Agent": UA, "Referer": SITE or "",
            "Accept": "video/*,*/*;q=0.8",
            "Range": f"bytes={start + got}-{end}"})
        try:
            with urllib.request.urlopen(req, timeout=IDLE_TIMEOUT) as r, \
                    open(dest, "ab") as f:
                while got < want:
                    chunk = r.read(1 << 17)
                    if not chunk:
                        raise RuntimeError("server closed the segment early")
                    f.write(chunk)
                    got += len(chunk)
            return want
        except Exception:
            pass                              # stalled/errored socket -> retry
        if i < len(BACKOFF):
            time.sleep(min(BACKOFF[i], 30))
    raise RuntimeError(f"segment {start}-{end}: gave up after {SEG_ATTEMPTS} attempts")


def _sequential_fallback(mp4: str, dest: str) -> int:
    """Whole-file sequential retry, for links where parallel just doesn't work."""
    for i in range(SEG_ATTEMPTS):
        try:
            return stream_download(mp4, dest)
        except Exception:
            try:
                os.remove(dest)
            except OSError:
                pass
            if i < len(BACKOFF):
                time.sleep(min(BACKOFF[i], 30))
    raise RuntimeError("sequential download failed")


def _segment_ranges(total: int, count: int) -> list[tuple[int, int]]:
    count = max(1, min(count, total))
    seg = max(total // count, 1)
    out, start = [], 0
    while start < total:
        end = min(start + seg - 1, total - 1)
        out.append((start, end))
        start = end + 1
    return out


def parallel_download(mp4: str, dest: str, total: int) -> int:
    """Download one file. Small files streamed on one connection; larger files
    are split into ranged segments fetched in parallel (the takcdn CDN
    throttles per connection). Segments are written to `dest.pN` and assembled
    atomically on success, so an interrupted run can resume complete parts."""
    import threading as th

    if not total or total <= PARALLEL_MIN:
        return stream_download(mp4, dest)

    parts = _segment_ranges(total, WORKERS)
    part_files = [f"{dest}.p{i}" for i in range(len(parts))]

    # reuse any part that was fully fetched before the previous run was cut short
    for (s, e), pf in zip(parts, part_files):
        if os.path.exists(pf) and os.path.getsize(pf) == e - s + 1:
            pass  # already complete -- keep it
        else:
            try:
                os.remove(pf)  # stale/incomplete part -> refetch
            except OSError:
                pass

    monitor = {"stop": th.Event(), "done": 0, "total": total}
    out_lock = th.Lock()

    def tick():
        while not monitor["stop"].is_set():
            time.sleep(0.5)
            got = monitor["done"]
            pct = got * 100 // max(monitor["total"], 1)
            with out_lock:
                sys.stdout.write(
                    f"\r  {got/1e6:7.1f}/{monitor['total']/1e6:7.1f} MB ({pct:3d}%)  ")
                sys.stdout.flush()

    def worker(args):
        (s, e), pf = args
        want = e - s + 1
        if os.path.exists(pf) and os.path.getsize(pf) == want:
            with out_lock:
                monitor["done"] += want
            return
        stream_download_to(mp4, pf, s, e)
        with out_lock:
            monitor["done"] += want

    th.Thread(target=tick, daemon=True).start()
    workers = [th.Thread(target=worker, args=((rng, pf),))
               for rng, pf in zip(parts, part_files)]
    try:
        [w.start() for w in workers]
        [w.join() for w in workers]
    finally:
        monitor["stop"].set()
    sys.stdout.write("\r"); sys.stdout.flush()

    # any segment that still didn't land? then this link is too flaky for
    # parallel -- fall back to the always-reliable sequential whole-file path
    # so the download still completes instead of failing outright.
    if not all(os.path.exists(pf) and
               os.path.getsize(pf) == e - s + 1
               for (s, e), pf in zip(parts, part_files)):
        for pf in part_files:
            try:
                os.remove(pf)
            except OSError:
                pass
        print("  (parallel unreliable here - switching to sequential)")
        return _sequential_fallback(mp4, dest)

    tmp = dest + ".tmp"
    with open(tmp, "wb") as out:
        for pf in part_files:
            with open(pf, "rb") as f:
                import shutil
                shutil.copyfileobj(f, out, 1 << 20)
    os.replace(tmp, dest)          # atomic: dest only ever = a complete file
    for pf in part_files:
        try:
            os.remove(pf)
        except OSError:
            pass
    return total


def run_downloads(plans, outdir: str, yes: bool) -> int:
    os.makedirs(outdir, exist_ok=True)
    done = 0
    for i, p in enumerate(plans, 1):
        dest = os.path.join(outdir, p["dest"])
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            print(f"SKIP (exists): {p['title'] or os.path.basename(dest)}")
            done += 1
            continue
        print(f"\n[{i}/{len(plans)}] {p['title'] or os.path.basename(dest)} "
              f"({human_gb(p['size']) if p['size'] else 'size?'})")
        try:
            got = parallel_download(p["mp4"], dest, p["size"] or 0)
            print(f"  saved {got/1e6:.1f} MB")
            done += 1
        except Exception as e:  # noqa
            # tidy any leftover temp/part files so they don't confuse later runs
            base = os.path.basename(dest)
            for f in os.listdir(outdir):
                if f.startswith(base + "."):
                    try:
                        os.remove(os.path.join(outdir, f))
                    except OSError:
                        pass
            print(f"  FAILED: {e}")
    return done


# ---------------- the month flow ----------------

def parse_ym(source: list[str]) -> tuple[int, int]:
    try:
        if len(source) >= 2:
            return int(source[0]), int(source[1])
        a = re.split(r"[-/]", source[0])
        if len(a) < 2:
            raise ValueError
        return int(a[0]), int(a[1])
    except (ValueError, IndexError):
        return 0, 0


def month_flow(year: int, month: int, outdir: str, yes: bool) -> int:
    if not (year >= 2000 and 1 <= month <= 12):
        print("invalid month/year.")
        return 1
    label = f"{year}-{month:02d}"
    month_dir = os.path.join(outdir, label)   # e.g. downloads/2026-09/
    os.makedirs(month_dir, exist_ok=True)
    posts = wp_posts_for_month(year, month)
    print(f"{len(posts)} posts published in {label} on {SITE}.")
    if not posts:
        print("Nothing to download for that month.")
        return 0

    resolved, total, plans, seen = 0, 0, [], {}
    for i, (title, link, _d) in enumerate(posts, 1):
        sys.stdout.write(f"\r  surveying {i}/{len(posts)}..."); sys.stdout.flush()
        mp4 = resolve_post_url(link)
        if not mp4:
            continue
        sz = probe_size(mp4)
        resolved += 1
        total += sz
        plans.append({"title": title, "link": link, "mp4": mp4,
                      "dest": title_filename(title, seen), "size": sz})
    print("\n")
    print(f"  posts with a playable video  : {resolved}/{len(posts)}")
    print(f"  estimated total download size: {human_gb(total)}")
    if resolved:
        print(f"  average video size           : {human_gb(total // resolved, 2)}")
    if not plans:
        print("No resolvable videos — nothing to download.")
        return 1

    if not yes:
        sys.stdout.write(f"\nDownload {len(plans)} video(s) ({human_gb(total)}) "
                         f"into {month_dir}? [y/N] ")
        sys.stdout.flush()
        if input().strip().lower() not in ("y", "yes"):
            print("Aborted by user.")
            return 0

    done = run_downloads(plans, month_dir, yes)
    print(f"\nFinished: {done}/{len(plans)} downloaded into {month_dir}. "
          f"Typical: list months with `{sys.argv[0].split('/')[-1]} list`.")
    return 0


def survey_only(year: int, month: int) -> int:
    label = f"{year}-{month:02d}"
    posts = wp_posts_for_month(year, month)
    print(f"{len(posts)} posts published in {label} on {SITE}.")
    if not posts:
        return 0
    resolved, total = 0, 0
    for i, (title, link, _d) in enumerate(posts, 1):
        sys.stdout.write(f"\r  surveying {i}/{len(posts)}..."); sys.stdout.flush()
        mp4 = resolve_post_url(link)
        if not mp4:
            continue
        total += probe_size(mp4)
        resolved += 1
    print("\n")
    print(f"  posts with a playable video  : {resolved}/{len(posts)}")
    print(f"  estimated total download size: {human_gb(total)}")
    return 0


def detect_api() -> None:
    """One lightweight sanity check that the target is a resolvable WP site."""
    try:
        data, h = http(API_URL + "?per_page=1",
                       {"User-Agent": UA, "Accept": "application/json"})
        json.loads(data)
        tot = h.get("X-WP-Total") or "?"
        print(f"[ok] WordPress REST API detected on {SITE} "
              f"(~{tot} total posts per the API).")
    except Exception as e:
        print(f"[warn] could not confirm the WP REST API on {SITE}: {e}")
        print("       continuing anyway — check that the site runs WordPress "
              "with /wp-json enabled if downloads come back empty.")


# ---------------- cli ----------------

def _add_common(p, with_yes: bool = False, with_out: bool = True):
    p.add_argument("--site", "-s", default=None,
                   help="target website URL (also: $WPVIDL_SITE, or the "
                        "interactive prompt on `month`)")
    p.add_argument("--log", "--single-log", dest="single_log", default=None,
                   help="log to this exact file; default: timestamped file in "
                        "$WPVIDL_LOG_DIR")
    p.add_argument("--ua", default=None, help="override the User-Agent")
    p.add_argument("-j", "--jobs", default=None,
                   help="parallel connections per file (default: $WPVIDL_JOBS or 16)")
    if with_out:
        p.add_argument("--out", default=os.environ.get("WPVIDL_OUT")
                       or os.path.join(os.path.expanduser("~"),
                                       "wp-video-dl", "downloads"))
    if with_yes:
        p.add_argument("-y", "--yes", action="store_true")


def build_parser():
    ap = argparse.ArgumentParser(
        prog="wp-video-dl",
        description="Download videos from a WordPress-hosted site, month by "
                    "month (surveys size first, then asks). Pure stdlib.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def mk(name, add_args=None):
        p = sub.add_parser(name)
        if add_args:
            add_args(p)
        return p

    mk("month", lambda p: _add_common(p, with_yes=True))
    mk("list", lambda p: _add_common(p, with_out=False))
    mk("info", lambda p: _add_common(p, with_out=False))
    mk("dl", lambda p: _add_common(p, with_yes=True))
    mk("survey", lambda p: _add_common(p, with_out=False))
    mk("dl-page", lambda p: _add_common(p, with_yes=True))
    dm = mk("dl-month", lambda p: _add_common(p, with_yes=True))
    dm.add_argument("ym", nargs="+", help="YEAR MONTH or 'YYYY-MM'")

    p = sub._name_parser_map["list"]; p.add_argument("page", nargs="?", default="1")
    p = sub._name_parser_map["info"]; p.add_argument("url")
    p = sub._name_parser_map["dl"];   p.add_argument("url")
    p = sub._name_parser_map["survey"]; p.add_argument("ym", nargs="+")
    p = sub._name_parser_map["dl-page"].add_argument("page", nargs="?", default="1")
    return ap


def main(argv) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    enable_log(args.single_log or default_log())

    if getattr(args, "ua", None):
        global UA
        UA = args.ua

    jobs = getattr(args, "jobs", None)
    if jobs:
        global WORKERS
        WORKERS = max(1, int(jobs))

    # --- resolve the target site -------------------------------------------------
    url_arg = None
    if getattr(args, "cmd", None) in ("info", "dl"):
        url_arg = args.url
    elif getattr(args, "cmd", None) in ("list", "dl-page") \
            and str(args.page).startswith("http"):
        url_arg = args.page

    interactive = (args.cmd == "month")
    site_given = args.site is not None or os.environ.get("WPVIDL_SITE")
    if args.site:
        set_site(args.site)
    elif site_given:
        set_site(os.environ["WPVIDL_SITE"])
    elif url_arg:
        d = derive_site_from_url(url_arg)
        if d:
            set_site(d)
        else:
            print("could not derive the target site from that URL; "
                  "pass --site URL.")
            return 2
    elif interactive:
        if pick_site(args, allow_prompt=True) is None:
            print("no target site provided.")
            return 2
    else:
        print("no target site. Pass --site URL (or set $WPVIDL_SITE, or use "
              "the interactive `month` command).")
        return 2

    if interactive:
        detect_api()

    # --- dispatch ---------------------------------------------------------------
    if args.cmd == "list":
        posts = list_posts(args.page)
        print(f"{len(posts)} video posts on {args.page if '://' in str(args.page) else SITE + '/' + str(args.page).lstrip('/')}\n")
        for i, p in enumerate(posts, 1):
            print(f"{i:3d}. {os.path.basename(p.rstrip('/'))}\n     {p}")
        return 0

    if args.cmd == "info":
        mp4 = resolve_post_url(args.url)
        if not mp4:
            print("No resolvable .mp4 found on that page.")
            return 1
        print(f"title file : {filename_for(mp4)}")
        print(f"source URL : {mp4}")
        return 0

    if args.cmd == "dl":
        mp4 = resolve_post_url(args.url)
        if not mp4:
            print("No resolvable .mp4 found on that page.")
            return 1
        title = page_title(args.url)
        run_downloads([{"title": title, "link": args.url, "mp4": mp4,
                        "dest": title_filename(title),
                        "size": probe_size(mp4)}],
                      args.out, True)
        return 0

    if args.cmd == "dl-page":
        posts = list_posts(args.page)
        plans, seen = [], {}
        for i, p in enumerate(posts, 1):
            sys.stdout.write(f"resolving {i}/{len(posts)}...\r"); sys.stdout.flush()
            mp4 = resolve_post_url(p)
            if mp4:
                t = page_title(p)
                plans.append({"title": t, "link": p, "mp4": mp4,
                              "dest": title_filename(t, seen),
                              "size": probe_size(mp4)})
        print()
        total = sum(x["size"] for x in plans)
        print(f"{len(plans)}/{len(posts)} resolved, ~{human_gb(total)}")
        if not plans:
            return 1
        if not args.yes and input(f"Download all {len(plans)}? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 0
        run_downloads(plans, args.out, True)
        return 0

    if args.cmd == "month":
        while True:
            sys.stdout.write("Enter month (YYYY-MM, e.g. 2026-08): ")
            sys.stdout.flush()
            raw = input().strip()
            y, m = parse_ym([raw] if raw else ["0"])
            if y >= 2000 and 1 <= m <= 12:
                break
            print("Could not parse that. Try '2026-08' or '2026 8'.")
        return month_flow(y, m, args.out, args.yes)

    year, month = parse_ym(args.ym)
    if not (year >= 2000 and 1 <= month <= 12):
        print("dl-month needs YEAR MONTH (e.g. '2026 8') or 'YYYY-MM'.")
        return 2
    if args.cmd == "survey":
        return survey_only(year, month)
    return month_flow(year, month, args.out, args.yes)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
