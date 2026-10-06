#!/usr/bin/env python3
"""Render the home page, one page per city (cities/*.json), the 404 page, sitemap.xml and robots.txt.

Usage: python3 build_pages.py   (run build_data.py <city> first: figures come from sources/<city>.json)
"""

from __future__ import annotations

import hashlib
import html
import json
import unicodedata
from datetime import date
from pathlib import Path
from string import Template
from urllib.parse import quote

from cities import load_cities

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
SITE_URL = "https://tram.camilleroux.com/"
GITHUB_URL = "https://github.com/camilleroux/montpellier-temps-transport"
X_URL = "https://x.com/CamilleRoux"
LINKEDIN_URL = "https://www.linkedin.com/in/camilleroux"
BLUESKY_URL = "https://bsky.app/profile/camilleroux.com"
AUTHOR_URL = "https://www.camilleroux.com/"
SITE_NAME = "À portée de tram"
ANALYTICS = (
    '    <!-- Cloudflare Web Analytics (sans cookie). "spa": false : les mises à jour de l\'URL ne comptent pas comme des pages vues. -->\n'
    '    <script type="module" src="https://static.cloudflareinsights.com/beacon.min.js" '
    "data-cf-beacon='{\"token\": \"1904c17ed0624c0cab4d69ea1bacc5e7\", \"spa\": false}'></script>"
)
LICENCES = {
    "lo": ("Licence Ouverte 2.0", "https://www.etalab.gouv.fr/licence-ouverte-open-licence/"),
    "odbl": ("ODbL", "https://opendatacommons.org/licenses/odbl/1-0/"),
    "mobilites": ("Licence Mobilités", "https://wiki.lafabriquedesmobilites.fr/wiki/Licence_Mobilit%C3%A9s"),
    "ccby": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/deed.fr"),
}
GEO_CREDITS = {
    "ban": '<a href="https://geo.api.gouv.fr/">contours communaux</a>, recherche d\'adresse via la\n'
    '          <a href="https://adresse.data.gouv.fr/">Base Adresse Nationale</a>.',
    "photon": 'limites administratives OpenStreetMap, recherche d\'adresse via\n'
    '          <a href="https://photon.komoot.io/">Photon</a> (komoot, données OpenStreetMap).',
}
ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
MODE_LABEL_SHORT = {"tram": "Tram", "metro": "Métro", "metro+tram": "Métro et tram"}
MODE_NAMES = {"metro": "Métro", "rer": "RER", "train": "Train", "tram": "Tram", "funicular": "Funiculaire", "cable": "Téléphérique", "busway": "Busway", "bhns": "BHNS"}
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

esc = html.escape


def text_color(background: str) -> str:
    """Black or white text, whichever reads best on a line colour (yellow lines need black)."""
    value = int(background.lstrip("#")[:6] or "888888", 16)
    luminance = 0.299 * (value >> 16) + 0.587 * ((value >> 8) & 255) + 0.114 * (value & 255)
    return "#111" if luminance > 150 else "#fff"


def line_badge(color: str, name: str) -> str:
    return f'<span class="line-badge" style="background:{color};color:{text_color(color)}">{esc(name)}</span>'


def thousands(value: int) -> str:
    """French thousands separator: 2445 → « 2 445 » (narrow no-break space)."""
    return f"{value:,}".replace(",", "\u202f")


def num(value: float) -> str:
    """French decimal comma: 4.4 → « 4,4 »."""
    return f"{value:g}".replace(".", ",")


def short_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.exists() else "0"


def french_date(value: str, weekday: bool = False) -> str:
    day = date.fromisoformat(value[:10])
    text = f"{day.day} {MONTHS[day.month - 1]} {day.year}"
    return f"{WEEKDAYS[day.weekday()]} {text}" if weekday else text


def load_built_cities() -> list[dict]:
    """Cities whose data has been built, with their figures (sources/<city>.json)."""
    cities = []
    for city in load_cities():
        sources = ROOT / "sources" / f"{city['slug']}.json"
        if not sources.exists() or not (SITE / "data" / f"{city['slug']}.json").exists():
            print(f"  {city['slug']} ignorée : lancer d'abord build_data.py {city['slug']}")
            continue
        city["sources"] = json.loads(sources.read_text(encoding="utf-8"))
        city["stats"] = city["sources"]["stats"]
        cities.append(city)
    return cities


def json_ld(data: dict) -> str:
    body = json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
    return '    <script type="application/ld+json">\n    ' + body.replace("\n", "\n    ") + "\n    </script>"


def head(*, title: str, description: str, url: str, base: str, image: str, image_alt: str, published: str, graph: list) -> str:
    """<head> content shared by every page: SEO, social previews, structured data."""
    redirect = (
        "    <script>\n"
        "      // Anciens liens github.io : on renvoie vers le domaine du site en gardant départ et arrivée.\n"
        f'      if (location.hostname.endsWith("github.io")) location.replace("{url}" + location.search);\n'
        "    </script>"
    )
    title_text = esc(title.split(" · ")[0])
    return "\n".join(
        [
            '    <meta charset="utf-8" />',
            '    <meta name="viewport" content="width=device-width, initial-scale=1" />',
            f"    <title>{esc(title)}</title>",
            f'    <meta name="description" content="{esc(description)}" />',
            redirect,
            f'    <link rel="canonical" href="{url}" />',
            '    <meta name="theme-color" content="#3aa70b" />',
            f'    <link rel="icon" href="{base}favicon.svg" type="image/svg+xml" />',
            f'    <link rel="icon" href="{base}favicon-32.png" type="image/png" sizes="32x32" />',
            f'    <link rel="apple-touch-icon" href="{base}apple-touch-icon.png" />',
            '    <meta name="author" content="Camille Roux" />',
            f'    <link rel="author" href="{AUTHOR_URL}" />',
            '    <meta property="og:type" content="website" />',
            '    <meta property="og:locale" content="fr_FR" />',
            f'    <meta property="og:site_name" content="{SITE_NAME}" />',
            f'    <meta property="og:title" content="{title_text}" />',
            f'    <meta property="og:description" content="{esc(description)}" />',
            f'    <meta property="og:url" content="{url}" />',
            f'    <meta property="og:image" content="{image}" />',
            '    <meta property="og:image:width" content="1200" />',
            '    <meta property="og:image:height" content="630" />',
            f'    <meta property="og:image:alt" content="{esc(image_alt)}" />',
            f'    <meta property="article:author" content="{AUTHOR_URL}" />',
            f'    <meta property="article:published_time" content="{published}T08:00:00+02:00" />',
            '    <meta name="twitter:card" content="summary_large_image" />',
            f'    <meta name="twitter:title" content="{title_text}" />',
            f'    <meta name="twitter:description" content="{esc(description)}" />',
            f'    <meta name="twitter:image" content="{image}" />',
            json_ld({"@context": "https://schema.org", "@graph": graph}),
            '    <link rel="preconnect" href="https://fonts.bunny.net" />',
            '    <link rel="stylesheet" href="https://fonts.bunny.net/css?family=inter:400,500,600,700,800" />',
        ]
    )


def header(base: str) -> str:
    return f"""    <header class="topbar">
      <nav class="topbar-inner" aria-label="Navigation principale">
        <a class="brand" href="{base}"><img src="{base}favicon.svg" width="22" height="22" alt="" /> {SITE_NAME}</a>
        <div class="topbar-links">
          <a class="topbar-link" href="{base}{RANKINGS_DIR}/">🏆 Classements</a>
          <a class="topbar-link" href="{AUTHOR_URL}" rel="author">camilleroux.com</a>
        </div>
      </nav>
    </header>"""


