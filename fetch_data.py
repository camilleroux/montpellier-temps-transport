#!/usr/bin/env python3
"""Download the raw sources of a city into data/<city>/.

Usage: python3 fetch_data.py <city> [--gtfs-only | --context-only | --rivers-only | --rail-only]
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

from cities import load_city

ROOT = Path(__file__).resolve().parent
USER_AGENT = "tram.camilleroux.com/0.2 (build script)"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def bbox(values) -> str:
    return ",".join(str(v) for v in values)


def download(url: str, data: bytes | None = None) -> bytes:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def clip_gtfs(source: Path, target: Path, area) -> None:
    """National feeds (Switzerland: 4.5 GB unzipped) are cut down to the stops in `gtfsClip` and the trips calling at
    two of them, with their routes, services and transfers: a few tens of MB that build_data.py reads like any feed."""
    south, west, north, east = area
    csv.field_size_limit(sys.maxsize)

    def read(archive: zipfile.ZipFile, name: str):
        with archive.open(name) as handle:
            reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8-sig", newline=""))
            header = next(reader)
            yield header
            yield from reader

    def table(archive: zipfile.ZipFile, name: str, keep):
        rows = read(archive, name)
        header = next(rows)
        return header, [row for row in rows if keep(dict(zip(header, row)))]

    def write(out: zipfile.ZipFile, name: str, header, rows) -> None:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
        out.writestr(name, buffer.getvalue())

    with zipfile.ZipFile(source) as archive:
        stops_header, stops = table(archive, "stops.txt", lambda row: True)
        stop_col = stops_header.index("stop_id")
        lat, lon = stops_header.index("stop_lat"), stops_header.index("stop_lon")
        inside = {row[stop_col] for row in stops if south <= float(row[lat] or 0) <= north and west <= float(row[lon] or 0) <= east}

        rows = read(archive, "stop_times.txt")
        times_header = next(rows)
        trip_col, time_stop_col = times_header.index("trip_id"), times_header.index("stop_id")
        times, calls = [], Counter()
        for row in rows:
            if row[time_stop_col] in inside:
                times.append(row)
                calls[row[trip_col]] += 1
        kept_trips = {trip_id for trip_id, count in calls.items() if count >= 2}
        times = [row for row in times if row[trip_col] in kept_trips]
        used = {row[time_stop_col] for row in times}
        parent_col = stops_header.index("parent_station") if "parent_station" in stops_header else None
        used |= {row[parent_col] for row in stops if parent_col is not None and row[stop_col] in used and row[parent_col]}

        trips_header, trips = table(archive, "trips.txt", lambda row: row["trip_id"] in kept_trips)
        routes_kept = {row[trips_header.index("route_id")] for row in trips}
        services_kept = {row[trips_header.index("service_id")] for row in trips}
        routes_header, routes = table(archive, "routes.txt", lambda row: row["route_id"] in routes_kept)
        agencies = {row[routes_header.index("agency_id")] for row in routes}
        names = archive.namelist()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
            write(out, "stops.txt", stops_header, [row for row in stops if row[stop_col] in used])
            write(out, "stop_times.txt", times_header, times)
            write(out, "trips.txt", trips_header, trips)
            write(out, "routes.txt", routes_header, routes)
            write(out, "agency.txt", *table(archive, "agency.txt", lambda row: row["agency_id"] in agencies))
            for name in ("calendar.txt", "calendar_dates.txt"):
                if name in names:
                    write(out, name, *table(archive, name, lambda row: row["service_id"] in services_kept))
            if "transfers.txt" in names:
                write(out, "transfers.txt", *table(
                    archive, "transfers.txt", lambda row: row["from_stop_id"] in used and row["to_stop_id"] in used))
            if "feed_info.txt" in names:
                out.writestr("feed_info.txt", archive.read("feed_info.txt"))


def overpass(query: str) -> bytes:
    payload = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(6):
        for url in OVERPASS_URLS:
            try:
                body = download(url, payload)
                json.loads(body)
                return body
            except Exception as error:  # noqa: BLE001 - Overpass is often busy, just retry
                print(f"  {url} failed ({error}), retrying…")
        time.sleep(10 * (attempt + 1))
    raise RuntimeError("Overpass unavailable")


def record(out: Path, name: str, source: str, how: str = "download") -> None:
    """Note in data/<city>/manifest.json where each raw file comes from and when it was fetched."""
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    path = out / name
    fetched = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if how == "manual" else datetime.now(timezone.utc)
    manifest[name] = {
        "source": source,
        "how": how,
        "fetchedAt": fetched.isoformat(timespec="seconds"),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_context(city: dict, out: Path) -> None:
    """Land around the metropolis, for coastal cities: whatever is left uncovered on the map is drawn as sea."""
    departments = city.get("seaDepartments", [])
    if departments:
        print(f"Communes voisines (départements {', '.join(departments)})…")
        features = []
        urls = []
        for code in departments:
            url = f"https://geo.api.gouv.fr/departements/{code}/communes?fields=nom,code&format=geojson&geometry=contour"
            features += json.loads(download(url))["features"]
            urls.append(url)
        (out / "context.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
        record(out, "context.geojson", " + ".join(urls))
    relations = city.get("contextOsmRelations", [])
    if relations:
        print("Territoires voisins hors de France (OSM)…")
        query = "[out:json][timeout:110];(" + "".join(f"relation({rel});" for rel in relations) + ");out geom;"
        (out / "context_osm.json").write_bytes(overpass(query))
        record(out, "context_osm.json", f"Overpass API: {query}")


def fetch_rail(city: dict, out: Path) -> None:
    """Line geometries from OSM, for feeds without shapes. `osmBusRoutes` adds bus lines run like a tram
    (Strasbourg's BHNS G and H), picked by network since other operators reuse the same letters."""
    print("Tracés des lignes (OSM)…")
    area = bbox(city["osmRailBbox"])
    query = f'[out:json][timeout:110];(relation["route"~"^(tram|subway|light_rail|funicular)$"]({area});'
    buses = city.get("osmBusRoutes")
    if buses:
        refs = "|".join(re.escape(ref) for ref in buses["refs"])
        query += f'relation["route"="bus"]["network"="{buses["network"]}"]["ref"~"^({refs})$"]({area});'
    if city.get("osmTrainRoutes"):
        # Trains (Léman Express, IR…) cross the whole country: only their ways inside the area are kept.
        query += f'relation["route"="train"]({area});'
        query += f");out geom({area});"
    else:
        query += ");out geom;"
    (out / "osm_rail.json").write_bytes(overpass(query))
    record(out, "osm_rail.json", f"Overpass API: {query}")


def fetch_rivers(city: dict, out: Path) -> None:
    """Rivers crossed on foot only by a bridge (`"rivers"`: names, and their « La Loire - Bras de Pirmil » parts)."""
    if not city.get("rivers"):
        return
    print("Cours d'eau (OSM)…")
    names = "|".join(re.escape(name) for name in city["rivers"])
    query = (
        f'[out:json][timeout:110];way["waterway"="river"]["name"~"^({names})( - .*)?$"]["tunnel"!~"."]'
        f'({bbox(city["osmBbox"])});out geom;'
    )
    (out / "osm_rivers.json").write_bytes(overpass(query))
    record(out, "osm_rivers.json", f"Overpass API: {query}")
    # Bridges open to pedestrians: the ones over these rivers are kept by build_data.py.
    query = (
        '[out:json][timeout:110];way["bridge"]["highway"]'
        '["highway"!~"^(motorway|motorway_link|trunk|trunk_link|construction|proposed)$"]["foot"!="no"]'
        f'({bbox(city["osmBbox"])});out geom;'
    )
    (out / "osm_bridges.json").write_bytes(overpass(query))
    record(out, "osm_bridges.json", f"Overpass API: {query}")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    city = load_city(sys.argv[1])
    out = ROOT / "data" / city["slug"]
    out.mkdir(parents=True, exist_ok=True)
    if "--context-only" in sys.argv:
        fetch_context(city, out)
        return
    if "--rail-only" in sys.argv:
        fetch_rail(city, out)
        return
    if "--rivers-only" in sys.argv:
        fetch_rivers(city, out)
        return

    print(f"GTFS {city['network']}…")
    if city.get("gtfsManual"):
        # Some operators (TCL on data.grandlyon.com) require an account: the file is downloaded by hand.
        if not (out / "gtfs.zip").exists():
            sys.exit(f"Téléchargez le GTFS à la main ({city['gtfsManual']}) et posez-le dans {out / 'gtfs.zip'}")
        print(f"  fichier manuel conservé ({city['gtfsManual']})")
        record(out, "gtfs.zip", city["gtfsUrl"], how="manual")
    else:
        if city.get("gtfsClip"):
            full = out / "gtfs_full.zip"
            full.write_bytes(download(city["gtfsUrl"]))
            print("  découpage sur la zone de la ville…")
            clip_gtfs(full, out / "gtfs.zip", city["gtfsClip"])
            full.unlink()
        else:
            (out / "gtfs.zip").write_bytes(download(city["gtfsUrl"]))
        record(out, "gtfs.zip", city["gtfsUrl"])
    for extra in city.get("gtfsExtra", []):
        # Lines published in a feed of their own (the REM in Montréal), merged by build_data.py.
        print(f"GTFS {extra['network']}…")
        name = f"gtfs_{extra['slug']}.zip"
        (out / name).write_bytes(download(extra["url"]))
        record(out, name, extra["url"])
    if "--gtfs-only" in sys.argv:
        return

    print(f"Communes de {city['metropole']}…")
    if city.get("communesOsm"):
        # Outside France (no EPCI): the municipalities and boroughs are OSM administrative boundaries.
        query = "[out:json][timeout:110];(" + "".join(f"relation({rel});" for rel in city["communesOsm"]) + ");out geom;"
        (out / "communes_osm.json").write_bytes(overpass(query))
        record(out, "communes_osm.json", f"Overpass API: {query}")
    else:
        # Some networks serve several intercommunalities (Tisséo: Toulouse Métropole and four neighbours).
        epcis = city["epci"] if isinstance(city["epci"], list) else [city["epci"]]
        urls = [f"https://geo.api.gouv.fr/epcis/{epci}/communes?fields=nom,code&format=geojson&geometry=contour" for epci in epcis]
        features = [feature for url in urls for feature in json.loads(download(url))["features"]]
        (out / "communes.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
        record(out, "communes.geojson", " + ".join(urls))
    if city.get("arrondissements"):
        print("Arrondissements municipaux…")
        url = (f"https://geo.api.gouv.fr/communes?type=arrondissement-municipal&codeParent={city['arrondissements']}"
               "&fields=nom,code&format=geojson&geometry=contour")
        (out / "arrondissements.geojson").write_bytes(download(url))
        record(out, "arrondissements.geojson", url)

    if city.get("railGeometry") == "osm":
        fetch_rail(city, out)

    print("Eau et parcs (OSM)…")
    area, parks = bbox(city["osmBbox"]), bbox(city["parksBbox"])
    query = (
        "[out:json][timeout:180];("
        f'relation["natural"="water"]({area});'
        f'way["natural"="water"]({area});'
        f'relation["leisure"="park"]({parks});'
        f'way["leisure"="park"]({parks});'
        ");out geom;"
    )
    (out / "osm_water_parks.json").write_bytes(overpass(query))
    record(out, "osm_water_parks.json", f"Overpass API: {query}")
    fetch_rivers(city, out)
    fetch_context(city, out)


if __name__ == "__main__":
    main()
