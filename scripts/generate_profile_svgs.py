from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


# ---------- Models ----------

@dataclass(frozen=True)
class RepoInfo:
    name: str
    stargazers_count: int
    languages_url: str | None
    default_branch: str | None


@dataclass(frozen=True)
class UserStats:
    login: str
    public_repos: int
    followers: int
    stars_total: int
    top_langs: list[tuple[str, int]]  # (language, bytes)


@dataclass(frozen=True)
class ActivityStats:
    days: list[tuple[str, int]]  # (YYYY-MM-DD, commits)
    prs_30d: int
    issues_30d: int


# ---------- HTTP ----------

def _http_json(url: str, token: str) -> Any:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# ---------- Data fetching ----------

def fetch_user(user: str, token: str) -> dict[str, Any]:
    return _http_json(f"https://api.github.com/users/{user}", token)


def fetch_all_repos(user: str, token: str) -> list[RepoInfo]:
    repos: list[RepoInfo] = []
    page = 1
    while True:
        batch = _http_json(
            f"https://api.github.com/users/{user}/repos"
            f"?per_page=100&page={page}&type=owner&sort=pushed",
            token,
        )
        if not batch:
            break
        for r in batch:
            repos.append(
                RepoInfo(
                    name=str(r.get("name", "")),
                    stargazers_count=int(r.get("stargazers_count", 0)),
                    languages_url=r.get("languages_url"),
                    default_branch=r.get("default_branch"),
                )
            )
        if len(batch) < 100:
            break
        page += 1
    return repos


def compute_top_languages(repos: list[RepoInfo], token: str, limit: int = 10) -> list[tuple[str, int]]:
    lang_bytes: dict[str, int] = {}
    for r in repos:
        if not r.languages_url:
            continue
        try:
            langs = _http_json(r.languages_url, token)
        except Exception:
            continue
        for lang, b in langs.items():
            if isinstance(b, int) and b > 0:
                lang_bytes[lang] = lang_bytes.get(lang, 0) + b

    return sorted(lang_bytes.items(), key=lambda x: x[1], reverse=True)[:limit]


def fetch_activity_30d(user: str, token: str) -> ActivityStats:
    """
    Robust, API-only:
    - Commits per day via search/commits (works best for own commits)
    - PRs/Issues count via search/issues
    """
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=30)).date()
    days = [(str(start + timedelta(days=i)), 0) for i in range(31)]
    day_index = {d: i for i, (d, _) in enumerate(days)}

    # Commits search (note: limited but ok for trend card)
    # endpoint: /search/commits requires special Accept header historically,
    # but modern API often works with vnd.github+json; if it fails, we fall back gracefully.
    for d, _ in list(days):
        try:
            q = f"author:{user} committer-date:{d}"
            url = f"https://api.github.com/search/commits?q={urllib.parse.quote(q)}&per_page=1"
            req = urllib.request.Request(url)
            req.add_header("Accept", "application/vnd.github+json")
            req.add_header("Authorization", f"Bearer {token}")
            req.add_header("X-GitHub-Api-Version", "2022-11-28")
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            total = int(payload.get("total_count", 0))
            days[day_index[d]] = (d, total)
        except Exception:
            # if search/commits fails, keep zeros (card still builds)
            pass

    # PRs & Issues in last 30 days
    since = start.isoformat()
    prs_30d = 0
    issues_30d = 0

    try:
        prs = _http_json(
            f"https://api.github.com/search/issues?q=author:{user}+type:pr+created:>={since}",
            token,
        )
        prs_30d = int(prs.get("total_count", 0))
    except Exception:
        pass

    try:
        issues = _http_json(
            f"https://api.github.com/search/issues?q=author:{user}+type:issue+created:>={since}",
            token,
        )
        issues_30d = int(issues.get("total_count", 0))
    except Exception:
        pass

    return ActivityStats(days=days, prs_30d=prs_30d, issues_30d=issues_30d)