def footer(cities: list[dict], base: str, data_credit: str, geocoder: str = "ban") -> str:
    links = " · ".join(f'<a href="{base}{city["path"]}">{esc(city["name"])}</a>' for city in sorted(cities, key=lambda c: c["name"]))
    return f"""    <footer class="site-footer">
      <div class="footer-inner">
        <p class="footer-author">
          Un projet de <a href="{AUTHOR_URL}" rel="author">Camille Roux</a>, développeur et co-fondateur de Human Coders,
          à Montpellier, d'après l'idée d'Anthony Castrio et de Jules Grandin. Découvrez
          <a href="{AUTHOR_URL}realisations/">ses autres réalisations</a> et sa
          <a href="{AUTHOR_URL}veille/">veille tech hebdomadaire</a>.
        </p>
        <p class="footer-links">Villes&nbsp;: {links}</p>
        <p class="footer-links">
          <a href="{GITHUB_URL}" rel="noopener">Code source sur GitHub</a> ·
          <a href="{GITHUB_URL}/issues" rel="noopener">Proposer une ville ou signaler une erreur</a> ·
          <a href="{base}classements/">Classements</a> ·
          <a href="{base}mentions-legales/">Mentions légales et licences</a>
        </p>
        <p class="footer-credits">
          Idée originale&nbsp;: le <a href="https://castrio.me/nyc/">NYC Transit Time Cartogram</a> d'Anthony Castrio,
          adapté ensuite à Paris par Jules Grandin
          (<a href="https://julesgrandin.github.io/paris-temps-transport/">C'est encore loin&nbsp;?</a>).
          {data_credit} Fond de carte © <a href="https://www.openstreetmap.org/copyright">contributeurs OpenStreetMap</a>,
          {GEO_CREDITS[geocoder]}
          Données calculées publiées sous licence <a href="{ODBL_URL}">ODbL</a>, code sous licence MIT.
        </p>
      </div>
    </footer>"""


X_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18.24 2.25h3.31l-7.23 8.26 8.5 11.24h-6.65l-5.21-6.82-5.97 '
    '6.82H1.68l7.73-8.84L1.25 2.25h6.83l4.71 6.23zm-1.16 17.52h1.83L7.08 4.13H5.12z"/></svg>'
)
LINKEDIN_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.13 '
    '1.45-2.13 2.94v5.67H9.35V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 4.9v6.29zM5.34 7.43a2.06 '
    '2.06 0 1 1 0-4.12 2.06 2.06 0 0 1 0 4.12zM7.12 20.45H3.56V9h3.56zM22.23 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 '
    '24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .77 23.2 0 22.22 0z"/></svg>'
)


def follow_note() -> str:
    """Under the list of cities: where the next ones are announced, and where to ask for one."""
    return f"""        <aside class="follow-card" aria-label="Nouvelles villes">
          <div class="follow-text">
            <p class="follow-title">Suivre les nouvelles villes</p>
            <p>Chaque nouvelle ville est annoncée sur X et LinkedIn. La vôtre n'est pas encore là&nbsp;?
            <a href="{GITHUB_URL}/issues" rel="noopener">Proposez-la sur GitHub</a>.</p>
          </div>
          <div class="follow-actions">
            <a class="button follow-x" href="{X_URL}" rel="me noopener">{X_ICON} Suivre sur X</a>
            <a class="button follow-linkedin" href="{LINKEDIN_URL}" rel="me noopener">{LINKEDIN_ICON} Suivre sur LinkedIn</a>
          </div>
        </aside>"""


def faq_block(entries: list[tuple]) -> str:
    """Entries are (question, answer) or (question, answer, answer_html) when the visible answer carries links."""
    return "\n".join(
        f'        <details class="faq"><summary>{esc(entry[0])}</summary><p>{entry[2] if len(entry) > 2 else esc(entry[1])}</p></details>'
        for entry in entries
    )


def faq_schema(entries: list[tuple]) -> dict:
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": entry[0], "acceptedAnswer": {"@type": "Answer", "text": entry[1]}} for entry in entries
        ],
    }


def credits_entry(question: str) -> tuple:
    """« Who made this? »: the original authors first, then the author of these maps."""
    text = (
        "L'idée vient du NYC Transit Time Cartogram d'Anthony Castrio, adapté ensuite à Paris par Jules Grandin "
        "(« C'est encore loin ? »). Ces cartes sont réalisées par Camille Roux, développeur et co-fondateur de Human "
        "Coders à Montpellier, qui présente ses autres réalisations et sa veille tech hebdomadaire sur camilleroux.com. Le "
        "code est ouvert sur GitHub."
    )
    html_text = (
        'L\'idée vient du <a href="https://castrio.me/nyc/">NYC Transit Time Cartogram</a> d\'Anthony Castrio, adapté '
        'ensuite à Paris par Jules Grandin (<a href="https://julesgrandin.github.io/paris-temps-transport/">C\'est encore '
        f'loin&nbsp;?</a>). Ces cartes sont réalisées par <a href="{AUTHOR_URL}" rel="author">Camille Roux</a>, développeur '
        f'et co-fondateur de Human Coders à Montpellier&nbsp;: découvrez <a href="{AUTHOR_URL}realisations/">ses autres '
        f'réalisations</a> et <a href="{AUTHOR_URL}veille/">sa veille tech hebdomadaire</a>. Le code est ouvert sur '
        f'<a href="{GITHUB_URL}">GitHub</a>.'
    )
    return (question, text, html_text)


def author_schema() -> dict:
    return {
        "@type": "Person",
        "@id": AUTHOR_URL + "#me",
        "name": "Camille Roux",
        "url": AUTHOR_URL,
        "image": AUTHOR_URL + "content/images/size/w256h256/format/jpeg/2025/05/camillecouleur---lowres-2.jpg",
        "jobTitle": "Développeur, co-fondateur de Human Coders",
        "worksFor": {"@type": "Organization", "name": "Human Coders", "url": "https://www.humancoders.com/"},
        "address": {"@type": "PostalAddress", "addressLocality": "Montpellier", "addressCountry": "FR"},
        "sameAs": [
            LINKEDIN_URL,
            X_URL,
            BLUESKY_URL,
            "https://mastodon.social/@camilleroux",
            "https://github.com/camilleroux",
        ],
    }


# « en France, en Belgique et au Québec »: where the cities are, for the home page.
COUNTRY_IN = {"FR": "en France", "BE": "en Belgique", "CA": "au Québec", "CH": "en Suisse", "LU": "au Luxembourg"}


def city_count(cities: list[dict]) -> str:
    """« 23 villes françaises », then « 27 villes en France, en Belgique et au Québec » once there are cities abroad."""
    countries = sorted({city["country"] for city in cities}, key=list(COUNTRY_IN).index)
    if countries == ["FR"]:
        return f"{len(cities)} villes françaises"
    places = [COUNTRY_IN[country] for country in countries]
    return f"{len(cities)} villes " + ", ".join(places[:-1]) + f" et {places[-1]}"


def city_card(city: dict, base: str, heading: str = "h3") -> str:
    stats = city["stats"]
    return f"""          <a class="city-card" href="{base}{city['path']}">
            <img src="{base}og/thumb-{city['slug']}.jpg" width="600" height="315" alt="" loading="lazy" />
            <span class="city-card-body">
              <{heading}>{esc(city['title'])}</{heading}>
              <span>{stats['within30']}&nbsp;% des {esc(city['railStations'])} à moins de 30&nbsp;min du centre ({esc(stats['center'])}) · réseau {esc(city['network'])}</span>
            </span>
          </a>"""


