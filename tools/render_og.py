#!/usr/bin/env python3
"""Render social previews with headless Chrome: site/og/<city>.jpg (1200×630), its thumbnail og/thumb-<city>.jpg
for the home page cards, and og/home.jpg for the home page.

Usage: python3 tools/render_og.py <city>|home|classements|all   (run build_pages.py before, and again after for "home")
Needs Google Chrome and ImageMagick (`magick`).
"""

from __future__ import annotations

import functools
import http.server
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cities import load_cities  # noqa: E402
SITE = ROOT / "site"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

OVERLAY = """<style>
  html, body { width: 1200px; height: 630px; overflow: hidden; margin: 0; }
  .topbar, .hero, .breadcrumb, .controls, .reach, .section, .site-footer, .map-buttons, .map-bus, .segmented, .link-button { display: none !important; }
  .page { max-width: none; padding: 0; }
  .map-card { border: 0; border-radius: 0; }
  .map-stage { height: 630px !important; min-height: 0; }
  .trip-panel { top: auto; bottom: 24px; left: 24px; width: 330px; }
  .legend { left: auto; right: 24px; bottom: 24px; }
  .og-title { position: absolute; top: 24px; left: 24px; z-index: 5; padding: 18px 22px; border: 1px solid #e6e6e6;
    border-radius: 16px; background: rgba(255,255,255,0.96); box-shadow: 0 6px 24px rgba(0,0,0,.08); }
  .og-title h1 { margin: 0; font-size: 42px; }
  .og-title p { margin: 6px 0 0; color: #4b4b4b; font-size: 19px; font-weight: 500; }
  .og-credit { position: absolute; right: 24px; top: 14px; z-index: 5; color: #6b6b6b; font: 500 11px Inter, sans-serif; }
</style>
</head>"""


HOME = """<!doctype html><html lang="fr"><head><meta charset="utf-8" />
<link rel="stylesheet" href="https://fonts.bunny.net/css?family=inter:500,800" />
<style>
  body { width: 1200px; height: 630px; margin: 0; overflow: hidden; font-family: Inter, sans-serif; background: #fff; }
  header { position: absolute; top: 34px; left: 44px; right: 44px; }
  h1 { margin: 0; font-size: 60px; font-weight: 800; letter-spacing: -0.035em; color: #111; }
  p { margin: 6px 0 0; font-size: 24px; font-weight: 500; color: #4b4b4b; }
  .grid { position: absolute; left: 44px; right: 44px; bottom: 34px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
  figure { margin: 0; position: relative; border-radius: 14px; overflow: hidden; border: 1px solid #e6e6e6; }
  img { display: block; width: 100%; height: 190px; object-fit: cover; object-position: center 70%; }
  figcaption { position: absolute; left: 10px; bottom: 10px; padding: 4px 10px; border-radius: 8px; background: #fff; font-weight: 800; font-size: 20px; }
</style></head><body>
<header><h1>À portée de tram</h1><p>Les grandes villes redessinées par le temps de trajet en tram et en métro</p></header>
<div class="grid">FIGURES</div></body></html>"""