def fetch_stats(user: str, token: str) -> tuple[UserStats, ActivityStats]:
    u = fetch_user(user, token)
    repos = fetch_all_repos(user, token)

    stars_total = sum(r.stargazers_count for r in repos)
    top_langs = compute_top_languages(repos, token, limit=10)

    stats = UserStats(
        login=str(u["login"]),
        public_repos=int(u.get("public_repos", 0)),
        followers=int(u.get("followers", 0)),
        stars_total=int(stars_total),
        top_langs=top_langs,
    )

    activity = fetch_activity_30d(user, token)
    return stats, activity


# ---------- SVG styling helpers ----------

PALETTE = [
    "#22c55e",  # green
    "#3b82f6",  # blue
    "#a855f7",  # purple
    "#f97316",  # orange
    "#eab308",  # yellow
    "#ef4444",  # red
    "#14b8a6",  # teal
    "#f43f5e",  # pink
    "#60a5fa",  # light blue
    "#34d399",  # mint
]


def card_frame(width: int, height: int, title: str, subtitle: str | None = None) -> str:
    sub = ""
    if subtitle:
        sub = f'<text x="24" y="58" class="sub">{_escape(subtitle)}</text>'
    return f"""
  <rect x="10" y="10" width="{width-20}" height="{height-20}" rx="18" class="card"/>
  <text x="24" y="40" class="title">{_escape(title)}</text>
  {sub}
"""


def common_defs() -> str:
    return """
  <defs>
    <style>
      .card { fill:#0d1117; stroke:#30363d; stroke-width:1; }
      .title { font:700 18px system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,Arial; fill:#e6edf3; }
      .sub   { font:500 12px system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,Arial; fill:#8b949e; }
      .txt   { font:600 14px system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,Arial; fill:#c9d1d9; }
      .muted { font:500 12px system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,Arial; fill:#8b949e; }
      .num   { font:800 20px system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,Arial; fill:#e6edf3; }
      .pill  { fill:#161b22; stroke:#30363d; stroke-width:1; }
      .barbg { fill:#161b22; }
      .shadow { filter: drop-shadow(0px 2px 8px rgba(0,0,0,0.35)); }
    </style>
  </defs>
"""


def render_stats_card(stats: UserStats) -> str:
    width, height = 420, 220
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # colorful accent line
    accent = f'<rect x="24" y="76" width="{width-48}" height="6" rx="3" fill="{PALETTE[1]}"/>'

    # three metric pills
    def pill(x: int, label: str, value: int, color: str) -> str:
        return f"""
  <g class="shadow">
    <rect x="{x}" y="98" width="118" height="92" rx="14" class="pill"/>
    <circle cx="{x+18}" cy="122" r="6" fill="{color}"/>
    <text x="{x+32}" y="126" class="muted">{_escape(label)}</text>
    <text x="{x+18}" y="162" class="num">{value}</text>
  </g>
"""

    body = (
            pill(24, "Public repos", stats.public_repos, PALETTE[0])
            + pill(150, "Stars", stats.stars_total, PALETTE[3])
            + pill(276, "Followers", stats.followers, PALETTE[2])
    )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="GitHub stats for {stats.login}">
{common_defs()}
{card_frame(width, height, f"Stats · {stats.login}", "Generated locally via GitHub Actions")}
{accent}
{body}
<text x="24" y="{height-24}" class="muted">Updated: {now}</text>
</svg>
"""
    return svg


def render_languages_card(stats: UserStats) -> str:
    width, height = 880, 320
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    langs = stats.top_langs[:8]  # nice fit
    total = sum(b for _, b in langs) or 1

    # table header
    header = """
  <text x="24" y="92" class="muted">Language</text>
  <text x="280" y="92" class="muted">Share</text>
  <text x="360" y="92" class="muted">Usage</text>
  <rect x="24" y="104" width="832" height="1" fill="#30363d"/>