def city_faq(city: dict) -> list[tuple]:
    stats, sources = city["stats"], city["sources"]
    name, rail = city["name"], city["railNoun"]
    lines = stats["lines"]
    headways = ", ".join(f"{MODE_NAMES.get(line['mode'], 'ligne').lower()} {line['name']} : {num(line['headway'])} min" for line in lines)
    fastest = min(lines, key=lambda line: line["headway"])
    period = sources["gtfs"].get("servicePeriod") or [None, None]
    fetched = sources["gtfs"].get("fetchedAt")
    return [
        (
            f"Combien de temps faut-il pour traverser {name} en {rail} ?",
            f"Depuis le centre ({stats['center']}), {stats['within15']} % des {city['railStations']} sont à moins de 15 minutes et "
            f"{stats['within30']} % à moins de 30 minutes, marche et attente comprises. La plus éloignée, {stats['farthestStation']}, "
            f"est à environ {stats['farthestMinutes']} minutes.",
        ),
        (
            f"Quelle est la fréquence des lignes de {rail} à {name} ?",
            f"En journée de semaine, l'intervalle moyen entre deux passages est de {headways}. La ligne la plus fréquente "
            f"est la {fastest['name']}, avec un passage toutes les {num(fastest['headway'])} minutes environ.",
        ),
        (
            "D'où viennent les horaires utilisés ?",
            f"Des horaires théoriques publiés par le réseau {city['network']} (format GTFS, {LICENCES[city['gtfsLicence']][0]})"
            + (f", téléchargés le {french_date(fetched)}" if fetched else "")
            + (f" et valables jusqu'au {french_date(period[1])}" if period[1] else "")
            + f". Les temps correspondent au {french_date(sources['referenceDate'], weekday=True)}, entre 7 h et 20 h.",
        ),
        (
            f"Les bus sont-ils pris en compte à {name} ?",
            f"Oui, en option : cochez « {city['busLabel']} » sous la carte. Par défaut, seuls les {rail} sont affichés. "
            "L'attente aux arrêts de bus peu fréquentés est plafonnée à 15 minutes.",
        ),
        (
            "Comment les temps de trajet sont-ils calculés ?",
            "Pour chaque trajet : marche jusqu'à l'arrêt à 4,5 km/h, attente égale à la moitié de l'intervalle entre deux "
            "passages, durée prévue entre les arrêts, correspondances avec 1,5 minute de marche"
            + (", et 1 minute pour rejoindre le quai du métro" if any(line["mode"] == "metro" for line in lines) else "")
            + ". Pas de temps réel ni de perturbations : c'est la ville « sur le papier ».",
        ),
        credits_entry(f"Qui a réalisé cette carte de {name} ?"),
    ]


def ranking_positions_block(cities: list[dict], city: dict) -> str:
    items = city_positions(cities, city["slug"], "../")
    if not items:
        return ""
    lines = "\n".join(f"          {item}" for item in items)
    return f"""        <h3 class="ranking-positions-title">🏆 {esc(city["name"])} dans les classements</h3>
        <ul class="ranking-positions">
{lines}
        </ul>
        <p class="section-link"><a href="../{RANKINGS_DIR}/">Tous les classements des trams et métros de France →</a></p>"""


def original_map(city: dict) -> str:
    """Paris had a map before this site: Jules Grandin's, which inspired it. It comes first, above this one."""
    original = city.get("originalMap")
    if not original:
        return ""
    return f"""        <aside class="original-map">
          <p>
            <strong>La carte originale, c'est celle de {esc(original["author"])}&nbsp;:</strong>
            <a href="{esc(original["url"])}">{esc(original["title"]).replace(" ?", "&nbsp;?")}</a>, en {esc(original["modes"])}, qui a
            inspiré tout ce site (elle-même partie du <a href="https://castrio.me/nyc/">NYC Transit Time Cartogram</a>
            d'Anthony Castrio). Allez la voir&nbsp;! Cette version-ci part des horaires {esc(city["network"])} et ajoute
            {esc(original["adds"])}.
          </p>
        </aside>
"""


def render_city(template: Template, cities: list[dict], city: dict) -> str:
    url = SITE_URL + city["path"]
    base = "../"
    stats = city["stats"]
    rail_noun = city["railNoun"]
    description = (
        f"Carte des temps de trajet en {rail_noun} à {city['name']} : choisissez un départ, toute la ville se colore "
        f"selon le temps qu'il faut pour y aller (réseau {city['network']})."
    )
    faq = city_faq(city)
    graph = [
        {
            "@type": "WebApplication",
            "name": city["title"],
            "url": url,
            "description": description,
            "inLanguage": "fr",
            "applicationCategory": "TravelApplication",
            "operatingSystem": "Web",
            "isAccessibleForFree": True,
            "image": f"{SITE_URL}og/{city['slug']}.jpg",
            "author": {"@id": AUTHOR_URL + "#me"},
            "spatialCoverage": {"@type": "Place", "name": city["metropole"]},
            "isBasedOn": ["https://castrio.me/nyc/", "https://julesgrandin.github.io/paris-temps-transport/"],
            "datePublished": city["published"],
            "dateModified": city["sources"]["builtAt"][:10],
        },
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": SITE_URL},
                {"@type": "ListItem", "position": 2, "name": city["name"], "item": url},
            ],
        },
        faq_schema(faq),
        author_schema(),
    ]
    fastest = min(stats["lines"], key=lambda line: line["headway"])
    tiles = [
        (f"{stats['within30']} %", f"des {city['railStations']} à moins de 30 min du centre ({stats['center']})"),
        (str(stats["railStations"]), city["railStations"]),
        (f"{num(fastest['headway'])} min", f"entre deux passages sur la ligne {fastest['name']}, la plus fréquente"),
        (f"{stats['farthestMinutes']} min", f"depuis le centre pour rejoindre {stats['farthestStation']}, la station la plus éloignée"),
    ]
    stat_tiles = "\n".join(f'          <div class="stat"><strong>{esc(value)}</strong><span>{esc(label)}</span></div>' for value, label in tiles)
    line_rows = "\n".join(
        f'            <tr><td>{line_badge(line["color"], line["name"])} '
        f'{esc(MODE_NAMES.get(line["mode"], ""))}</td><td>{line["stations"]}</td><td>~{num(line["headway"])} min</td></tr>'
        for line in stats["lines"]
    )
    # City switcher: the city name in the title opens a panel of real links (site/app.js), crawlable as well.
    items = "\n".join(
        f'            <a class="city-item{" current" if other["slug"] == city["slug"] else ""}" href="{base}{other["path"]}"'
        f'{" aria-current=\"page\"" if other["slug"] == city["slug"] else ""} data-name="{esc(other["name"].lower())}">'
        f'<img src="{base}og/thumb-{other["slug"]}.jpg" width="120" height="63" alt="" loading="lazy" />'
        f'<span><strong>{esc(other["name"])}</strong><small>{esc(MODE_LABEL_SHORT[other["kind"]])} · {esc(other["network"])}</small></span></a>'
        for other in sorted(cities, key=lambda item: item["name"])
    )
    rest = esc(city["title"][len(city["name"]):])
    headline = (
        f'<button id="cityTrigger" type="button" class="city-trigger" aria-haspopup="dialog" aria-expanded="false" '
        f'title="Changer de ville">{esc(city["name"])}<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 6l5 5 5-5"/></svg></button>'
        + "&nbsp;".join(rest.rsplit(" ", 1))
    )
    config = {
        "slug": city["slug"],
        "name": city["name"],
        "dataVersion": short_hash(SITE / "data" / f"{city['slug']}.json"),
        "defaultFrom": city["defaultFrom"],
        "railNoun": rail_noun,
        "railStations": city["railStations"],
        "busNoun": city["busNoun"],
        "geocoder": city["geocoder"],
        "searchBbox": city["osmBbox"],
    }
    feeds = " et ".join(f'<a href="{esc(feed["dataset"])}">GTFS {esc(feed["network"])}</a>' for feed in gtfs_feeds(city))
    data_credit = f'Horaires&nbsp;: {feeds} ({esc(city["metropole"])}).'

    values = {
        "head": head(
            title=f"{city['title']} · {city['titleSuffix']}",
            description=description,
            url=url,
            base=base,
            image=f"{SITE_URL}og/{city['slug']}.jpg?v={short_hash(SITE / 'og' / (city['slug'] + '.jpg'))}",
            image_alt=city["ogAlt"],
            published=city["published"],
            graph=graph,
        ),
        "header": header(base),
        "footer": footer(cities, base, data_credit, city["geocoder"]),
        "analytics": ANALYTICS,
        "base": base,
        "city_config": json.dumps(config, ensure_ascii=False).replace("</", "<\\/"),
        "city_items": items,
        "original_map": original_map(city),
        "headline": headline,
        "name": esc(city["name"]),
        "area": esc(city.get("area", "de la Métropole")),
        "rail_noun": esc(rail_noun),
        "rail_label": esc(city["railLabel"]),
        "bus_label": esc(city["busLabel"]),
        "bus_noun": esc(city["busNoun"]),
        "search_example": esc(city["searchExample"]),
        "network": esc(city["network"]),
        "stat_tiles": stat_tiles,
        "ranking_positions": ranking_positions_block(cities, city),
        "line_rows": line_rows,
        "faq_html": faq_block(faq),
        "follow": follow_note(),
        "other_cities": "\n".join(city_card(other, base) for other in cities if other["slug"] != city["slug"]),
        "styles_version": short_hash(SITE / "styles.css"),
        "app_version": short_hash(SITE / "app.js"),
    }
    return template.substitute(values)


