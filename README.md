# wp-video-dl

Download videos from a **WordPress-hosted video site**, month by month — it
surveys the real file sizes *without* downloading them, tells you the count and
the total gigabytes, then asks before it touches anything. Pure Python
standard library — no pip installs, runs anywhere Python 3.8+ is.

Every run is written to a timestamped log (`~/wp-video-dl/logs`)
with the full transcript and any tracebacks, so a failed run on a remote server
is easy to debug.

## What it does

Run `wp-video-dl` with **no command** and it walks you through everything:

1. You name the **target website** (any WordPress site with the WP REST API
   enabled — it queries `/wp-json/wp/v2/posts`). Pasting a full post URL works
   too; the domain is extracted automatically.
2. You pick what to do:
   - **1) Download videos by month** — every post that month, resolved to its
     real `.mp4` (via a `Range` header, so it works even on CDNs that drop
     full GETs), surveyed, confirmed, downloaded.
   - **2) Search videos by keyword** — finds every post matching the keyword
     (any language), lists them numbered with sizes, then you choose: specific
     numbers (`1,3-5`), `a` for all, or Enter to cancel.
   - **3) Download one direct `.mp4` link** — no post page needed.
3. It probes the true file sizes first (a zero-byte range request — **nothing
   is downloaded yet**), prints **N videos · X.X GB total**, and asks before
   touching anything.
4. Downloads skip anything already saved (safe to re-run).

From any prompt, `m` returns to the main menu and `q` quits.

## Quick start

```bash
python3 wp-video-dl.py            # interactive menu: site -> month / search / direct link
python3 wp-video-dl.py month      # interactive: site -> month -> download
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
- automatic — derived from the URL you pass to `info` / `dl` / `dl-url` / `list`
- `wp-video-dl` / `wp-video-dl month` just **asks** you

## Commands

| command | what it does |
|---|---|
| *(no command)* | interactive menu: site → month / keyword search / direct .mp4 link |
| `month` | interactive: target site → month → survey → confirm → download |
| `survey YEAR MONTH` | show post count + total GB, download nothing |
| `dl-month YEAR MONTH` | survey then download (skips existing) |
| `search KEYWORD` | find videos matching a keyword, pick which to download (numbers/ranges/`a`) |
| `dl-url <MP4_URL>` | download a direct .mp4 link |
| `list [PAGE]` | list post links on a listing page |
| `info <POST_URL>` | show the resolved `.mp4` for one post |
| `dl <POST_URL>` | download one post |
| `dl-page [PAGE]` | download every resolvable video on a listing page |

`YEAR MONTH` forms: `2026 8` · `2026-08` · `2026/8` · `08/2026` · `aug 2026` ·
`August 2026` · a bare month (`09`) or year (`2026`) is also accepted.

### Common flags

`--site URL`, `--out DIR`, `--log FILE`, `--ua STR`, `-y/--yes`

### Environment

- `WPVIDL_SITE` — default target website
- `WPVIDL_OUT` — default download folder (`~/wp-video-dl/downloads`)
- `WPVIDL_LOG_DIR` — where per-run logs go (`~/wp-video-dl/logs`)

## Deploy on a fresh Ubuntu server

**One command test run** (fetches from the GitHub repo, then starts the
interactive menu — it asks the target website, then whether to download by
month, search by keyword, or grab one direct link, and confirms the download):

```bash
cd ~ && curl -fsSL https://raw.githubusercontent.com/teelge/wp-video-dl/main/install.sh -o install.sh \
  && WPVIDL_SRC_URL=https://raw.githubusercontent.com/teelge/wp-video-dl/main/wp-video-dl.py bash install.sh
```

It installs the `wp-video-dl` command under `~/wp-video-dl` with `logs/` and
`downloads/`, then launches the interactive menu right there.

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

Search results are picked from a numbered list (e.g. `1,3-5`, or `a` for all):

```bash
wp-video-dl search "keyword here" --site https://example.com            # pick which to download
wp-video-dl search "keyword here" --site https://example.com -y         # download all matches
wp-video-dl dl-url https://cdn.example.com/videos/foo.mp4               # one direct .mp4 link
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