"""

    rows = []
    start_y = 132
    row_h = 26
    bar_x = 360
    bar_w = 496
    for i, (lang, b) in enumerate(langs):
        y = start_y + i * row_h
        pct = (b / total) * 100.0
        w = max(2, int(bar_w * (b / total)))
        color = PALETTE[i % len(PALETTE)]

        rows.append(
            f"""
  <circle cx="24" cy="{y-4}" r="5" fill="{color}"/>
  <text x="38" y="{y}" class="txt">{_escape(lang)}</text>
  <text x="280" y="{y}" class="txt">{pct:0.1f}%</text>
  <rect x="{bar_x}" y="{y-14}" width="{bar_w}" height="12" rx="6" class="barbg"/>
  <rect x="{bar_x}" y="{y-14}" width="{w}" height="12" rx="6" fill="{color}"/>
"""
        )

    subtitle = "Top languages by bytes (summed across repos)"
    accent = f'<rect x="24" y="66" width="{width-48}" height="5" rx="3" fill="{PALETTE[6]}"/>'

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Top languages for {stats.login}">
{common_defs()}
{card_frame(width, height, "Top Languages", subtitle)}
{accent}
{header}
{''.join(rows)}
<text x="24" y="{height-24}" class="muted">Updated: {now}</text>
</svg>
"""
    return svg


def render_activity_card(stats: UserStats, activity: ActivityStats) -> str:
    width, height = 420, 220
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # mini heat bar (31 days)
    counts = [c for _, c in activity.days]
    mx = max(counts) if counts else 0
    mx = mx if mx > 0 else 1

    # heatbar geometry
    x0, y0 = 24, 98
    w_total, h = width - 48, 32
    n = len(activity.days)
    gap = 2
    cell_w = max(2, int((w_total - gap * (n - 1)) / n))

    cells = []
    x = x0
    for i, (_, c) in enumerate(activity.days):
        intensity = c / mx  # 0..1
        # choose color by intensity using a gradient-like pick
        # 0..1 -> palette index 0..4
        idx = min(4, int(round(intensity * 4)))
        color = [ "#1f2937", "#22c55e", "#3b82f6", "#a855f7", "#f97316" ][idx]
        cells.append(f'<rect x="{x}" y="{y0}" width="{cell_w}" height="{h}" rx="4" fill="{color}"/>')
        x += cell_w + gap

    # PR/Issue pills
    def small_pill(y: int, label: str, value: int, color: str) -> str:
        return f"""
  <g class="shadow">
    <rect x="24" y="{y}" width="{width-48}" height="44" rx="14" class="pill"/>
    <circle cx="44" cy="{y+22}" r="6" fill="{color}"/>
    <text x="60" y="{y+26}" class="txt">{_escape(label)}</text>
    <text x="{width-40}" y="{y+28}" text-anchor="end" class="num">{value}</text>
  </g>
"""

    accent = f'<rect x="24" y="66" width="{width-48}" height="5" rx="3" fill="{PALETTE[4]}"/>'

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Recent activity for {stats.login}">
{common_defs()}
{card_frame(width, height, "Activity (last 30 days)", "Commits trend + PRs/Issues created")}
{accent}
<text x="24" y="92" class="muted">Commits/day</text>
<g>{''.join(cells)}</g>
{small_pill(140, "PRs created", activity.prs_30d, PALETTE[1])}
{small_pill(190, "Issues created", activity.issues_30d, PALETTE[3])}
<text x="24" y="{height-24}" class="muted">Updated: {now}</text>
</svg>
"""
    return svg


# ---------- Main ----------

def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    user = os.environ.get("GITHUB_USER", "").strip()

    if not token:
        print("Missing GITHUB_TOKEN env var.", file=sys.stderr)
        return 2
    if not user:
        print("Missing GITHUB_USER env var.", file=sys.stderr)
        return 2

    stats, activity = fetch_stats(user, token)

    os.makedirs("assets", exist_ok=True)
    with open("assets/stats.svg", "w", encoding="utf-8") as f:
        f.write(render_stats_card(stats))
    with open("assets/languages.svg", "w", encoding="utf-8") as f:
        f.write(render_languages_card(stats))
    with open("assets/activity.svg", "w", encoding="utf-8") as f:
        f.write(render_activity_card(stats, activity))

    print("Wrote assets/stats.svg, assets/languages.svg, assets/activity.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