def render_home(template: Template, cities: list[dict]) -> str:
    names = ", ".join(city["name"] for city in cities[:-1]) + f" et {cities[-1]['name']}"
    networks = ", ".join(f"{city['network']} ({city['name']})" for city in cities)
    description = f"Cartes des temps de trajet en tram et métro à {names} : la ville se colore selon le temps pour y aller."
    faq = [
        (
            "D'où viennent les temps de trajet ?",
            f"Des horaires théoriques officiels de chaque réseau ({networks}), publiés en open data au format GTFS. "
            "Ils correspondent à un jour de semaine ordinaire, entre 7 h et 20 h.",
        ),
        (
            "Les temps affichés sont-ils fiables ?",
            "Ce sont des moyennes « sur le papier » : marche jusqu'à l'arrêt, attente égale à la moitié de l'intervalle entre "
            "deux passages, durée prévue entre les arrêts et correspondances. Pas de temps réel ni de perturbations.",
        ),
        (
            "Le bus est-il pris en compte ?",
            "Oui, en option sur chaque carte. Par défaut, seuls le tram, le métro et les transports guidés sont affichés, "
            "pour montrer l'ossature du réseau.",
        ),
        (
            "Ma ville n'y est pas, pourquoi ?",
            "Il faut un réseau de tram ou de métro et des horaires publiés en open data. Les prochaines villes sont ajoutées "
            "au fur et à mesure : vous pouvez en proposer une sur GitHub, et chaque nouvelle ville est annoncée sur X "
            "et LinkedIn.",
            "Il faut un réseau de tram ou de métro et des horaires publiés en open data. Les prochaines villes sont ajoutées "
            f'au fur et à mesure&nbsp;: vous pouvez en <a href="{GITHUB_URL}/issues">proposer une sur GitHub</a>, et chaque '
            f'nouvelle ville est annoncée sur <a href="{X_URL}">X</a> et <a href="{LINKEDIN_URL}">LinkedIn</a>.',
        ),
        credits_entry("Qui a réalisé ce site ?"),
    ]
    graph = [
        {
            "@type": "WebSite",
            "@id": SITE_URL + "#site",
            "name": SITE_NAME,
            "url": SITE_URL,
            "description": description,
            "inLanguage": "fr",
            "author": {"@id": AUTHOR_URL + "#me"},
        },
        {
            "@type": "ItemList",
            "name": "Cartes des temps de trajet par ville",
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "name": city["title"], "url": SITE_URL + city["path"]}
                for i, city in enumerate(cities)
            ],
        },
        faq_schema(faq),
        author_schema(),
    ]
    published = min(city["published"] for city in cities)
    values = {
        "head": head(
            title=f"{SITE_NAME} · Temps de trajet en tram et métro par ville",
            description=description,
            url=SITE_URL,
            base="./",
            image=SITE_URL + "og/home.jpg?v=" + short_hash(SITE / "og" / "home.jpg"),
            image_alt=f"Cartes des temps de trajet en tram et métro à {names}.",
            published=published,
            graph=graph,
        ),
        "header": header("./"),
        "footer": footer(cities, "./", "Horaires&nbsp;: GTFS des réseaux de chaque ville (détail dans les mentions légales)."),
        "analytics": ANALYTICS,
        "city_count": city_count(cities),
        "city_cards": "\n".join(city_card(city, "./", "h2") for city in cities),
        "city_links": "\n".join(
            f'          <a class="chip" href="./{city["path"]}">{esc(city["name"])}</a>'
            for city in sorted(cities, key=lambda city: unicodedata.normalize("NFD", city["name"]))
        ),
        "faq_html": faq_block(faq),
        "follow": follow_note(),
        "styles_version": short_hash(SITE / "styles.css"),
    }
    return template.substitute(values)


def gtfs_feeds(city: dict) -> list[dict]:
    """The feeds of a city: its main GTFS, then the extra ones merged into it (the REM next to the STM in Montréal)."""
    main = {
        "network": city.get("gtfsNetwork", city["network"]),
        "dataset": city["gtfsDataset"],
        "licence": city["gtfsLicence"],
        "fetchedAt": city["sources"]["gtfs"].get("fetchedAt"),
    }
    fetched = {extra["network"]: extra.get("fetchedAt") for extra in city["sources"].get("gtfsExtra", [])}
    extras = [{**extra, "fetchedAt": fetched.get(extra["network"])} for extra in city.get("gtfsExtra", [])]
    return [main, *extras]