def screenshot(url: str, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "og.png"
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
             "--window-size=1200,630", "--virtual-time-budget=10000", f"--screenshot={png}", url],
            check=True, capture_output=True,
        )
        out.parent.mkdir(exist_ok=True)
        subprocess.run(["magick", str(png), "-strip", "-quality", "88", str(out)], check=True)
    print(f"Wrote {out.relative_to(ROOT)}")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def serve():
    handler = functools.partial(QuietHandler, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def render_home(cities: list[dict]) -> None:
    # Six slots, five cities so far: the home card shows the first ones by order.
    figures = "".join(
        f'<figure><img src="og/thumb-{city["slug"]}.jpg" /><figcaption>{city["name"]}</figcaption></figure>' for city in cities[:6]
    )
    preview = SITE / "_og_home.html"
    preview.write_text(HOME.replace("FIGURES", figures), encoding="utf-8")
    server = serve()
    try:
        screenshot(f"http://127.0.0.1:{server.server_port}/_og_home.html", SITE / "og" / "home.jpg")
    finally:
        server.shutdown()
        preview.unlink()


RANKINGS_OVERLAY = """<style>
  html, body { width: 1200px; height: 630px; overflow: hidden; margin: 0; }
  .topbar, .breadcrumb, .section, .site-footer, .lede, .ranking-cards { display: none !important; }
  .podium { width: 100%; margin-top: 34px; }
  .podium-step strong { font-size: 2.6rem; }
  .podium-step span:not(.medal) { font-size: 1.15rem; }
  .page { max-width: 1120px; padding: 34px 40px 0; }
  .hero { padding: 0; }
  .hero h1 { font-size: 64px; margin: 18px 0 8px; }
  .ranking-highlights { margin-top: 30px; gap: 16px; }
  .ranking-highlights .stat { padding: 22px; }
  .ranking-highlights strong { font-size: 2.6rem; }
  .ranking-highlights span { font-size: 1.05rem; }
  body::after { content: "À portée de tram · tram.camilleroux.com · d'après les horaires officiels des réseaux";
    position: absolute; left: 0; right: 0; bottom: 44px; text-align: center; color: #3aa70b; font: 600 20px Inter, sans-serif; }
</style>
</head>"""


def render_rankings() -> None:
    """The rankings hub (og/classements.jpg) and every ranking page (og/classement-<slug>.jpg)."""
    pages = [(SITE / "classements", "classements.jpg")] + [
        (path.parent, f"classement-{path.parent.name}.jpg") for path in sorted((SITE / "classements").glob("*/index.html"))
    ]
    server = serve()
    try:
        for folder, image in pages:
            page = (folder / "index.html").read_text(encoding="utf-8").replace("</head>", RANKINGS_OVERLAY, 1)
            preview = folder / "_og.html"
            preview.write_text(page, encoding="utf-8")
            try:
                relative = folder.relative_to(SITE).as_posix()
                screenshot(f"http://127.0.0.1:{server.server_port}/{relative}/_og.html", SITE / "og" / image)
            finally:
                preview.unlink()
    finally:
        server.shutdown()


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cities = load_cities()
    targets = [c["slug"] for c in cities] + ["home", "classements"] if sys.argv[1] == "all" else [sys.argv[1]]
    for target in targets:
        if target == "home":
            render_home(cities)
        elif target == "classements":
            render_rankings()
        else:
            render_city(next(city for city in cities if city["slug"] == target))


def render_city(city: dict) -> None:
    page = (SITE / city["path"] / "index.html").read_text(encoding="utf-8")
    title = f'<div class="og-title"><h1>{city["title"]}</h1><p>La ville redessinée par le temps de trajet, depuis où vous voulez</p></div>'
    credit = '<div class="og-credit">© contributeurs OpenStreetMap · horaires ' + city["network"] + '</div>'
    page = page.replace("</head>", OVERLAY, 1).replace('<canvas id="mapCanvas"></canvas>', '<canvas id="mapCanvas"></canvas>\n' + title + credit, 1)
    preview = SITE / city["path"] / "_og.html"
    preview.write_text(page, encoding="utf-8")

    server = serve()
    try:
        # Trajet de l'aperçu : celui de la config, sinon une station à ~20 min du centre (calculée par build_data.py).
        sources = json.loads((ROOT / "sources" / f"{city['slug']}.json").read_text(encoding="utf-8"))
        trip = city.get("ogTrip") or sources["stats"]["ogTrip"]
        out = SITE / "og" / f"{city['slug']}.jpg"
        screenshot(f"http://127.0.0.1:{server.server_port}/{city['path']}_og.html?to={trip['lat']},{trip['lon']}", out)
        thumb = SITE / "og" / f"thumb-{city['slug']}.jpg"
        subprocess.run(["magick", str(out), "-resize", "600x315", "-strip", "-quality", "82", str(thumb)], check=True)
        print(f"Wrote {thumb.relative_to(ROOT)}")
    finally:
        server.shutdown()
        preview.unlink()


if __name__ == "__main__":
    main()
