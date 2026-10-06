#!/usr/bin/env python3
"""Build the compact JSON bundle of a city for the transit time map.

Usage: python3 build_data.py <city>   (reads data/<city>/, writes site/data/<city>.json)
"""

from __future__ import annotations

import csv
import heapq
import io
import json
import math
import re
import statistics
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from cities import load_city

ROOT = Path(__file__).resolve().parent

LAND_PAD_METERS = 1200.0
VIEW_PAD_METERS = 900.0

GRID_CELL_METERS = 200.0
# Walking speed (4.5 km/h), applied to straight-line distances: tram lines run along straight avenues.
WALK_METERS_PER_MINUTE = 75.0
CELL_NEAREST_STATIONS = 5
CELL_NEAREST_RAIL_STATIONS = 3
# Stations looked at around each cell before dropping the ones across a river.
CELL_CANDIDATES = 4
ORIGIN_NEAREST_STATIONS = 8  # stations reachable on foot from a departure point (also read by site/app.js)
DEFAULT_BOARD_WAIT = 5.0
TRANSFER_WALK = 1.5
INTER_COMPLEX_WALK_RADIUS = 450.0
STOP_GROUP_RADIUS = 350.0
MIN_RIDE_MINUTES = 0.4
MIN_WAIT = 1.0
MAX_WAIT = 15.0
# A next stop served by less than this share of the trains leaving in its direction is a branch.
BRANCH_SHARE = 0.9
# Daytime window used to measure headways and ride times (weekday, 7h–20h).
SERVICE_WINDOW = (7 * 3600, 20 * 3600)
MIN_RING_DISTANCE = 45.0
MIN_LINE_DISTANCE = 25.0
RIVER_POINT_DISTANCE = 20.0
RIVER_BUCKET_METERS = 500.0
# Longest walk through a bridge (40 min): beyond, walking is never the best way (also read by site/app.js).
MAX_BRIDGE_WALK_METERS = 3000.0
MIN_PARK_AREA = 20_000.0
MIN_WATER_AREA = 15_000.0
CONTEXT_RING_DISTANCE = 80.0
# Water bodies at least this large (or lagoons) are not land: no heatmap, no pins.
WATER_MASK_AREA = 1_000_000.0

# GTFS route_type → mode (basic and extended types).
RAIL_MODES = {"tram", "metro", "rer", "train", "funicular", "cable", "busway", "bhns"}
# Minutes to walk from the street to the platform (and back): stairs and corridors of underground lines.
MODE_ACCESS_MINUTES = {"metro": 1.0, "rer": 1.0, "train": 1.0, "funicular": 1.0, "cable": 1.0}
# Communes kept when a city config says "communes": "served": enough stops, and not too far from tram/metro.
SERVED_MIN_STOPS = 3
SERVED_MAX_RAIL_DISTANCE = 12_000.0
# Preview image (og): arrival at the rail station closest to this travel time from the centre.
OG_TRIP_MINUTES = 20
REFERENCE_HORIZON_DAYS = 60


def route_mode(route_type: str) -> str:
    value = int(route_type or 3)
    if value == 0 or 900 <= value < 1000:
        return "tram"
    if value in (1, 2) or 100 <= value < 200 or 400 <= value < 500:
        return "metro"
    if value == 7 or 1400 <= value < 1500:
        return "funicular"
    if value in (5, 6) or 1300 <= value < 1400:
        return "cable"
    if value == 4 or 1000 <= value < 1300:
        return "ferry"
    return "bus"


Point = Tuple[float, float]
Ring = List[Point]
Polygon = List[Ring]
MultiPolygon = List[Polygon]

LAT0 = 0.0  # set from the city config in main()


def lonlat_to_xy(lon: float, lat: float) -> Point:
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = meters_per_deg_lat * math.cos(math.radians(LAT0))
    return lon * meters_per_deg_lon, lat * meters_per_deg_lat


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def round_point(point: Point) -> List[float]:
    return [round(point[0], 1), round(point[1], 1)]


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def ring_area(ring: Sequence[Point]) -> float:
    area = 0.0
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def polygon_centroid(ring: Sequence[Point]) -> Point:
    area = ring_area(ring) or 1.0
    factor = 1.0 / (6.0 * area)
    cx = cy = 0.0
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return cx * factor, cy * factor


def simplify_polyline(points: Sequence[Point], min_distance: float) -> List[Point]:
    if len(points) <= 2:
        return list(points)
    simplified = [points[0]]
    for point in points[1:-1]:
        if dist(point, simplified[-1]) >= min_distance:
            simplified.append(point)
    if points[-1] != simplified[-1]:
        simplified.append(points[-1])
    return simplified


def simplify_ring(ring: Sequence[Point], min_distance: float) -> Ring:
    if len(ring) <= 4:
        return list(ring)
    core = list(ring[:-1]) if ring[0] == ring[-1] else list(ring)
    simplified = [core[0]]
    for point in core[1:]:
        if dist(point, simplified[-1]) >= min_distance:
            simplified.append(point)
    if len(simplified) < 3:
        simplified = core[:3]
    simplified.append(simplified[0])
    return simplified