def render_legal(cities: list[dict]) -> str:
    """Mentions légales (LCEN) and the licence of every source."""
    rows = "\n".join(
        f'          <tr><td>{esc(city["name"])}</td><td><a href="{esc(feed["dataset"])}">GTFS {esc(feed["network"])}</a></td>'
        f'<td><a href="{LICENCES[feed["licence"]][1]}">{LICENCES[feed["licence"]][0]}</a></td>'
        f'<td>{french_date(feed["fetchedAt"]) if feed["fetchedAt"] else "—"}</td></tr>'
        for city in sorted(cities, key=lambda item: item["name"])
        for feed in gtfs_feeds(city)
    )
    rows += "".join(
        f'\n          <tr><td>{esc(city["city"])} (classements)</td><td><a href="{esc(city["source"]["dataset"])}">GTFS {esc(city["network"])}</a></td>'
        f'<td><a href="{LICENCES[city["source"]["licence"]][1]}">{LICENCES[city["source"]["licence"]][0]}</a></td>'
        f'<td>{french_date(city["source"]["fetchedAt"])}</td></tr>'
        for city in load_rankings(cities)
        if city.get("externalUrl")
    )
    graph = [author_schema()]
    return f"""<!doctype html>
<html lang="fr">
  <head>
{head(title=f"Mentions légales et licences · {SITE_NAME}", description="Éditeur, hébergeur, mesure d'audience et licences des données utilisées par À portée de tram.", url=SITE_URL + "mentions-legales/", base="../", image=SITE_URL + "og/home.jpg", image_alt="À portée de tram", published="2026-10-05", graph=graph)}
    <link rel="stylesheet" href="../styles.css?v={short_hash(SITE / 'styles.css')}" />
  </head>
  <body>
{header('../')}
    <main class="page">
      <nav class="breadcrumb" aria-label="Fil d'Ariane">
        <a href="../">{SITE_NAME}</a> <span aria-hidden="true">›</span> <span aria-current="page">Mentions légales</span>
      </nav>
      <section class="section">
        <h1 class="page-title">Mentions légales et licences</h1>
        <h2>Éditeur</h2>
        <p>Ce site est édité à titre personnel par <a href="{AUTHOR_URL}" rel="author">Camille Roux</a>. Contact&nbsp;: via
        <a href="{AUTHOR_URL}contact/">la page contact de camilleroux.com</a> ou les <a href="{GITHUB_URL}/issues">issues GitHub</a> du projet.</p>
        <h2>Hébergement</h2>
        <p>GitHub, Inc. (GitHub Pages), 88 Colin P. Kelly Jr. Street, San Francisco, CA 94107, États-Unis.
        Nom de domaine géré par Cloudflare, Inc., 101 Townsend Street, San Francisco, CA 94107, États-Unis.</p>
        <h2>Mesure d'audience et données personnelles</h2>
        <p>La fréquentation est mesurée avec Cloudflare Web Analytics, sans cookie ni identifiant personnel. Les trajets
        sont calculés dans votre navigateur&nbsp;: aucune position ni adresse n'est enregistrée. La recherche d'adresse
        interroge l'API de la Base Adresse Nationale (adresse.data.gouv.fr) et, hors de France, l'API Photon de komoot
        (photon.komoot.io, données OpenStreetMap).</p>
        <h2>Licences</h2>
        <p>Le code est publié sous licence MIT sur <a href="{GITHUB_URL}">GitHub</a>. Les données calculées
        (<code>data/*.json</code>) sont des bases de données dérivées, publiées sous licence <a href="{ODBL_URL}">ODbL</a>.
        Fond de carte et tracés&nbsp;: © <a href="https://www.openstreetmap.org/copyright">contributeurs OpenStreetMap</a>
        (ODbL). Contours communaux&nbsp;: <a href="https://geo.api.gouv.fr/">geo.api.gouv.fr</a> (Licence Ouverte) et, hors
        de France, limites administratives OpenStreetMap (ODbL).</p>
        <table class="lines-table">
          <caption>Horaires utilisés pour chaque ville</caption>
          <thead><tr><th scope="col">Ville</th><th scope="col">Source</th><th scope="col">Licence</th><th scope="col">Téléchargé le</th></tr></thead>
          <tbody>
{rows}
          </tbody>
        </table>
      </section>
    </main>
{footer(cities, "../", "")}
{ANALYTICS}
  </body>
</html>
"""


def write_sources_readme(cities: list[dict]) -> None:
    lines = [
        "# Provenance des données",
        "",
        "Généré par `build_pages.py` à partir des fiches `sources/<ville>.json`.",
        "",
        "| Ville | Réseau | Licence | GTFS téléchargé le | Validité du GTFS | Jour de référence |",
        "|---|---|---|---|---|---|",
    ]
    for city in sorted(cities, key=lambda item: item["name"]):
        gtfs = city["sources"]["gtfs"]
        period = gtfs.get("servicePeriod") or ["?", "?"]
        how = " (à la main)" if gtfs.get("how") == "manual" else ""
        lines.append(
            f"| [{city['name']}]({city['slug']}.json) | {city['network']} | {LICENCES[city['gtfsLicence']][0]} | "
            f"{gtfs.get('fetchedAt', '?')[:10]}{how} | {period[0]} → {period[1]} | {city['sources']['referenceDate']} |"
        )
    (ROOT / "sources" / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


RANKINGS_DIR = "classements"
MEDALS = ["🥇", "🥈", "🥉"]


def load_rankings(cities: list[dict]) -> list[dict]:
    """Figures read straight from the timetables (sources/rankings.json, written by tools/rankings.py).

    Cities with a map here, plus the ones that only take part in the rankings (`externalUrl`: their map is elsewhere).
    """
    path = ROOT / "sources" / "rankings.json"
    if not path.exists():
        return []
    published = {city["slug"] for city in cities}
    entries = json.loads(path.read_text(encoding="utf-8")).values()
    return [city for city in entries if city["slug"] in published or city.get("externalUrl")]


def ordinal(rank: int) -> str:
    return "1<sup>re</sup>" if rank == 1 else f"{rank}<sup>e</sup>"


def clock(seconds: int) -> str:
    """Time of night in French: 26:27 → « 2 h 27 »."""
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours % 24}&nbsp;h&nbsp;{minutes:02d}"


def competition_ranks(values: list[float]) -> list[int]:
    """Rank of each value (sorted, best first); equal values share a rank: 1, 2, 2, 4."""
    return [1 + sum(1 for other in values if other > value) for value in values]


