# wp-video-dl

Download videos from a **WordPress-hosted video site**, month by month — it
surveys the real file sizes *without* downloading them, tells you the count and
the total gigabytes, then asks before it touches anything. Pure Python
standard library — no pip installs, runs anywhere Python 3.8+ is.

Every run is written to a timestamped log (`~/wp-video-dl/logs`)
with the full transcript and any tracebacks, so a failed run on a remote server
is easy to debug.

## What it does

1. You name the **target website** (any WordPress site with the WP REST API
   enabled — it queries `/wp-json/wp/v2/posts`).
2. You pick the **month** (`2026-08`).
3. It lists every post that month, resolves each one to its real `.mp4`
   (via a `Range` header, so it works even on CDNs that drop full GETs).
4. It probes the true file sizes (a zero-byte range request — **nothing is
   downloaded yet**).
5. It prints: **N videos · X.X GB total**, then asks
   `Download N video(s) (X.X GB)? [y/N]`.
6. On `y` it downloads, skipping anything already saved (safe to re-run).

## Quick start

```bash
python3 wp-video-dl.py month            # interactive: site -> month -> download
```

Or all non-interactively:

```bash
python3 wp-video-dl.py month --site https://example.com -y --out ./videos
python3 wp-video-dl.py survey  2026-08 --site https://example.com   # count + GB only
python3 wp-video-dl.py dl-month 2026 8 --site https://example.com   # survey + download
```

The target site can come from any of:

- `--site https://example.com` (flag on any command)
- `export WPVIDL_SITE=https://example.com`
- automatic — derived from the URL you pass to `info` / `dl` / `list`
- `wp-video-dl month` just **asks** you

## Commands

| command | what it does |
|---|---|
| `month` | interactive: target site → month → survey → confirm → download |
| `survey YEAR MONTH` | show post count + total GB, download nothing |
| `dl-month YEAR MONTH` | survey then download (skips existing) |
| `list [PAGE]` | list post links on a listing page |
| `info <POST_URL>` | show the resolved `.mp4` for one post |
| `dl <POST_URL>` | download one post |
| `dl-page [PAGE]` | download every resolvable video on a listing page |

`YEAR MONTH` forms: `2026 8` · `2026-08` · `2026/8`

### Common flags

`--site URL`, `--out DIR`, `--log FILE`, `--ua STR`, `-y/--yes`

### Environment

- `WPVIDL_SITE` — default target website
- `WPVIDL_OUT` — default download folder (`~/wp-video-dl/downloads`)
- `WPVIDL_LOG_DIR` — where per-run logs go (`~/wp-video-dl/logs`)

## Deploy on a fresh Ubuntu server

**One command test run** (fetches from the GitHub repo, then runs the
interactive flow — it asks the target website, the month, shows count + GB,
then confirms the download):

```bash
cd ~ && curl -fsSL https://raw.githubusercontent.com/teelge/wp-video-dl/main/install.sh -o install.sh \
  && WPVIDL_SRC_URL=https://raw.githubusercontent.com/teelge/wp-video-dl/main/wp-video-dl.py bash install.sh
```

It installs the `wp-video-dl` command under `~/wp-video-dl` with `logs/` and
`downloads/`, then launches `wp-video-dl month` right there.

Or copy the two files and run the installer directly:

```bash
scp install.sh wp-video-dl.py ubuntu@SERVER:~/
ssh ubuntu@SERVER
bash install.sh
```

You can also pre-answer parts non-interactively instead of the prompts:

```bash
WPVIDL_SITE=https://example.com wp-video-dl survey 2026-08   # count + GB only
wp-video-dl dl-month 2026 8 --site https://example.com -y     # download straight away
```

## Requirements & notes

- WordPress site **with the WordPress REST API enabled** (default on
  self-hosted WP). The tool prints a warning if it can't confirm `/wp-json`.
- Python 3.8+ (`python3` on any modern Ubuntu).
- Downloads use `Range: bytes=0-` + a browser `User-Agent` + `Referer`, which
  is required by some CDNs that otherwise drop full requests.
- **You decide what to download.** This is a generic retrieval tool — only
  download content you are entitled to.

## Debugging

Each run appends to a timestamped log:

```bash
ls ~/wp-video-dl/logs/       # latest wp_video_dl_*.log
tail -f ~/wp-video-dl/logs/wp_video_dl_LATEST.log
```

Set `WPVIDL_LOG_DIR` to redirect logs, or `--log /path/file.log` for one exact
file.

The only tuning knobs if a site behaves differently: the regexes in
`resolve_mp4` (how the `.mp4` is found on a post page) and the CDN/range
behaviour in `stream_download` / `probe_size`.