def point_in_ring(point: Point, ring: Sequence[Point]) -> bool:
    x, y = point
    inside = False
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        intersects = (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
        if intersects:
            inside = not inside
    return inside


def ring_bounds(ring: Sequence[Point]) -> Tuple[float, float, float, float]:
    xs = [x for x, _ in ring]
    ys = [y for _, y in ring]
    return min(xs), min(ys), max(xs), max(ys)


class PolygonSet:
    """Point-in-polygon tests with a bounding-box pre-check (big rivers have thousands of vertices)."""

    def __init__(self, polygons: MultiPolygon):
        self.items = [(ring_bounds(polygon[0]), polygon) for polygon in polygons if polygon and len(polygon[0]) >= 4]

    def contains(self, point: Point) -> bool:
        x, y = point
        for (min_x, min_y, max_x, max_y), polygon in self.items:
            if x < min_x or x > max_x or y < min_y or y > max_y:
                continue
            if point_in_ring(point, polygon[0]) and not any(point_in_ring(point, hole) for hole in polygon[1:]):
                return True
        return False


def multipolygon_bounds(multipolygon: MultiPolygon, pad: float) -> Tuple[float, float, float, float]:
    xs = [x for polygon in multipolygon for ring in polygon for x, _ in ring]
    ys = [y for polygon in multipolygon for ring in polygon for _, y in ring]
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def coords_to_polygons(geometry: dict, min_distance: float = MIN_RING_DISTANCE) -> MultiPolygon:
    geom_type = geometry["type"]
    coords = geometry["coordinates"]
    if geom_type == "Polygon":
        polygons = [coords]
    elif geom_type == "MultiPolygon":
        polygons = coords
    else:
        return []
    converted: MultiPolygon = []
    for rings in polygons:
        polygon: Polygon = []
        for ring in rings:
            points = [lonlat_to_xy(lon, lat) for lon, lat in ring]
            if len(points) < 4:
                continue
            if points[0] != points[-1]:
                points.append(points[0])
            polygon.append(simplify_ring(points, min_distance))
        if polygon:
            converted.append(polygon)
    return converted


def serialize_polygon(polygon: Polygon) -> List[List[List[float]]]:
    return [[round_point(point) for point in ring] for ring in polygon]


class StationIndex:
    """Bucket grid for nearest-station queries (cities have thousands of stops)."""

    def __init__(self, points: Sequence[Point], indexes: Sequence[int], size: float = 800.0):
        self.size = size
        self.points = points
        self.buckets: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for index in indexes:
            x, y = points[index]
            self.buckets[(int(x // size), int(y // size))].append(index)
        self.empty = not indexes

    def nearest(self, point: Point, count: int, max_rings: int = 40) -> List[Tuple[float, int]]:
        if self.empty:
            return []
        cx, cy = int(point[0] // self.size), int(point[1] // self.size)
        found: List[Tuple[float, int]] = []
        for ring in range(max_rings + 1):
            for gx in range(cx - ring, cx + ring + 1):
                for gy in range(cy - ring, cy + ring + 1):
                    if max(abs(gx - cx), abs(gy - cy)) != ring:
                        continue
                    for index in self.buckets.get((gx, gy), ()):
                        found.append((dist(point, self.points[index]), index))
            found.sort()
            # Every station closer than `ring * size` is already found.
            if len(found) >= count and found[count - 1][0] <= ring * self.size:
                break
        return found[:count]

    def within(self, point: Point, radius: float) -> List[int]:
        reach = int(radius // self.size) + 1
        cx, cy = int(point[0] // self.size), int(point[1] // self.size)
        return [
            index
            for gx in range(cx - reach, cx + reach + 1)
            for gy in range(cy - reach, cy + reach + 1)
            for index in self.buckets.get((gx, gy), ())
            if dist(point, self.points[index]) <= radius
        ]


class Rivers:
    """Big rivers are crossed on foot only by a bridge: a walk whose straight line cuts one goes through the best
    bridge instead (one bridge at most: an island is reached by its own stops). Same rule as site/app.js."""

    def __init__(self, lines: Sequence[Sequence[Point]], bridges: Sequence[Tuple[Point, Point, float]] = ()):
        self.buckets: Dict[Tuple[int, int], List[Tuple[Point, Point]]] = defaultdict(list)
        for line in lines:
            for a, b in zip(line, line[1:]):
                for key in self._keys(a, b):
                    self.buckets[key].append((a, b))
        self.bridges = list(bridges)

    @staticmethod
    def _keys(a: Point, b: Point):
        size = RIVER_BUCKET_METERS
        for gx in range(int(min(a[0], b[0]) // size), int(max(a[0], b[0]) // size) + 1):
            for gy in range(int(min(a[1], b[1]) // size), int(max(a[1], b[1]) // size) + 1):
                yield gx, gy

    def crosses(self, a: Point, b: Point) -> bool:
        if not self.buckets:
            return False
        for key in self._keys(a, b):
            for c, d in self.buckets.get(key, ()):
                if segments_cross(a, b, c, d):
                    return True
        return False

    def walk(self, a: Point, b: Point) -> float:
        """Walking distance in meters: straight, or through a bridge; infinite without one."""
        if not self.crosses(a, b):
            return dist(a, b)
        # Shortest detour first: the first bridge whose two legs stay on their bank is the best one.
        detours = sorted(
            (dist(a, near) + length + dist(far, b), near, far)
            for end_a, end_b, length in self.bridges
            for near, far in ((end_a, end_b), (end_b, end_a))
        )
        for meters, near, far in detours:
            if meters > MAX_BRIDGE_WALK_METERS:
                break
            if not self.crosses(a, near) and not self.crosses(far, b):
                return meters
        return math.inf


def segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    def side(p: Point, q: Point, r: Point) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


def extract_rivers(data_dir: Path, city: dict) -> Tuple[List[List[Point]], List[Tuple[Point, Point, float]]]:
    """River lines, and the bridges over them: (one end, other end, length)."""
    if not city.get("rivers"):
        return [], []
    lines = []
    for element in load_json(data_dir / "osm_rivers.json")["elements"]:
        points = way_points(element.get("geometry", []))
        if len(points) >= 2:
            lines.append(simplify_polyline(points, RIVER_POINT_DISTANCE))
    rivers = Rivers(lines)
    bridges: Dict[Tuple[int, int], Tuple[Point, Point, float]] = {}
    for element in load_json(data_dir / "osm_bridges.json")["elements"]:
        points = way_points(element.get("geometry", []))
        if len(points) < 2 or not any(rivers.crosses(a, b) for a, b in zip(points, points[1:])):
            continue
        length = sum(dist(a, b) for a, b in zip(points, points[1:]))
        # Carriageways and sidewalks of the same bridge are separate ways: one per 40 m.
        middle = ((points[0][0] + points[-1][0]) / 2, (points[0][1] + points[-1][1]) / 2)
        bridges.setdefault((round(middle[0] / 40), round(middle[1] / 40)), (points[0], points[-1], length))
    return lines, list(bridges.values())


# --- Land, water, parks -----------------------------------------------------


def served_communes(features: Sequence[Tuple[str, MultiPolygon]], stations: Sequence[dict]) -> set:
    """Communes with a few stops of the network and a tram/metro station within reach: big intercommunalities
    (Grand Reims, Nice Côte d'Azur…) reach far beyond their urban network."""
    rail_points = [station["point"] for station in stations if station["rail"]]
    names = set()
    for name, polygons in features:
        polygon_set = PolygonSet(polygons)
        inside = [station["point"] for station in stations if polygon_set.contains(station["point"])]
        if len(inside) < SERVED_MIN_STOPS:
            continue
        if min(dist(point, rail) for point in inside for rail in rail_points) <= SERVED_MAX_RAIL_DISTANCE:
            names.add(name)
    return names


def extract_arrondissements(data_dir: Path, city: dict) -> List[dict]:
    """Paris, Lyon, Marseille: the arrondissements are drawn inside the commune, so that the outlying ones show up.
    Only their outlines and names: the land stays the commune's, whose contour also covers the harbour basins."""
    if not city.get("arrondissements"):
        return []
    arrondissements = []
    for feature in sorted(load_json(data_dir / "arrondissements.geojson")["features"], key=lambda f: f["properties"]["code"]):
        polygons = coords_to_polygons(feature["geometry"])
        if not polygons:
            continue
        # « Marseille 15e Arrondissement » → « Marseille 15ᵉ », and « 15ᵉ » on the map.
        commune, number = re.fullmatch(r"(.+) (\d+)(?:er|e) Arrondissement", feature["properties"]["nom"]).groups()
        short = number + ("ᵉʳ" if number == "1" else "ᵉ")
        largest = max((polygon[0] for polygon in polygons), key=lambda ring: abs(ring_area(ring)))
        arrondissements.append(
            {
                "name": f"{commune} {short}",
                "short": short,
                "polygons": [serialize_polygon(polygon) for polygon in polygons],
                "outline": [[round_point(point) for point in polygon[0]] for polygon in polygons],
                "label": round_point(polygon_centroid(largest)),
            }
        )
    return arrondissements


def load_communes(data_dir: Path) -> List[Tuple[str, MultiPolygon]]:
    """(name, polygons) of each commune: from geo.api.gouv.fr in France, from OSM boundaries elsewhere (Montréal)."""
    if (data_dir / "communes_osm.json").exists():
        return [
            # French name first: « Bruxelles », not the bilingual « Bruxelles - Brussel ».
            (element["tags"].get("name:fr") or element["tags"]["name"], [[simplify_ring(ring, MIN_RING_DISTANCE) for ring in polygon] for polygon in osm_polygons(element)])
            for element in load_json(data_dir / "communes_osm.json")["elements"]
        ]
    return [(feature["properties"]["nom"], coords_to_polygons(feature["geometry"])) for feature in load_json(data_dir / "communes.geojson")["features"]]


def extract_communes(data_dir: Path, city: dict, stations: Sequence[dict]) -> Tuple[List[dict], MultiPolygon]:
    features = load_communes(data_dir)
    # Some metropolises are far larger than their urban network (Aix-Marseille-Provence): keep only the listed
    # communes, or the ones actually served.
    wanted = served_communes(features, stations) if city.get("communes") == "served" else set(city.get("communes", []))
    communes = []
    all_polygons: MultiPolygon = []
    for name, polygons in sorted(features, key=lambda feature: feature[0]):
        if (wanted and name not in wanted) or not polygons:
            continue
        largest = max((polygon[0] for polygon in polygons), key=lambda ring: abs(ring_area(ring)))
        communes.append(
            {
                "name": name,
                "polygons": [serialize_polygon(polygon) for polygon in polygons],
                "outline": [[round_point(point) for point in polygon[0]] for polygon in polygons],
                "label": round_point(polygon_centroid(largest)),
            }
        )
        all_polygons.extend(polygons)
    return communes, all_polygons


def way_points(geometry: Sequence[dict]) -> List[Point]:
    return [lonlat_to_xy(node["lon"], node["lat"]) for node in geometry if node]


def assemble_rings(ways: List[List[Point]]) -> List[Ring]:
    """Join open ways end to end into closed rings (OSM multipolygon members)."""
    rings: List[Ring] = []
    pending = [list(way) for way in ways if len(way) >= 2]
    while pending:
        ring = pending.pop()
        while ring[0] != ring[-1]:
            for i, way in enumerate(pending):
                if way[0] == ring[-1]:
                    ring.extend(way[1:])
                elif way[-1] == ring[-1]:
                    ring.extend(reversed(way[:-1]))
                elif way[-1] == ring[0]:
                    ring[:0] = way[:-1]
                elif way[0] == ring[0]:
                    ring[:0] = list(reversed(way[1:]))
                else:
                    continue
                pending.pop(i)
                break
            else:
                break  # cut by the query bounding box: drop it
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings


def osm_polygons(element: dict) -> MultiPolygon:
    if element["type"] == "way":
        points = way_points(element.get("geometry") or [])
        return [[points]] if len(points) >= 4 and points[0] == points[-1] else []
    members = [m for m in element.get("members", []) if m["type"] == "way" and m.get("geometry")]
    outers = assemble_rings([way_points(m["geometry"]) for m in members if m.get("role") != "inner"])
    inners = assemble_rings([way_points(m["geometry"]) for m in members if m.get("role") == "inner"])
    polygons: MultiPolygon = []
    for outer in outers:
        holes = [inner for inner in inners if point_in_ring(inner[0], outer)]
        polygons.append([outer, *holes])
    return polygons


def extract_water_and_parks(data_dir: Path, bounds) -> Tuple[MultiPolygon, MultiPolygon, MultiPolygon]:
    """Return (water that is not land, other water shown on the map, parks)."""
    payload = load_json(data_dir / "osm_water_parks.json")
    min_x, min_y, max_x, max_y = bounds
    masked: MultiPolygon = []
    water: MultiPolygon = []
    parks: MultiPolygon = []
    for element in payload["elements"]:
        tags = element.get("tags", {})
        for polygon in osm_polygons(element):
            ring_min_x, ring_min_y, ring_max_x, ring_max_y = ring_bounds(polygon[0])
            if ring_max_x < min_x or ring_min_x > max_x or ring_max_y < min_y or ring_min_y > max_y:
                continue
            area = abs(ring_area(polygon[0]))
            if tags.get("natural") == "water":
                if area < MIN_WATER_AREA:
                    continue
                tolerance = MIN_RING_DISTANCE if area > 1e6 else 12.0
                simplified = [simplify_ring(ring, tolerance) for ring in polygon]
                target = masked if tags.get("water") == "lagoon" or area >= WATER_MASK_AREA else water
                target.append(simplified)
            elif tags.get("leisure") == "park" and area >= MIN_PARK_AREA:
                parks.append([simplify_ring(ring, 15.0) for ring in polygon])
    return masked, water, parks


def extract_context(data_dir: Path, city: dict) -> MultiPolygon:
    """Land around a coastal metropolis (neighbouring communes, foreign territories such as Monaco), within the OSM
    area of the city: the map draws it as land so that what remains uncovered reads as sea."""
    south, west, north, east = city["osmBbox"]
    min_x, min_y = lonlat_to_xy(west, south)
    max_x, max_y = lonlat_to_xy(east, north)
    polygons: MultiPolygon = []
    if (data_dir / "context.geojson").exists():
        for feature in load_json(data_dir / "context.geojson")["features"]:
            polygons += coords_to_polygons(feature["geometry"], CONTEXT_RING_DISTANCE)
    if (data_dir / "context_osm.json").exists():
        for element in load_json(data_dir / "context_osm.json")["elements"]:
            polygons += [[simplify_ring(ring, CONTEXT_RING_DISTANCE) for ring in polygon] for polygon in osm_polygons(element)]
    kept = []
    for polygon in polygons:
        ring_min_x, ring_min_y, ring_max_x, ring_max_y = ring_bounds(polygon[0])
        if ring_max_x >= min_x and ring_min_x <= max_x and ring_max_y >= min_y and ring_min_y <= max_y:
            kept.append(polygon)
    return kept


# --- GTFS -------------------------------------------------------------------


# Tables merged from a city's extra feeds (`gtfsExtra`: the REM next to the STM), and their identifier columns,
# prefixed with the feed's slug so that they cannot collide with the main feed's.
MERGED_TABLES = ("agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "shapes.txt", "calendar.txt", "calendar_dates.txt")
GTFS_ID_COLUMNS = {"route_id", "agency_id", "trip_id", "service_id", "stop_id", "parent_station", "shape_id"}


def gtfs_path(data_dir: Path, city: dict) -> Path:
    """The city's GTFS. With extra feeds, a gtfs_merged.zip (rebuilt when a source changes): the main feed copied as
    is, then the rows of each extra feed, in the main feed's columns."""
    extras = city.get("gtfsExtra", [])
    if not extras:
        return data_dir / "gtfs.zip"
    sources = [data_dir / "gtfs.zip", *(data_dir / f"gtfs_{extra['slug']}.zip" for extra in extras)]
    merged = data_dir / "gtfs_merged.zip"
    if merged.exists() and all(merged.stat().st_mtime >= source.stat().st_mtime for source in sources):
        return merged
    print(f"Fusion des GTFS ({', '.join(source.name for source in sources)})…")
    archives = [zipfile.ZipFile(source) for source in sources]
    main, others = archives[0], list(zip(extras, archives[1:]))
    with zipfile.ZipFile(merged, "w", zipfile.ZIP_DEFLATED) as out:
        for name in main.namelist():
            with main.open(name) as source, out.open(name, "w", force_zip64=True) as target:
                last = b"\n"
                while chunk := source.read(1 << 20):
                    target.write(chunk)
                    last = chunk[-1:]
                if name not in MERGED_TABLES:
                    continue
                if last != b"\n":
                    target.write(b"\n")
                with main.open(name) as header_source:
                    header = next(csv.reader(io.TextIOWrapper(header_source, encoding="utf-8-sig")))
                text = io.TextIOWrapper(target, encoding="utf-8", newline="")
                writer = csv.writer(text, lineterminator="\n")
                for extra, archive in others:
                    for row in read_gtfs_table(archive, name):
                        writer.writerow(
                            f"{extra['slug']}:{row[column]}" if column in GTFS_ID_COLUMNS and row.get(column) else row.get(column, "")
                            for column in header
                        )
                text.flush()
                text.detach()
    for archive in archives:
        archive.close()
    return merged


def read_gtfs_table(archive: zipfile.ZipFile, name: str) -> Iterable[dict]:
    if name not in archive.namelist():
        return
    with archive.open(name) as handle:
        yield from csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig"))


def parse_time(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.strip().split(":"))
    return hours * 3600 + minutes * 60 + seconds


def parse_date(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def services_by_date(calendar: Sequence[dict], calendar_dates: Sequence[dict]) -> Dict[date, frozenset]:
    """Active services for every day of the feed (calendar.txt rules + calendar_dates.txt exceptions)."""
    active: Dict[date, set] = defaultdict(set)
    for row in calendar:
        day, end = parse_date(row["start_date"]), parse_date(row["end_date"])
        while day <= end:
            if row[WEEKDAYS[day.weekday()]] == "1":
                active[day].add(row["service_id"])
            day += timedelta(days=1)
    for row in calendar_dates:
        day = parse_date(row["date"])
        if row["exception_type"] == "1":
            active[day].add(row["service_id"])
        else:
            active[day].discard(row["service_id"])
    return {day: frozenset(services) for day, services in active.items()}


def pick_reference_date(services: Dict[date, frozenset], trips_per_service: Counter) -> date:
    """A plain school-term Tuesday or Thursday: the most common set of services among those days.

    Picking the busiest day instead would favour holidays with works and substitution buses. Days
    with a thinner timetable are left out: holidays, and feeds that run months ahead with only a few lines filled in.
    """
    weekdays = sorted(day for day, active in services.items() if day.weekday() in (1, 3) and active)
    upcoming = [day for day in weekdays if day >= date.today()] or weekdays
    # Stay close to today when the feed allows it: a date months ahead reads oddly on the page.
    soon = [day for day in upcoming if day <= upcoming[0] + timedelta(days=REFERENCE_HORIZON_DAYS)]
    upcoming = soon if len(soon) >= 4 else upcoming
    volume = {day: sum(trips_per_service[service] for service in services[day]) for day in upcoming}
    busiest = max(volume.values())
    # School holidays typically run 10–20 % fewer trips: only near-busiest days are plain term days.
    candidates = [day for day in upcoming if volume[day] >= 0.92 * busiest]
    signatures = Counter(services[day] for day in candidates)
    typical = signatures.most_common(1)[0][0]
    return next(day for day in candidates if services[day] == typical)


SMALL_WORDS = {"de", "du", "des", "la", "le", "les", "et", "en", "sur", "sous", "aux", "au", "à"}
ACRONYMS = {"TGV", "SNCF", "CHU", "CHR", "IUT", "ZI", "ZA", "ZAC", "RN", "RD", "TER", "UFR", "INSA", "EDF", "CPAM", "IME", "MEETT"}


def display_name(name: str) -> str:
    """Some feeds write stop names in capitals (« LYCEE S. WEIL »): turn them into title case for display."""
    letters = [c for c in name if c.isalpha()]
    if len(letters) < 4 or not all(c.isupper() for c in letters):
        return name
    words = []
    for i, word in enumerate(name.lower().split(" ")):
        if word.upper().strip(".,") in ACRONYMS:
            words.append(word.upper())
        elif i and word in SMALL_WORDS:
            words.append(word)
        elif word[:2] in ("d'", "l'") and len(word) > 2:
            words.append(word[:2] + word[2:3].upper() + word[3:])
        else:
            words.append("-".join(part[:1].upper() + part[1:] for part in word.split("-")))
    return " ".join(words)


def normalize_name(name: str) -> str:
    return " ".join(name.lower().replace("-", " ").replace("’", "'").split())


def group_stops(stops: Dict[str, dict], used_stop_ids: set) -> Tuple[List[dict], Dict[str, int]]:
    """Merge stops sharing a name and lying close together into one complex."""
    by_name: Dict[str, List[str]] = defaultdict(list)
    for stop_id in used_stop_ids:
        by_name[normalize_name(stops[stop_id]["stop_name"])].append(stop_id)

    complexes: List[dict] = []
    complex_of: Dict[str, int] = {}
    for _, stop_ids in sorted(by_name.items()):
        points = {stop_id: lonlat_to_xy(float(stops[stop_id]["stop_lon"]), float(stops[stop_id]["stop_lat"])) for stop_id in stop_ids}
        clusters: List[List[str]] = []
        for stop_id in sorted(stop_ids):
            merged = None
            for cluster in clusters:
                if any(dist(points[stop_id], points[other]) <= STOP_GROUP_RADIUS for other in cluster):
                    if merged is None:
                        cluster.append(stop_id)
                        merged = cluster
                    else:
                        merged.extend(cluster)
                        cluster.clear()
            clusters = [cluster for cluster in clusters if cluster]
            if merged is None:
                clusters.append([stop_id])
        for cluster in clusters:
            xs = [points[stop_id][0] for stop_id in cluster]
            ys = [points[stop_id][1] for stop_id in cluster]
            index = len(complexes)
            names = Counter(stops[stop_id]["stop_name"] for stop_id in cluster)
            complexes.append(
                {
                    "id": min(cluster),
                    "name": display_name(names.most_common(1)[0][0]),
                    "point": (sum(xs) / len(xs), sum(ys) / len(ys)),
                    "routes": set(),
                }
            )
            for stop_id in cluster:
                complex_of[stop_id] = index
    return complexes, complex_of


def read_stop_times(archive: zipfile.ZipFile, trips: Dict[str, dict]) -> Dict[str, List[Tuple[int, str, int, int]]]:
    """Stream stop_times.txt (hundreds of MB for big networks), keeping only the reference day's trips."""
    stop_times: Dict[str, List[Tuple[int, str, int, int]]] = defaultdict(list)
    with archive.open("stop_times.txt") as handle:
        reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8-sig"))
        header = next(reader)
        trip_col, seq_col, stop_col = header.index("trip_id"), header.index("stop_sequence"), header.index("stop_id")
        arr_col, dep_col = header.index("arrival_time"), header.index("departure_time")
        for row in reader:
            trip_id = row[trip_col]
            if trip_id not in trips or not row[arr_col]:
                continue
            stop_times[trip_id].append((int(row[seq_col]), row[stop_col], parse_time(row[arr_col]), parse_time(row[dep_col])))
    return stop_times


def route_excluded(row: dict, city: dict) -> bool:
    """School buses (extended types 712/713) are not open to the public; some cities exclude special lines.
    Regional aggregated feeds (Caen in the Normandy one) are narrowed to the city's operator."""
    agencies = city.get("agencies")
    return (
        row.get("route_type") in ("712", "713")
        or row.get("route_type") in city.get("excludeRouteTypes", [])
        or row.get("route_short_name") in city.get("excludeRouteNames", [])
        or row["route_id"] in city.get("excludeRoutes", [])
        or bool(agencies and row.get("agency_id") not in agencies)
    )


def extract_network(data_dir: Path, city: dict):
    # Some feeds mislabel their lines (Reims declares its tram as a metro); configs fix them by short name, or by
    # route_id when the name is ambiguous (the RER A of Île-de-France and the A buses of its suburbs).
    mode_overrides = city.get("routeModes", {})
    with zipfile.ZipFile(gtfs_path(data_dir, city)) as archive:
        routes = {row["route_id"]: row for row in read_gtfs_table(archive, "routes.txt")}
        excluded = {route_id for route_id, row in routes.items() if route_excluded(row, city)}
        stops = {row["stop_id"]: row for row in read_gtfs_table(archive, "stops.txt")}
        if city.get("stopNameLanguage"):
            # STIB writes « TRONE » in stops.txt and « Trône » in its French translations.
            names = {
                row["field_value"]: row["translation"]
                for row in read_gtfs_table(archive, "translations.txt")
                if row.get("table_name") == "stops" and row.get("field_name") == "stop_name"
                and row.get("language") == city["stopNameLanguage"] and row.get("field_value")
            }
            for row in stops.values():
                row["stop_name"] = names.get(row["stop_name"], row["stop_name"])
        # Names cleaned up by the config before stops are grouped by name: STM writes « Station Montmorency -Zone B »,
        # the REM one stop per platform (« Station Gare Centrale - Quai 1 »).
        for pattern, replacement in city.get("stopNameRewrites", []):
            for row in stops.values():
                row["stop_name"] = re.sub(pattern, replacement, row["stop_name"])
        services = services_by_date(list(read_gtfs_table(archive, "calendar.txt")), list(read_gtfs_table(archive, "calendar_dates.txt")))
        # Demand-responsive trips (TaM flags them in a "TAD" column) cannot be modelled with fixed times.
        all_trips = [
            row
            for row in read_gtfs_table(archive, "trips.txt")
            if not (row.get("TAD") or "").strip() and row["route_id"] not in excluded
        ]
        # Services of one line published as separate routes (the REM: Deux-Montagnes, Anse-à-l'Orme and Bois-Franc
        # trains, all to Brossard): one route, so that waits on the shared trunk count every train.
        for row in all_trips:
            row["route_id"] = city.get("mergeRoutes", {}).get(row["route_id"], row["route_id"])
        reference_date = pick_reference_date(services, Counter(row["service_id"] for row in all_trips))
        active_services = services[reference_date]
        trips = {row["trip_id"]: row for row in all_trips if row["service_id"] in active_services}
        stop_times = read_stop_times(archive, trips)
        transfer_rows = list(read_gtfs_table(archive, "transfers.txt")) if city.get("transferTimes") == "gtfs" else []

    if city.get("stopsBbox"):
        # Regional feeds (Île-de-France) run far beyond the map: the RER reaches Creil. Trips are cut at the edge.
        south, west, north, east = city["stopsBbox"]
        inside = {
            stop_id for stop_id, row in stops.items()
            if south <= float(row["stop_lat"] or 0) <= north and west <= float(row["stop_lon"] or 0) <= east
        }
        stop_times = {
            trip_id: kept for trip_id, sequence in stop_times.items()
            if len(kept := [entry for entry in sequence if entry[1] in inside]) >= 2
        }

    used_stop_ids = {stop_id for sequence in stop_times.values() for _, stop_id, _, _ in sequence}
    complexes, complex_of = group_stops(stops, used_stop_ids)

    ride_samples: Dict[Tuple[int, int, str], List[float]] = defaultdict(list)
    departures: Dict[Tuple[int, str], Counter] = defaultdict(Counter)
    # Departures towards each next stop, and the stops trains come from before each ride: branches (line 13, RER A).
    next_departures: Dict[Tuple[int, str], Counter] = defaultdict(Counter)
    previous: Dict[Tuple[int, int, str], set] = defaultdict(set)
    stop_routes: Dict[str, set] = defaultdict(set)
    window_start, window_end = SERVICE_WINDOW
    for trip_id, sequence in stop_times.items():
        trip = trips[trip_id]
        route_id = trip["route_id"]
        sequence.sort()
        before = None
        for (_, stop_a, _, dep_a), (_, stop_b, arr_b, _) in zip(sequence, sequence[1:]):
            a, b = complex_of[stop_a], complex_of[stop_b]
            complexes[a]["routes"].add(route_id)
            complexes[b]["routes"].add(route_id)
            stop_routes[stop_a].add(route_id)
            stop_routes[stop_b].add(route_id)
            if a == b:
                continue
            if before is not None:
                previous[(a, b, route_id)].add(before)
            before = a
            if not window_start <= dep_a < window_end:
                continue
            ride_samples[(a, b, route_id)].append(max(0, arr_b - dep_a) / 60.0)
            departures[(a, route_id)][trip.get("direction_id") or "0"] += 1
            next_departures[(a, route_id)][b] += 1

    window_minutes = (window_end - window_start) / 60.0

    def half_headway(departures_count: float) -> float:
        return min(MAX_WAIT, max(MIN_WAIT, window_minutes / departures_count / 2.0))

    waits: Dict[Tuple[int, str], float] = {}
    for key, per_direction in departures.items():
        waits[key] = round(half_headway(sum(per_direction.values()) / len(per_direction)), 2)

    def branch_wait(a: int, b: int, route_id: str) -> float:
        """Where a line splits, only one train in two (or fewer) goes down each branch: boarding on the trunk for a
        branch means waiting for that branch. The extra wait is added to the ride towards the first stop of the branch."""
        towards = next_departures[(a, route_id)]
        onward = sum(count for stop, count in towards.items() if stop not in previous[(a, b, route_id)])
        if towards[b] >= BRANCH_SHARE * onward:
            return 0.0
        extra = half_headway(towards[b]) - half_headway(onward)
        return round(extra, 2) if extra >= 0.1 else 0.0

    transfer_times = gtfs_transfer_times(transfer_rows, stop_routes, complex_of)

    served = {route_id for station in complexes for route_id in station["routes"]}
    route_info = {}
    for route_id in sorted(served):  # sorted: identical output from one build to the next
        row = routes[route_id]
        mode = mode_overrides.get(route_id) or mode_overrides.get(row.get("route_short_name", ""), route_mode(row.get("route_type", "3")))
        route_info[route_id] = {
            "mode": mode,
            "rail": mode in RAIL_MODES,
            # Some feeds give every line one colour (TEC: all of Charleroi's metro in yellow); configs restore them by name.
            "color": city.get("routeColors", {}).get(row.get("route_short_name", ""))
            or f"#{(row.get('route_color') or '888888').strip().lstrip('#') or '888888'}",
            "name": city.get("routeNames", {}).get(route_id) or row.get("route_short_name") or row.get("route_long_name") or route_id,
        }
    # Each ride: (median minutes, extra wait for a branch). Only guided lines split into real branches: bus
    # variants part and merge again all along their route, and charging each split would add up to absurd waits.
    edges = {
        (a, b, route_id): (
            max(MIN_RIDE_MINUTES, statistics.median(samples)),
            branch_wait(a, b, route_id) if route_info[route_id]["rail"] else 0.0,
        )
        for (a, b, route_id), samples in ride_samples.items()
    }
    rail_shape_ids = {
        trip["shape_id"] for trip in trips.values() if route_info.get(trip["route_id"], {}).get("rail") and trip.get("shape_id")
    }
    shape_routes = {trip["shape_id"]: trip["route_id"] for trip in trips.values() if trip.get("shape_id") in rail_shape_ids}
    return reference_date, complexes, edges, waits, transfer_times, route_info, shape_routes


def gtfs_transfer_times(rows: Sequence[dict], stop_routes: Dict[str, set], complex_of: Dict[str, int]) -> Dict[Tuple[int, str, int, str], float]:
    """Platform-to-platform times published by the network (transfers.txt): Île-de-France Mobilités gives 4 to 10 minutes
    in the corridors of Châtelet, where a flat estimate would say 2. Median per pair of (station, line)."""
    samples: Dict[Tuple[int, str, int, str], List[float]] = defaultdict(list)
    for row in rows:
        a, b, seconds = row["from_stop_id"], row["to_stop_id"], row.get("min_transfer_time")
        # Type 3: no transfer possible there, not a time.
        if not seconds or row.get("transfer_type") == "3" or a not in stop_routes or b not in stop_routes:
            continue
        for route_a in stop_routes[a]:
            for route_b in stop_routes[b]:
                if route_a != route_b:
                    samples[(complex_of[a], route_a, complex_of[b], route_b)].append(int(seconds) / 60.0)
    return {key: round(statistics.median(values), 2) for key, values in samples.items()}


def build_graph(complexes: Sequence[dict], edges, waits, transfer_times, route_info: Dict[str, dict],
                access_minutes: Dict[str, float], rivers: Rivers):
    route_states: List[dict] = []
    station_states: List[List[int]] = [[] for _ in complexes]
    lookup: Dict[Tuple[int, str], int] = {}
    for station_index, station in enumerate(complexes):
        for route_id in sorted(station["routes"]):
            state_index = len(route_states)
            route_states.append(
                {
                    "stationIndex": station_index,
                    "routeId": route_id,
                    "wait": waits.get((station_index, route_id), DEFAULT_BOARD_WAIT),
                    "access": access_minutes.get(route_info[route_id]["mode"], 0.0),
                }
            )
            station_states[station_index].append(state_index)
            lookup[(station_index, route_id)] = state_index

    adjacency: List[List[List[float]]] = [[] for _ in route_states]

    def add_edge(src: int, dst: int, weight: float, extra_wait: float = 0.0) -> None:
        # A third value, when present, is the part of the weight spent waiting (for a branch), shown as such.
        adjacency[src].append([dst, round(weight, 2), round(extra_wait, 2)] if extra_wait else [dst, round(weight, 2)])

    # Ride edges are directed: one-way loops and branches stay correct.
    for (a, b, route_id), (minutes, extra) in edges.items():
        add_edge(lookup[(a, route_id)], lookup[(b, route_id)], minutes + extra, extra)

    def transfer(src: int, dst: int, walk: float) -> float:
        published = transfer_times.get((route_states[src]["stationIndex"], route_states[src]["routeId"],
                                        route_states[dst]["stationIndex"], route_states[dst]["routeId"]))
        if published is not None:
            # The network's own platform-to-platform time, corridors included.
            return published + route_states[dst]["wait"]
        # Leaving one platform and reaching the other: half of each access time, plus the wait.
        access = (route_states[src]["access"] + route_states[dst]["access"]) / 2.0
        return walk + access + route_states[dst]["wait"]

    # Changing line inside a stop: short walk plus waiting for the next vehicle.
    for states in station_states:
        for src in states:
            for dst in states:
                if src != dst:
                    add_edge(src, dst, transfer(src, dst, TRANSFER_WALK))

    # Walking to a nearby stop with another name.
    points = [station["point"] for station in complexes]
    index = StationIndex(points, range(len(points)))
    for i, a in enumerate(complexes):
        for j in index.within(a["point"], INTER_COMPLEX_WALK_RADIUS):
            meters = rivers.walk(a["point"], complexes[j]["point"])
            if i == j or meters > INTER_COMPLEX_WALK_RADIUS:
                continue
            walk = meters / WALK_METERS_PER_MINUTE + TRANSFER_WALK
            for src in station_states[i]:
                for dst in station_states[j]:
                    if route_states[src]["routeId"] != route_states[dst]["routeId"]:
                        add_edge(src, dst, transfer(src, dst, walk))
    return route_states, station_states, adjacency


# --- Rail geometry ----------------------------------------------------------


def rail_routes_from_gtfs(gtfs: Path, shape_routes: Dict[str, str], route_info: Dict[str, dict]) -> List[dict]:
    points: Dict[str, List[Tuple[int, Point]]] = defaultdict(list)
    with zipfile.ZipFile(gtfs) as archive:
        for row in read_gtfs_table(archive, "shapes.txt"):
            if row["shape_id"] in shape_routes:
                points[row["shape_id"]].append(
                    (int(row["shape_pt_sequence"]), lonlat_to_xy(float(row["shape_pt_lon"]), float(row["shape_pt_lat"])))
                )
    shapes, seen = [], set()
    for shape_id, sequence in sorted(points.items()):
        line = simplify_polyline([point for _, point in sorted(sequence)], MIN_LINE_DISTANCE)
        key = (shape_routes[shape_id], tuple(sorted({tuple(round_point(p)) for p in line[:: max(1, len(line) // 20)]})))
        if len(line) < 2 or key in seen:
            continue
        seen.add(key)
        route_id = shape_routes[shape_id]
        shapes.append({"id": route_id, "color": route_info[route_id]["color"], "points": [round_point(p) for p in line]})
    return shapes


def rail_routes_from_osm(data_dir: Path, city: dict, route_info: Dict[str, dict]) -> List[dict]:
    payload = load_json(data_dir / "osm_rail.json")
    by_name = {info["name"]: route_id for route_id, info in route_info.items() if info["rail"]}
    aliases = city.get("osmRefAliases", {})
    seen: Dict[str, set] = defaultdict(set)
    shapes = []
    for relation in sorted(payload["elements"], key=lambda item: item["id"]):
        ref = relation.get("tags", {}).get("ref", "")
        route_id = by_name.get(aliases.get(ref, ref))
        if not route_id:
            continue
        for member in relation.get("members", []):
            if member["type"] != "way" or member.get("role") not in ("", None) or member["ref"] in seen[route_id]:
                continue
            seen[route_id].add(member["ref"])
            points = way_points(member.get("geometry", []))
            if len(points) >= 2:
                shapes.append(
                    {
                        "id": route_id,
                        "color": route_info[route_id]["color"],
                        "points": [round_point(p) for p in simplify_polyline(points, MIN_LINE_DISTANCE)],
                    }
                )
    return shapes


# --- Grid -------------------------------------------------------------------


def build_grid(land: MultiPolygon, masked_water: MultiPolygon, stations: Sequence[dict], bounds, cols: int, rows: int, rivers: Rivers):
    min_x, min_y, max_x, max_y = bounds
    cell_w = (max_x - min_x) / cols
    cell_h = (max_y - min_y) / rows
    land_set, water_set = PolygonSet(land), PolygonSet(masked_water)
    points = [station["point"] for station in stations]
    all_index = StationIndex(points, range(len(points)))
    rail_index = StationIndex(points, [i for i, station in enumerate(stations) if station["rail"]])
    cells = []
    mask = [-1] * (cols * rows)
    for row in range(rows):
        for col in range(cols):
            point = (min_x + (col + 0.5) * cell_w, min_y + (row + 0.5) * cell_h)
            if not land_set.contains(point) or water_set.contains(point):
                continue
            def reachable(index_: StationIndex, count: int) -> List[Tuple[float, int]]:
                found: List[Tuple[float, int]] = []
                for straight, i in index_.nearest(point, count * CELL_CANDIDATES):
                    # A walk is never shorter than the straight line: once `count` stops are closer, the rest is useless.
                    if len(found) >= count and straight >= found[count - 1][0]:
                        break
                    meters = rivers.walk(point, points[i])
                    if meters < math.inf:
                        found.append((meters, i))
                        found.sort()
                return found[:count]

            nearest = {index: meters for meters, index in reachable(all_index, CELL_NEAREST_STATIONS)}
            for meters, index in reachable(rail_index, CELL_NEAREST_RAIL_STATIONS):
                nearest[index] = meters
            mask[row * cols + col] = len(cells)
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "point": round_point(point),
                    "access": [[index, round(meters, 1)] for index, meters in sorted(nearest.items(), key=lambda item: item[1])],
                }
            )
    return cells, mask


def network_stats(city: dict, route_info, stations, route_states, station_states, adjacency, rivers: Rivers) -> dict:
    """Figures shown on the page (and its FAQ): lines, headways, share of rail stations within 30 min of the centre."""
    rail_states = [i for i, state in enumerate(route_states) if route_info[state["routeId"]]["rail"]]
    lines = []
    mode_order = {"metro": 0, "tram": 1, "funicular": 2, "cable": 3}
    for route_id, info in sorted(route_info.items(), key=lambda item: (mode_order.get(item[1]["mode"], 9), len(item[1]["name"]), item[1]["name"])):
        if not info["rail"]:
            continue
        waits = sorted(route_states[i]["wait"] for i in rail_states if route_states[i]["routeId"] == route_id)
        lines.append(
            {
                "name": info["name"],
                "mode": info["mode"],
                "color": info["color"],
                "stations": len(waits),
                "headway": round(statistics.median(waits) * 2, 1),
            }
        )

    # Same model as the browser: walk to the nearest rail stations, then rail only.
    origin = lonlat_to_xy(city["defaultFrom"]["lon"], city["defaultFrom"]["lat"])
    rail_station_ids = [i for i, station in enumerate(stations) if station["rail"]]
    nearest = sorted(rail_station_ids, key=lambda i: dist(origin, stations[i]["point"]))[: ORIGIN_NEAREST_STATIONS * CELL_CANDIDATES]
    walks = {i: rivers.walk(origin, stations[i]["point"]) for i in nearest}
    seeds = sorted((i for i in nearest if walks[i] < math.inf), key=walks.get)[:ORIGIN_NEAREST_STATIONS]
    best = [math.inf] * len(route_states)
    heap: List[Tuple[float, int]] = []
    for station_index in seeds:
        walk = walks[station_index] / WALK_METERS_PER_MINUTE
        for state in station_states[station_index]:
            if route_info[route_states[state]["routeId"]]["rail"]:
                time = walk + route_states[state]["access"] + route_states[state]["wait"]
                if time < best[state]:
                    best[state] = time
                    heapq.heappush(heap, (time, state))
    while heap:
        time, state = heapq.heappop(heap)
        if time > best[state]:
            continue
        for target, weight, *_ in adjacency[state]:
            target = int(target)
            if route_info[route_states[target]["routeId"]]["rail"] and time + weight < best[target]:
                best[target] = time + weight
                heapq.heappush(heap, (time + weight, target))
    arrival = {}
    for state, time in enumerate(best):
        index = route_states[state]["stationIndex"]
        out = time + route_states[state]["access"]
        walk = dist(origin, stations[index]["point"]) / WALK_METERS_PER_MINUTE
        arrival[index] = min(arrival.get(index, math.inf), out, walk)
    rail_times = [arrival.get(i, math.inf) for i in rail_station_ids]
    reachable = [i for i in rail_station_ids if math.isfinite(arrival.get(i, math.inf))]
    farthest = max(reachable, key=lambda i: arrival[i])
    og_station = min(reachable, key=lambda i: abs(arrival[i] - OG_TRIP_MINUTES))
    meters_per_deg_lat = 111_320.0
    og_x, og_y = stations[og_station]["point"]
    return {
        "lines": lines,
        "railStations": len(rail_station_ids),
        "busLines": sum(1 for info in route_info.values() if not info["rail"]),
        "center": city["defaultFrom"]["label"],
        "within15": round(100 * sum(t <= 15 for t in rail_times) / len(rail_times)),
        "within30": round(100 * sum(t <= 30 for t in rail_times) / len(rail_times)),
        "farthestStation": stations[farthest]["name"],
        "farthestMinutes": round(arrival[farthest]),
        "ogTrip": {
            "name": stations[og_station]["name"],
            "lat": round(og_y / meters_per_deg_lat, 5),
            "lon": round(og_x / (meters_per_deg_lat * math.cos(math.radians(LAT0))), 5),
        },
    }


def write_provenance(city: dict, data_dir: Path, reference_date: date, route_info: Dict[str, dict], stations: Sequence[dict], stats: dict) -> Path:
    """Record in sources/<city>.json (versioned) which raw files were used, when they were fetched and what they cover."""
    manifest_path = data_dir / "manifest.json"
    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    with zipfile.ZipFile(gtfs_path(data_dir, city)) as archive:
        feed_info = next(iter(read_gtfs_table(archive, "feed_info.txt")), None)
        services = services_by_date(list(read_gtfs_table(archive, "calendar.txt")), list(read_gtfs_table(archive, "calendar_dates.txt")))
    days = sorted(day for day, active in services.items() if active)
    window_start, window_end = SERVICE_WINDOW
    provenance = {
        "city": city["name"],
        "builtAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "referenceDate": reference_date.isoformat(),
        "serviceWindow": f"{window_start // 3600}h–{window_end // 3600}h",
        "gtfs": {
            "network": city["network"],
            "dataset": city["gtfsDataset"],
            **manifest.get("gtfs.zip", {}),
            "feedInfo": feed_info,
            "servicePeriod": [days[0].isoformat(), days[-1].isoformat()] if days else None,
        },
        **({"gtfsExtra": [
            {"network": extra["network"], "dataset": extra["dataset"], **manifest.get(f"gtfs_{extra['slug']}.zip", {})}
            for extra in city["gtfsExtra"]
        ]} if city.get("gtfsExtra") else {}),
        "communes": {
            "metropole": city["metropole"],
            **({"epci": city["epci"]} if city.get("epci") else {}),
            **manifest.get("communes_osm.json" if city.get("communesOsm") else "communes.geojson", {}),
        },
        **({"arrondissements": manifest["arrondissements.geojson"]} if "arrondissements.geojson" in manifest else {}),
        "openStreetMap": {
            "licence": "ODbL, © contributeurs OpenStreetMap",
            **{name.removesuffix(".json"): manifest[name] for name in ("osm_rail.json", "osm_water_parks.json", "osm_rivers.json", "osm_bridges.json") if name in manifest},
        },
        "railGeometry": "OpenStreetMap" if city.get("railGeometry") == "osm" else "GTFS shapes.txt",
        "excludedRoutes": city.get("excludeRoutes", []),
        "network": {
            "lines": dict(Counter(info["mode"] for info in route_info.values())),
            "stops": len(stations),
            "railStations": sum(1 for station in stations if station["rail"]),
        },
        "stats": stats,
    }
    path = ROOT / "sources" / f"{city['slug']}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    global LAT0
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    city = load_city(sys.argv[1])
    LAT0 = city["lat0"]
    data_dir = ROOT / "data" / city["slug"]
    output_path = ROOT / "site" / "data" / f"{city['slug']}.json"

    reference_date, complexes, edges, waits, transfer_times, route_info, shape_routes = extract_network(data_dir, city)
    for station in complexes:
        station["rail"] = any(route_info[route_id]["rail"] for route_id in station["routes"])
    communes, land = extract_communes(data_dir, city, complexes)
    arrondissements = extract_arrondissements(data_dir, city)
    bounds = multipolygon_bounds(land, LAND_PAD_METERS)
    cols = round((bounds[2] - bounds[0]) / GRID_CELL_METERS)
    rows = round((bounds[3] - bounds[1]) / GRID_CELL_METERS)
    masked_water, water, parks = extract_water_and_parks(data_dir, bounds)
    context = extract_context(data_dir, city)

    access_minutes = {**MODE_ACCESS_MINUTES, **city.get("modeAccess", {})}
    river_lines, bridges = extract_rivers(data_dir, city)
    rivers = Rivers(river_lines, bridges)
    route_states, station_states, adjacency = build_graph(complexes, edges, waits, transfer_times, route_info, access_minutes, rivers)
    if city.get("railGeometry") == "osm":
        routes = rail_routes_from_osm(data_dir, city, route_info)
    else:
        routes = rail_routes_from_gtfs(gtfs_path(data_dir, city), shape_routes, route_info)

    stations = [
        {
            "id": station["id"],
            "name": station["name"],
            "point": station["point"],
            "routes": sorted(station["routes"], key=lambda r: (len(route_info[r]["name"]), route_info[r]["name"])),
            "rail": any(route_info[route_id]["rail"] for route_id in station["routes"]),
        }
        for station in complexes
    ]
    rail_points = [station["point"] for station in stations if station["rail"]]
    view_bounds = (
        min(x for x, _ in rail_points) - VIEW_PAD_METERS,
        min(y for _, y in rail_points) - VIEW_PAD_METERS,
        max(x for x, _ in rail_points) + VIEW_PAD_METERS,
        max(y for _, y in rail_points) + VIEW_PAD_METERS,
    )
    if city.get("view") == "stops":
        # Framing the tram/metro network alone hides the neighbourhoods it does not reach (Marseille): frame every
        # stop instead, buses included, which follows where people live without the empty hills and sea.
        view_bounds = (
            min(station["point"][0] for station in stations) - VIEW_PAD_METERS,
            min(station["point"][1] for station in stations) - VIEW_PAD_METERS,
            max(station["point"][0] for station in stations) + VIEW_PAD_METERS,
            max(station["point"][1] for station in stations) + VIEW_PAD_METERS,
        )
    if city.get("viewBbox"):
        # Paris: the RER reaches far into the suburbs, the map opens on the city and its inner ring.
        south, west, north, east = city["viewBbox"]
        view_bounds = (*lonlat_to_xy(west, south), *lonlat_to_xy(east, north))
    cells, mask = build_grid(land, masked_water, stations, bounds, cols, rows, rivers)

    output = {
        "meta": {
            "lat0": LAT0,
            "referenceDate": reference_date.strftime("%Y%m%d"),
            "bounds": [round(v, 1) for v in bounds],
            "viewBounds": [round(v, 1) for v in view_bounds],
            "gridCols": cols,
            "gridRows": rows,
            "walkMetersPerMinute": WALK_METERS_PER_MINUTE,
            "originStationCount": ORIGIN_NEAREST_STATIONS,
            "sea": bool(context),
        },
        "context": [serialize_polygon(polygon) for polygon in context],
        "boroughs": communes,
        **({"arrondissements": arrondissements} if arrondissements else {}),
        **({"rivers": [[round_point(point) for point in line] for line in river_lines]} if river_lines else {}),
        **({"bridges": [[round_point(a), round_point(b), round(length, 1)] for a, b, length in bridges]} if bridges else {}),
        "water": [serialize_polygon(polygon) for polygon in masked_water + water],
        "parks": [serialize_polygon(polygon) for polygon in parks],
        "routes": routes,
        "routeInfo": route_info,
        "stations": [{**station, "point": round_point(station["point"])} for station in stations],
        "routeStates": route_states,
        "stationStates": station_states,
        "adjacency": adjacency,
        "cells": cells,
        "mask": mask,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    stats = network_stats(city, route_info, stations, route_states, station_states, adjacency, rivers)
    provenance_path = write_provenance(city, data_dir, reference_date, route_info, stations, stats)
    print(f"Wrote {provenance_path.relative_to(ROOT)}")
    rail_count = sum(1 for station in stations if station["rail"])
    modes = Counter(info["mode"] for info in route_info.values())
    print(
        f"Wrote {output_path.relative_to(ROOT)} "
        f"({output_path.stat().st_size / 1_000_000:.2f} MB, GTFS du {reference_date}, lignes {dict(modes)}, "
        f"{len(stations)} arrêts dont {rail_count} tram/métro, {len(route_states)} states, "
        f"{sum(len(a) for a in adjacency)} edges, {len(cells)} cells ({cols}×{rows}), {len(routes)} tracés)"
    )


if __name__ == "__main__":
    main()