def ranking_definitions(data: list[dict], base: str) -> list[dict]:
    """The four published rankings. Each row: its cells, the value it is ranked on, its city and mode."""

    def city_link(city: dict) -> str:
        if city.get("externalUrl"):
            return f'<a href="{esc(city["externalUrl"])}" rel="noopener" title="La carte de Jules Grandin">{esc(city["city"])}</a>'
        return f'<a href="{base}{city["path"]}">{esc(city["city"])}</a>'

    def line_label(line: dict, name: str) -> str:
        return f'{line_badge(line["color"], name)} {esc(MODE_NAMES[line["mode"]])}'

    lines = [(city, name, line) for city in data for name, line in city["lines"].items() if line["peakPassages"]]
    frequent = sorted(lines, key=lambda item: (-item[2]["peakPassages"], item[0]["city"], item[1]))
    stations = sorted(data, key=lambda city: (-city["busiestStation"]["passages"], city["city"]))
    longest = sorted(lines, key=lambda item: (-item[2]["endToEndMinutes"], item[0]["city"], item[1]))
    trips = sorted(data, key=lambda city: (-city["weekdayTrips"], city["city"]))
    night = sorted(
        (city for city in data if city.get("centre", {}).get("lastSaturday")),
        key=lambda city: (-city["centre"]["lastSaturday"]["seconds"], city["city"]),
    )

    def night_line(city: dict) -> str:
        last = city["centre"]["lastSaturday"]
        line = city["lines"].get(last["line"])
        return line_label(line, last["line"]) if line else esc(last["line"])

    def station_cell(city: dict) -> str:
        best = city["busiestStation"]
        if best["station"]:
            return esc(best["station"])
        tied = [esc(name) for name in best["tied"]]
        if len(tied) > 3:
            return f'<span class="muted">Égalité entre {len(tied)} stations</span>'
        return f'<span class="muted">Ex aequo : {", ".join(tied[:-1])} et {tied[-1]}</span>'

    def line_rows(items: list, value, cells) -> list[dict]:
        return [
            {"city": city["slug"], "line": name, "mode": line["mode"], "value": value(line), "cells": cells(city, name, line)}
            for city, name, line in items
        ]

    return [
        {
            "slug": "dernier-tram-samedi-soir",
            "short": "Le dernier tram du samedi",
            "title": "Le dernier tram et le dernier métro du samedi soir, ville par ville",
            "question": "Où rentre-t-on le plus tard en tram ou en métro le samedi soir ?",
            "intro": "Après le concert, le bar ou le restaurant : jusqu'à quelle heure peut-on encore attraper un tram ou un "
            "métro en plein centre-ville, la nuit du samedi au dimanche ?",
            "method": "Dernier passage d'un tram ou d'un métro à la station du centre-ville (celle de la place centrale de "
            "chaque carte), la nuit du samedi au dimanche, d'après les horaires théoriques d'un samedi ordinaire. "
            "Les bus de nuit ne sont pas comptés.",
            "headers": ["Ville", "Station du centre", "Ligne", "Dernier passage"],
            "valueCol": 3,
            "rows": [
                {"city": city["slug"], "mode": None, "value": city["centre"]["lastSaturday"]["seconds"],
                 "cells": [city_link(city), esc(city["centre"]["station"]), night_line(city), clock(city["centre"]["lastSaturday"]["seconds"])]}
                for city in night
            ],
            "podium": [(clock(city["centre"]["lastSaturday"]["seconds"]), esc(city["centre"]["station"]), esc(city["city"])) for city in night[:3]],
            "highlight": (
                clock(night[0]["centre"]["lastSaturday"]["seconds"]),
                f'dernier passage du samedi soir à la station {esc(night[0]["centre"]["station"])} ({esc(night[0]["city"])})',
            ),
            "position": lambda rank, total, city: (
                f'{ordinal(rank)} sur {total} pour le dernier tram du samedi soir&nbsp;: '
                f'{clock(city["centre"]["lastSaturday"]["seconds"])} à la station {esc(city["centre"]["station"])}'
            ),
        },
        {
            "slug": "metro-tram-le-plus-frequent",
            "short": "Le plus fréquent",
            "title": "Le métro et le tram les plus fréquents de France",
            "question": "Un métro ou un tram toutes les combien ?",
            "intro": "À l'heure de pointe, certaines lignes passent toutes les minutes, d'autres toutes les dix minutes. "
            "Voici les lignes de tram et de métro où l'on attend le moins.",
            "method": "Nombre de passages entre 8 h et 9 h un jour de semaine, à la station et dans le sens les plus "
            "desservis de chaque ligne.",
            "headers": ["Ville", "Ligne", "Passages 8 h – 9 h", "Un passage toutes les"],
            "valueCol": 2,
            "byMode": True,
            "rows": line_rows(
                frequent,
                lambda line: line["peakPassages"],
                lambda city, name, line: [city_link(city), line_label(line, name), str(line["peakPassages"]), f'{num(line["peakHeadway"])} min'],
            ),
            "podium": [
                (f'{num(line["peakHeadway"])} min', f'{esc(MODE_NAMES[line["mode"]])} {esc(name)}', esc(city["city"]))
                for city, name, line in frequent[:3]
            ],
            "highlight": (
                f'{num(frequent[0][2]["peakHeadway"])} min',
                f'entre deux rames du {MODE_NAMES[frequent[0][2]["mode"]].lower()} {esc(frequent[0][1])} à {esc(frequent[0][0]["city"])}',
            ),
            "position": lambda rank, total, city, name, line: (
                f'{ordinal(rank)} {MODE_NAMES[line["mode"]].lower()} le plus fréquent sur {total}&nbsp;: ligne {esc(name)}, '
                f'un passage toutes les {num(line["peakHeadway"])} min à l\'heure de pointe'
            ),
        },
        {
            "slug": "station-la-plus-desservie",
            "short": "La station la plus desservie",
            "title": "La station de tram ou de métro la plus desservie de chaque ville",
            "question": "Quelle station voit passer le plus de rames ?",
            "intro": "Le nœud du réseau, là où se croisent les lignes : la station de chaque ville où passent le plus de "
            "trams et de métros dans la journée.",
            "method": "Passages de tram et de métro (toutes lignes, les deux sens) un jour de semaine, les quais d'un même "
            "nom regroupés. Quand plusieurs stations d'un même tronc commun sont à égalité, aucune n'est désignée.",
            "headers": ["Ville", "Station", "Passages par jour"],
            "valueCol": 2,
            "rows": [
                {"city": city["slug"], "mode": None, "value": city["busiestStation"]["passages"],
                 "cells": [city_link(city), station_cell(city), thousands(city["busiestStation"]["passages"])]}
                for city in stations
            ],
            "podium": [
                (thousands(city["busiestStation"]["passages"]), esc(city["busiestStation"]["station"] or "—"), esc(city["city"]))
                for city in stations[:3]
            ],
            "highlight": (
                thousands(stations[0]["busiestStation"]["passages"]),
                f'passages par jour à {esc(stations[0]["busiestStation"]["station"] or "")} ({esc(stations[0]["city"])})',
            ),
            "position": lambda rank, total, city: (
                f'{ordinal(rank)} sur {total} pour la station la plus desservie&nbsp;: '
                + (esc(city["busiestStation"]["station"]) + ", " if city["busiestStation"]["station"] else "")
                + f'{thousands(city["busiestStation"]["passages"])} passages par jour'
            ),
        },
        {
            "slug": "ligne-la-plus-longue",
            "short": "La ligne la plus longue",
            "title": "Les lignes de tram et de métro les plus longues à parcourir",
            "question": "Combien de temps pour aller d'un terminus à l'autre ?",
            "intro": "Certaines lignes traversent toute l'agglomération : voici celles qu'il faut le plus de temps pour "
            "parcourir de bout en bout.",
            "method": "Durée prévue d'un terminus à l'autre, un jour de semaine, sur le plus long des trajets réguliers de la "
            "ligne (au moins un tiers des passages du trajet le plus courant) : la ligne entière, sans les services "
            "partiels ni les courses exceptionnelles.",
            "headers": ["Ville", "Ligne", "Trajet", "Durée"],
            "valueCol": 3,
            "byMode": True,
            "rows": line_rows(
                longest,
                lambda line: line["endToEndMinutes"],
                lambda city, name, line: [city_link(city), line_label(line, name), esc(line["endToEnd"]), f'{line["endToEndMinutes"]} min'],
            ),
            "podium": [
                (f'{line["endToEndMinutes"]} min', f'{esc(MODE_NAMES[line["mode"]])} {esc(name)}', esc(city["city"]))
                for city, name, line in longest[:3]
            ],
            "highlight": (
                f'{longest[0][2]["endToEndMinutes"]} min',
                f'de bout en bout sur la ligne {esc(longest[0][1])} à {esc(longest[0][0]["city"])}',
            ),
            "position": lambda rank, total, city, name, line: (
                f'{ordinal(rank)} ligne de {MODE_NAMES[line["mode"]].lower()} la plus longue sur {total}&nbsp;: '
                f'ligne {esc(name)}, {line["endToEndMinutes"]} min de bout en bout'
            ),
        },
        {
            "slug": "reseau-le-plus-fourni",
            "short": "Le réseau le plus fourni",
            "title": "Le réseau de tram et de métro le plus fourni de France",
            "question": "Quel réseau fait rouler le plus de trams et de métros ?",
            "intro": "Le nombre de trajets de tram et de métro programmés chaque jour : une mesure simple de l'offre de "
            "chaque réseau.",
            "method": "Nombre de trajets de tram et de métro programmés un jour de semaine, quelle que soit leur longueur.",
            "headers": ["Ville", "Réseau", "Trajets par jour"],
            "valueCol": 2,
            "rows": [
                {"city": city["slug"], "mode": None, "value": city["weekdayTrips"],
                 "cells": [city_link(city), esc(city["network"]), thousands(city["weekdayTrips"])]}
                for city in trips
            ],
            "podium": [(thousands(city["weekdayTrips"]), esc(city["network"]), esc(city["city"])) for city in trips[:3]],
            "highlight": (thousands(trips[0]["weekdayTrips"]), f'trajets de tram et de métro par jour à {esc(trips[0]["city"])}'),
            "position": lambda rank, total, city: (
                f'{ordinal(rank)} réseau le plus fourni sur {total}&nbsp;: {thousands(city["weekdayTrips"])} trajets de '
                f'tram et de métro par jour'
            ),
        },
    ]


