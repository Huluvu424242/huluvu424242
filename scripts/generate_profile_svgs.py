from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class UserStats:
    login: str
    public_repos: int
    followers: int
    stars_total: int
    top_langs: list[tuple[str, int]]  # (lang, bytes)


def _http_json(url: str, token: str) -> Any:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_user_stats(user: str, token: str) -> UserStats:
    u = _http_json(f"https://api.github.com/users/{user}", token)

    # Repos paginiert holen (bis 100 pro Seite)
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = _http_json(
            f"https://api.github.com/users/{user}/repos?per_page=100&page={page}&type=owner&sort=pushed",
            token,
        )
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    stars_total = sum(int(r.get("stargazers_count", 0)) for r in repos)

    # Top-Languages: GitHub liefert "language" pro Repo (nur Hauptsprache),
    # plus detaillierte Bytes über /languages.
    lang_bytes: dict[str, int] = {}
    for r in repos:
        languages_url = r.get("languages_url")
        if not languages_url:
            continue
        try:
            langs = _http_json(languages_url, token)
        except Exception:
            continue
        for lang, b in langs.items():
            if isinstance(b, int):
                lang_bytes[lang] = lang_bytes.get(lang, 0) + b

    top_langs = sorted(lang_bytes.items(), key=lambda x: x[1], reverse=True)[:6]

    return UserStats(
        login=u["login"],
        public_repos=int(u.get("public_repos", 0)),
        followers=int(u.get("followers", 0)),
        stars_total=int(stars_total),
        top_langs=top_langs,
    )


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def render_stats_svg(stats: UserStats) -> str:
    # Simple "card" SVG (kein externes CSS/Fonts nötig; nutzt System-Fonts)
    width, height = 900, 240
    pad = 24

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Balken für Top-Languages
    total = sum(b for _, b in stats.top_langs) or 1
    bars = []
    bar_x = pad
    bar_y = 170
    bar_w = width - 2 * pad
    bar_h = 18
    cur = bar_x

    # Wir wählen bewusst keine knalligen Farben; neutrales Grau.
    # (Du kannst später gern anpassen.)
    for i, (lang, b) in enumerate(stats.top_langs):
        frac = b / total
        w = max(2, int(math.floor(bar_w * frac)))
        bars.append(
            f'<rect x="{cur}" y="{bar_y}" width="{w}" height="{bar_h}" rx="6" />'
        )
        cur += w

    # Labels darunter
    labels = []
    lx = pad
    ly = 215
    for lang, b in stats.top_langs:
        pct = (b / total) * 100
        labels.append(
            f'<text x="{lx}" y="{ly}" class="muted">{_escape(lang)} {pct:0.1f}%</text>'
        )
        lx += 140

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="GitHub stats for {stats.login}">
  <defs>
    <style>
      .card {{
        fill: #0d1117;
        stroke: #30363d;
        stroke-width: 1;
      }}
      .title {{
        font: 700 22px system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial;
        fill: #c9d1d9;
      }}
      .text {{
        font: 500 16px system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial;
        fill: #c9d1d9;
      }}
      .muted {{
        font: 500 13px system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial;
        fill: #8b949e;
      }}
      .bar rect {{
        fill: #30363d;
      }}
    </style>
  </defs>

  <rect x="10" y="10" width="{width-20}" height="{height-20}" rx="16" class="card"/>

  <text x="{pad}" y="52" class="title">GitHub Stats · {_escape(stats.login)}</text>

  <text x="{pad}" y="92" class="text">Public repos: {stats.public_repos}</text>
  <text x="{pad+250}" y="92" class="text">Followers: {stats.followers}</text>
  <text x="{pad+500}" y="92" class="text">Stars: {stats.stars_total}</text>

  <text x="{pad}" y="138" class="muted">Top Languages (by bytes across repos)</text>

  <g class="bar">
    {''.join(bars)}
  </g>

  {''.join(labels)}

  <text x="{pad}" y="{height-28}" class="muted">Updated: {now}</text>
</svg>
"""
    return svg


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    user = os.environ.get("GITHUB_USER", "").strip()

    if not token:
        print("Missing GITHUB_TOKEN env var.", file=sys.stderr)
        return 2
    if not user:
        print("Missing GITHUB_USER env var.", file=sys.stderr)
        return 2

    stats = fetch_user_stats(user, token)

    os.makedirs("assets", exist_ok=True)
    with open("assets/stats.svg", "w", encoding="utf-8") as f:
        f.write(render_stats_svg(stats))

    print("Wrote assets/stats.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