def city_positions(cities: list[dict], slug: str, base: str) -> list[str]:
    """Where a city stands in each ranking (its best line for the line rankings), linked to its row."""
    data = load_rankings(cities)
    entry = next((city for city in data if city["slug"] == slug), None)
    if not entry:
        return []
    items = []
    for d in ranking_definitions(data, base):
        rows = d["rows"]
        query = ""
        if d.get("byMode"):
            # Compared with lines of the same mode (a tram cannot match a metro); rows are sorted, so the city's
            # first row is its best line.
            mode = next(row["mode"] for row in rows if row["city"] == slug)
            rows = [row for row in rows if row["mode"] == mode]
            query = f"?mode={mode}"
        ranks = competition_ranks([row["value"] for row in rows])
        index = next(i for i, row in enumerate(rows) if row["city"] == slug)
        if d.get("byMode"):
            name = rows[index]["line"]
            text = d["position"](ranks[index], len(rows), entry, name, entry["lines"][name])
        else:
            text = d["position"](ranks[index], len(rows), entry)
        items.append(f'<li><a href="{base}{RANKINGS_DIR}/{d["slug"]}/{query}#{slug}">{text}</a></li>')
    return items


def ranking_page(*, title: str, description: str, url: str, base: str, image_name: str, crumbs: list, cities: list, body: str) -> str:
    image = SITE / "og" / image_name
    image_url = f"{SITE_URL}og/{image_name}?v={short_hash(image)}" if image.exists() else SITE_URL + "og/home.jpg"
    graph = [
        {
            "@type": "Article",
            "headline": title,
            "description": description,
            "url": url,
            "inLanguage": "fr",
            "author": {"@id": AUTHOR_URL + "#me"},
            "datePublished": date.today().isoformat(),
            "image": image_url,
        },
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "name": name, "item": item} for i, (name, item) in enumerate(crumbs)
            ],
        },
        author_schema(),
    ]
    trail = " <span aria-hidden=\"true\">›</span> ".join(
        [f'<a href="{item.replace(SITE_URL, base) or "./"}">{esc(name)}</a>' for name, item in crumbs[:-1]]
        + [f'<span aria-current="page">{esc(crumbs[-1][0])}</span>']
    )
    return f"""<!doctype html>
<html lang="fr">
  <head>
{head(title=f"{title} · {SITE_NAME}", description=description, url=url, base=base, image=image_url, image_alt=title, published=date.today().isoformat(), graph=graph)}
    <link rel="stylesheet" href="{base}styles.css?v={short_hash(SITE / 'styles.css')}" />
  </head>
  <body>
{header(base)}
    <main class="page">
      <nav class="breadcrumb" aria-label="Fil d'Ariane">{trail}</nav>
{body}
    </main>
{footer(cities, base, "")}
    <script>{RANKING_SCRIPT}</script>
{ANALYTICS}
  </body>
</html>
"""


RANKING_SCRIPT = """
document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => {
  const mode = button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach((b) => b.classList.toggle("active", b === button));
  document.querySelectorAll("#ranking tbody tr").forEach((row) => {
    row.hidden = mode && row.dataset.mode !== mode;
    const cell = row.querySelector(".rank");
    cell.textContent = mode ? row.dataset.modeRank : cell.dataset.rank;
  });
}));
const wanted = new URLSearchParams(location.search).get("mode");
if (wanted) document.querySelector(`[data-filter="${wanted}"]`)?.click();
if (location.hash) document.querySelector(location.hash)?.scrollIntoView();
document.querySelectorAll("[data-copy]").forEach((button) => button.addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(button.dataset.copy); button.textContent = "Lien copié ✓"; } catch {}
}));
"""


def method_section(data: list[dict], base: str, method: str) -> str:
    dates = ", ".join(f'{esc(city["city"])} ({french_date(city["weekday"])})' for city in sorted(data, key=lambda c: c["city"]))
    paris = (
        " À Paris, ces chiffres comptent le métro et le tram d'Île-de-France Mobilités, sans RER, Transilien, CDGVAL, "
        "Orlyval ni funiculaire de Montmartre."
        if any(city["slug"] == "paris" for city in data)
        else ""
    )
    return f"""      <section class="section" aria-labelledby="method-title">
        <h2 id="method-title">Méthode</h2>
        <p>{esc(method)} Ces chiffres ne reposent sur aucun calcul de trajet&nbsp;: ce sont des comptages directs dans les
        horaires théoriques publiés par chaque réseau (GTFS), pour un mardi ou un jeudi de semaine scolaire. Seuls le tram et
        le métro sont comptés (pas les bus, Busway, funiculaires ni téléphériques)&nbsp;; Rhônexpress et la navette OL
        Stadium sont exclus à Lyon.{paris} Jours utilisés&nbsp;: {dates}.</p>
        <p>Sources et licences&nbsp;: voir les <a href="{base}mentions-legales/">mentions légales</a>. Une erreur&nbsp;?
        <a href="{GITHUB_URL}/issues">Signalez-la sur GitHub</a>.</p>
      </section>"""


def render_rankings(cities: list[dict]) -> dict[str, str]:
    """Pages of the rankings: the hub (/classements/) and one page per ranking. Returns {path: html}."""
    data = load_rankings(cities)
    if not data:
        return {}
    hub_url = f"{SITE_URL}{RANKINGS_DIR}/"
    pages = {}

    hub_defs = ranking_definitions(data, "../")
    cards = "\n".join(
        f"""          <a class="ranking-card" href="./{d['slug']}/">
            <span class="ranking-card-question">{esc(d['question'])}</span>
            <ol>{"".join(f"<li><span>{MEDALS[i]}</span> <strong>{value}</strong> {label} · {sub}</li>" for i, (value, label, sub) in enumerate(d["podium"]))}</ol>
            <span class="ranking-card-link">Voir le classement complet →</span>
          </a>"""
        for d in hub_defs
    )
    highlights = "\n".join(
        f'          <div class="stat"><strong>{d["highlight"][0]}</strong><span>{d["highlight"][1]}</span></div>' for d in hub_defs
    )
    hub_description = (
        f"Le métro le plus fréquent, la station la plus desservie, la ligne la plus longue : les trams et métros de "
        f"{len(data)} villes françaises comparés à partir de leurs horaires officiels."
    )
    pages[f"{RANKINGS_DIR}/index.html"] = ranking_page(
        title="Les classements des trams et métros de France",
        description=hub_description,
        url=hub_url,
        base="../",
        image_name="classements.jpg",
        crumbs=[(SITE_NAME, SITE_URL), ("Classements", hub_url)],
        cities=cities,
        body=f"""      <section class="hero">
        <span class="chip">🏆 {len(data)} réseaux comparés</span>
        <h1>Les classements des trams et&nbsp;métros</h1>
        <p class="lede">Quel métro passe le plus souvent, quelle station voit défiler le plus de rames, quelle ligne est la
        plus longue à parcourir, où rentre-t-on le plus tard le samedi soir&nbsp;? Tous les chiffres viennent directement des
        horaires officiels des réseaux.</p>
        <div class="stat-grid ranking-highlights">
{highlights}
        </div>
      </section>
      <section aria-label="Les classements">
        <div class="ranking-cards">
{cards}
        </div>
      </section>
{method_section(data, "../", "Chaque classement détaille sa propre mesure.")}""",
    )

    defs = ranking_definitions(data, "../../")
    for d in defs:
        url = f"{hub_url}{d['slug']}/"
        podium = "\n".join(
            f'          <div class="podium-step step-{i + 1}"><span class="medal">{MEDALS[i]}</span><strong>{value}</strong>'
            f"<span>{label}</span><small>{sub}</small></div>"
            for i, (value, label, sub) in enumerate(d["podium"])
        )
        headers = "".join(f'<th scope="col">{esc(h)}</th>' for h in ["#", *d["headers"]])
        rows = d["rows"]
        ranks = competition_ranks([row["value"] for row in rows])
        mode_ranks = {}
        for mode in {row["mode"] for row in rows}:
            subset = [i for i, row in enumerate(rows) if row["mode"] == mode]
            for i, rank in zip(subset, competition_ranks([rows[i]["value"] for i in subset])):
                mode_ranks[i] = rank
        top = max(row["value"] for row in rows)
        seen = set()
        body_rows = []
        for i, row in enumerate(rows):
            cells = list(row["cells"])
            col = d["valueCol"]
            cells[col] = f'<span class="bar" style="width:{100 * row["value"] / top:.0f}%"></span><span class="bar-value">{cells[col]}</span>'
            anchor = f' id="{row["city"]}"' if row["city"] not in seen else ""
            seen.add(row["city"])
            mode = f' data-mode="{row["mode"]}" data-mode-rank="{mode_ranks[i]}"' if d.get("byMode") else ""
            body_rows.append(
                f'            <tr{anchor}{mode}><td class="rank" data-rank="{ranks[i]}">{ranks[i]}</td>'
                + "".join(f'<td{" class=\"bar-cell\"" if j == col else ""}>{cell}</td>' for j, cell in enumerate(cells))
                + "</tr>"
            )
        rows = "\n".join(body_rows)
        filters = (
            """        <div class="mode-filter" role="group" aria-label="Filtrer par mode">
          <button type="button" class="chip active" data-filter="">Tous</button>
          <button type="button" class="chip" data-filter="metro">Métro</button>
          <button type="button" class="chip" data-filter="tram">Tram</button>
        </div>
"""
            if d.get("byMode")
            else ""
        )
        page_url = f"{hub_url}{d['slug']}/"
        share_text = quote(f"{d['question']} Le classement des trams et métros de France")
        share = f"""        <p class="share">Partager&nbsp;:
          <a href="https://www.linkedin.com/sharing/share-offsite/?url={quote(page_url)}" rel="noopener">LinkedIn</a> ·
          <a href="https://x.com/intent/post?text={share_text}&amp;url={quote(page_url)}" rel="noopener">X</a> ·
          <a href="https://bsky.app/intent/compose?text={share_text}%20{quote(page_url)}" rel="noopener">Bluesky</a> ·
          <button type="button" class="link-button" data-copy="{page_url}">Copier le lien</button>
        </p>"""
        others = " · ".join(f'<a href="../{o["slug"]}/">{esc(o["short"])}</a>' for o in defs if o["slug"] != d["slug"])
        pages[f"{RANKINGS_DIR}/{d['slug']}/index.html"] = ranking_page(
            title=d["title"],
            description=f'{d["question"]} {d["intro"]}',
            url=url,
            base="../../",
            image_name=f"classement-{d['slug']}.jpg",
            crumbs=[(SITE_NAME, SITE_URL), ("Classements", hub_url), (d["short"], url)],
            cities=cities,
            body=f"""      <section class="hero">
        <span class="chip">🏆 Classement · {len(data)} réseaux</span>
        <h1>{esc(d["question"])}</h1>
        <p class="lede">{esc(d["intro"])}</p>
        <div class="podium">
{podium}
        </div>
      </section>
      <section class="section" aria-labelledby="table-title">
        <h2 id="table-title">{esc(d["title"])}</h2>
        <p>{esc(d["method"])}</p>
{filters}        <div class="table-scroll">
        <table class="lines-table ranking-table" id="ranking">
          <thead><tr>{headers}</tr></thead>
          <tbody>
{rows}
          </tbody>
        </table>
        </div>
{share}
        <p class="section-link">Les autres classements&nbsp;: {others} · <a href="../">tous les classements</a></p>
      </section>
{method_section(data, "../../", d["method"])}""",
        )
    return pages


def render_404(cities: list[dict]) -> str:
    links = "\n".join(f'          <a class="chip" href="/{city["path"]}">{esc(city["name"])}</a>' for city in cities)
    return f"""<!doctype html>
<html lang="fr">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Page introuvable · {SITE_NAME}</title>
    <meta name="robots" content="noindex" />
    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
    <link rel="stylesheet" href="https://fonts.bunny.net/css?family=inter:400,500,600,700,800" />
    <link rel="stylesheet" href="/styles.css?v={short_hash(SITE / 'styles.css')}" />
  </head>
  <body>
{header('/')}
    <main class="page">
      <section class="hero">
        <h1>Terminus&nbsp;!</h1>
        <p class="lede">Cette page n'existe pas. Choisissez une ville pour reprendre votre trajet.</p>
        <nav class="city-switch" aria-label="Villes">
{links}
        </nav>
      </section>
    </main>
  </body>
</html>
"""


def main() -> None:
    cities = load_built_cities()
    city_template = Template((ROOT / "templates" / "city.html").read_text(encoding="utf-8"))
    for city in cities:
        page = SITE / city["path"] / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(render_city(city_template, cities, city), encoding="utf-8")
        print(f"Wrote {page.relative_to(ROOT)}")

    home_template = Template((ROOT / "templates" / "home.html").read_text(encoding="utf-8"))
    (SITE / "index.html").write_text(render_home(home_template, cities), encoding="utf-8")
    (SITE / "404.html").write_text(render_404(cities), encoding="utf-8")
    rankings = render_rankings(cities)
    for relative, html_page in rankings.items():
        (SITE / relative).parent.mkdir(parents=True, exist_ok=True)
        (SITE / relative).write_text(html_page, encoding="utf-8")
    (SITE / "mentions-legales").mkdir(exist_ok=True)
    (SITE / "mentions-legales" / "index.html").write_text(render_legal(cities), encoding="utf-8")
    write_sources_readme(cities)
    print("Wrote site/index.html, site/404.html")

    today = date.today().isoformat()
    urls = [f"  <url><loc>{SITE_URL}</loc><lastmod>{today}</lastmod></url>"]
    urls += [f"  <url><loc>{SITE_URL}{relative.removesuffix('index.html')}</loc><lastmod>{today}</lastmod></url>" for relative in rankings]
    urls += [
        f"  <url><loc>{SITE_URL}{city['path']}</loc><lastmod>{city['sources']['builtAt'][:10]}</lastmod></url>" for city in cities
    ]
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n",
        encoding="utf-8",
    )
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n", encoding="utf-8")
    print("Wrote site/sitemap.xml, site/robots.txt")


if __name__ == "__main__":
    main()
