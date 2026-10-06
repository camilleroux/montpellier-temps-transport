// Carte des temps de trajet en transports en commun (tram.camilleroux.com).
// La ville affichée est décrite par le bloc JSON #city-config de la page.
// Carte des temps de trajet en tram (et bus) sur le réseau TaM.

const CITY = JSON.parse(document.getElementById("city-config").textContent);
const DATA_URL = new URL(`./data/${CITY.slug}.json?v=${CITY.dataVersion}`, import.meta.url);
// Base Adresse Nationale in France; Photon (OSM) elsewhere, limited to the city's area.
const GEOCODER_URL = CITY.geocoder === "photon" ? "https://photon.komoot.io/api/" : "https://api-adresse.data.gouv.fr/search/";

const DEFAULT_FROM = CITY.defaultFrom;
const MODE_LABELS = {
  tram: "Tram",
  metro: "Métro",
  rer: "RER",
  train: "Train",
  funicular: "Funiculaire",
  cable: "Téléphérique",
  ferry: "Bateau",
  busway: "Busway",
  bhns: "BHNS",
  bus: "Bus",
};
const DEFAULT_MAX = 45;
const ISOCHRONE_OPTIONS = [15, 30, 45, 60];
const DEFAULT_ISOCHRONES = [15, 30];
const REACH_MINUTES = 30;
// Au doigt, on vise moins précisément et un tap bouge souvent de quelques pixels.
const MARKER_HIT_RADIUS = { mouse: 18, touch: 30 };
const CLICK_SLOP = { mouse: 5, touch: 12 };
const MIN_ZOOM_FACTOR = 0.5;
const MAX_ZOOM_FACTOR = 14;
const STOP_LABEL_SCALE = 0.13; // pixels par mètre au-delà desquels on nomme les arrêts
const RAIL_NAME_RADIUS = 400; // mètres

// Du plus proche (vert) au plus lointain (rouge) ; au-delà du max : gris.
const PALETTE = [
  [0, [47, 150, 18]],
  [0.25, [126, 200, 80]],
  [0.5, [226, 228, 120]],
  [0.75, [244, 182, 112]],
  [1, [226, 120, 120]],
];
// Au-delà du max, la couleur s'efface progressivement jusqu'à laisser voir le fond.
const BEYOND_FADE = 0.15;
const HEAT_ALPHA = 0.78;
const HEAT_UPSAMPLE = 3;
const LUT_SIZE = 512;
const NEIGHBOURS = [[1, 0], [-1, 0], [0, 1], [0, -1]];
const RIVER_BRIDGE_CELLS = 4; // cases de 200 m : de quoi traverser le Rhône ou la Garonne

const COLORS = {
  background: "#f1efe9",
  land: "#e4e2dc",
  water: "#bcd7e8",
  park: "rgba(120, 180, 90, 0.18)",
  communeLine: "rgba(255, 255, 255, 0.9)",
  contour: "#111111",
  from: "#3aa70b",
  to: "#111111",
};

const $ = (id) => document.getElementById(id);
const canvas = $("mapCanvas");
const ctx = canvas.getContext("2d");
const stage = $("mapStage");

const app = {
  data: null,
  graph: null,
  paths: null,
  offset: [0, 0],
  view: { cx: 0, cy: 0, scale: 1, fitScale: 1 },
  size: { width: 0, height: 0, dpr: 1 },
  from: null, // { point, label }
  to: null, // { point, label }
  includeBus: false,
  maxMinutes: DEFAULT_MAX,
  isochrones: [...DEFAULT_ISOCHRONES],
  heatFrom: "from", // la heatmap part du départ ou de l'arrivée
  solution: null, // plus courts chemins depuis le départ (panneau, itinéraire)
  heatSolution: null, // plus courts chemins depuis le point d'où part la heatmap
  grid: null,
  heatCanvas: document.createElement("canvas"),
  drag: null,
  pointers: new Map(),
  frameRequested: false,
};

// --- Petites fonctions utilitaires ------------------------------------------

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const hypot = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

function formatMinutes(minutes) {
  if (!Number.isFinite(minutes)) return "—";
  if (minutes < 1) return "< 1 min";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes - hours * 60);
  return `${hours} h ${String(rest).padStart(2, "0")}`;
}

function paletteColor(t) {
  for (let i = 1; i < PALETTE.length; i += 1) {
    const [stop, color] = PALETTE[i];
    if (t <= stop) {
      const [prevStop, prevColor] = PALETTE[i - 1];
      const mix = (t - prevStop) / (stop - prevStop);
      return prevColor.map((channel, c) => Math.round(channel + (color[c] - channel) * mix));
    }
  }
  return PALETTE[PALETTE.length - 1][1];
}

function metersPerDegree() {
  const lat = 111320;
  return { lat, lon: lat * Math.cos((app.data.meta.lat0 * Math.PI) / 180) };
}

function toWorld(lat, lon) {
  const m = metersPerDegree();
  return [lon * m.lon, lat * m.lat];
}

function toLatLon(point) {
  const m = metersPerDegree();
  return { lat: point[1] / m.lat, lon: point[0] / m.lon };
}

function pointInRing(point, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > point[1] !== yj > point[1] && point[0] < ((xj - xi) * (point[1] - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function pointInPolygon(point, polygon) {
  return pointInRing(point, polygon[0]) && !polygon.slice(1).some((hole) => pointInRing(point, hole));
}

// --- Cours d'eau --------------------------------------------------------------
// Les grands cours d'eau (Loire, Garonne, Rhône…) ne se traversent à pied que par un pont : une marche dont la ligne
// droite en coupe un passe par le meilleur pont (un seul : une île se rejoint par ses arrêts). Même règle que build_data.py.

const RIVER_BUCKET = 500;
const MAX_BRIDGE_WALK_METERS = 3000;

function riverKeys(a, b, visit) {
  for (let gx = Math.floor(Math.min(a[0], b[0]) / RIVER_BUCKET); gx <= Math.floor(Math.max(a[0], b[0]) / RIVER_BUCKET); gx += 1) {
    for (let gy = Math.floor(Math.min(a[1], b[1]) / RIVER_BUCKET); gy <= Math.floor(Math.max(a[1], b[1]) / RIVER_BUCKET); gy += 1) {
      if (visit(`${gx},${gy}`)) return true;
    }
  }
  return false;
}

function indexRivers(lines) {
  const buckets = new Map();
  for (const line of lines ?? []) {
    for (let i = 1; i < line.length; i += 1) {
      const segment = [line[i - 1], line[i]];
      riverKeys(segment[0], segment[1], (key) => {
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(segment);
      });
    }
  }
  return buckets;
}

function side(p, q, r) {
  return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
}

function crossesRiver(a, b) {
  const buckets = app.rivers;
  if (!buckets.size) return false;
  return riverKeys(a, b, (key) =>
    (buckets.get(key) ?? []).some(([c, d]) => side(a, b, c) * side(a, b, d) < 0 && side(c, d, a) * side(c, d, b) < 0),
  );
}

/** Distance de marche en mètres : en ligne droite, ou par un pont ; infinie sans pont praticable. */
function walkMeters(a, b) {
  const straight = hypot(a, b);
  if (!crossesRiver(a, b)) return straight;
  // Le plus court détour d'abord : le premier pont dont les deux tronçons restent sur leur rive est le meilleur.
  const detours = [];
  for (const [endA, endB, length] of app.data.bridges ?? []) {
    detours.push([hypot(a, endA) + length + hypot(endB, b), endA, endB], [hypot(a, endB) + length + hypot(endA, b), endB, endA]);
  }
  detours.sort((x, y) => x[0] - y[0]);
  // Au-delà de 3 km (40 min), marcher n'est jamais le meilleur choix : inutile de tester les ponts lointains.
  const found = detours.find(([meters, near, far]) => meters <= MAX_BRIDGE_WALK_METERS && !crossesRiver(a, near) && !crossesRiver(far, b));
  return found ? found[0] : Infinity;
}

function isOnLand(point) {
  if (app.data.water.some((polygon) => pointInPolygon(point, polygon))) return false;
  return app.data.boroughs.some((commune) => commune.polygons.some((polygon) => pointInPolygon(point, polygon)));
}

function communeAt(point) {
  const inside = (area) => area.polygons.some((polygon) => pointInPolygon(point, polygon));
  return ((app.data.arrondissements ?? []).find(inside) ?? app.data.boroughs.find(inside))?.name;
}

// --- Graphe du réseau ---------------------------------------------------------

class MinHeap {
  constructor() {
    this.keys = [];
    this.values = [];
  }

  get size() {
    return this.keys.length;
  }

  push(key, value) {
    const { keys, values } = this;
    let i = keys.length;
    keys.push(key);
    values.push(value);
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (keys[parent] <= key) break;
      keys[i] = keys[parent];
      values[i] = values[parent];
      i = parent;
    }
    keys[i] = key;
    values[i] = value;
  }

  pop() {
    const { keys, values } = this;
    const top = values[0];
    const lastKey = keys.pop();
    const lastValue = values.pop();
    if (keys.length) {
      let i = 0;
      for (;;) {
        let child = 2 * i + 1;
        if (child >= keys.length) break;
        if (child + 1 < keys.length && keys[child + 1] < keys[child]) child += 1;
        if (keys[child] >= lastKey) break;
        keys[i] = keys[child];
        values[i] = values[child];
        i = child;
      }
      keys[i] = lastKey;
      values[i] = lastValue;
    }
    return top;
  }
}

function prepareGraph(data) {
  const count = data.routeStates.length;
  const offsets = new Int32Array(count + 1);
  data.adjacency.forEach((edges, i) => {
    offsets[i + 1] = offsets[i] + edges.length;
  });
  const targets = new Int32Array(offsets[count]);
  const weights = new Float32Array(offsets[count]);
  data.adjacency.forEach((edges, i) => {
    edges.forEach(([target, weight], k) => {
      targets[offsets[i] + k] = target;
      weights[offsets[i] + k] = weight;
    });
  });
  return {
    count,
    offsets,
    targets,
    weights,
    station: Int32Array.from(data.routeStates, (state) => state.stationIndex),
    wait: Float32Array.from(data.routeStates, (state) => state.wait),
    // Accès au quai (escaliers, couloirs du métro), compté à l'entrée comme à la sortie.
    access: Float32Array.from(data.routeStates, (state) => state.access),
    route: data.routeStates.map((state) => state.routeId),
    isBus: Uint8Array.from(data.routeStates, (state) => (data.routeInfo[state.routeId]?.rail ? 0 : 1)),
  };
}

function walkMinutes(meters) {
  return meters / app.data.meta.walkMetersPerMinute;
}

function stationUsable(index) {
  return app.includeBus || app.data.stations[index].rail;
}

/** Plus courts chemins depuis un point : temps d'arrivée à chaque arrêt + prédécesseurs. */
function solveFrom(point) {
  const { graph, data } = app;
  const dist = new Float64Array(graph.count).fill(Infinity);
  const prev = new Int32Array(graph.count).fill(-1);
  const seedWalk = new Float64Array(graph.count);
  const heap = new MinHeap();

  // Les arrêts les plus proches à vol d'oiseau, puis leur vraie distance à pied (détour par un pont).
  const seeds = data.stations
    .map((station, index) => ({ index, walk: walkMinutes(hypot(point, station.point)) }))
    .filter((seed) => stationUsable(seed.index))
    .sort((a, b) => a.walk - b.walk)
    .slice(0, data.meta.originStationCount * 4)
    .map((seed) => ({ index: seed.index, walk: walkMinutes(walkMeters(point, data.stations[seed.index].point)) }))
    .filter((seed) => Number.isFinite(seed.walk))
    .sort((a, b) => a.walk - b.walk)
    .slice(0, data.meta.originStationCount);

  for (const seed of seeds) {
    for (const state of data.stationStates[seed.index]) {
      if (!app.includeBus && graph.isBus[state]) continue;
      const walk = seed.walk + graph.access[state];
      const time = walk + graph.wait[state];
      if (time < dist[state]) {
        dist[state] = time;
        seedWalk[state] = walk;
        heap.push(time, state);
      }
    }
  }

  while (heap.size) {
    const state = heap.pop();
    const base = dist[state];
    for (let e = graph.offsets[state]; e < graph.offsets[state + 1]; e += 1) {
      const next = graph.targets[e];
      if (!app.includeBus && graph.isBus[next]) continue;
      const time = base + graph.weights[e];
      if (time < dist[next]) {
        dist[next] = time;
        prev[next] = state;
        heap.push(time, next);
      }
    }
  }

  const stationTime = new Float64Array(data.stations.length).fill(Infinity);
  const stationBest = new Int32Array(data.stations.length).fill(-1);
  // Temps pour ressortir dans la rue à chaque arrêt (le métro demande de remonter du quai).
  for (let state = 0; state < graph.count; state += 1) {
    const station = graph.station[state];
    const out = dist[state] + graph.access[state];
    if (out < stationTime[station]) {
      stationTime[station] = out;
      stationBest[station] = state;
    }
  }
  return { point, dist, prev, seedWalk, stationTime, stationBest };
}

/** Meilleur temps vers un point quelconque : à pied direct, ou via l'arrêt le plus favorable. */
function travelTo(solution, point) {
  const direct = walkMinutes(walkMeters(solution.point, point));
  let best = { minutes: direct, station: -1, walk: direct };
  app.data.stations.forEach((station, index) => {
    const arrival = solution.stationTime[index];
    // La vraie distance (pont), plus coûteuse, seulement pour un arrêt qui peut améliorer le trajet.
    if (!Number.isFinite(arrival) || arrival + walkMinutes(hypot(station.point, point)) >= best.minutes) return;
    const walk = walkMinutes(walkMeters(station.point, point));
    if (arrival + walk < best.minutes) best = { minutes: arrival + walk, station: index, walk };
  });
  return best;
}

function routeLabel(routeId) {
  const info = app.data.routeInfo[routeId];
  return `${MODE_LABELS[info.mode] ?? "Ligne"} ${info.name}`;
}

/** Reconstitue l'itinéraire (marche, lignes, correspondances) vers un point. */
function buildItinerary(solution, point) {
  const { graph, data } = app;
  const result = travelTo(solution, point);
  if (result.station === -1) {
    return { minutes: result.minutes, steps: [{ kind: "walk", text: "Tout à pied", minutes: result.minutes }] };
  }

  const chain = [];
  for (let state = solution.stationBest[result.station]; state !== -1; state = solution.prev[state]) chain.push(state);
  chain.reverse();

  const name = (state) => data.stations[graph.station[state]].name;
  const steps = [{ kind: "walk", text: `À pied jusqu'à ${name(chain[0])}`, minutes: solution.seedWalk[chain[0]] }];
  // Part du trajet passée à attendre un train de sa branche (ligne 13, RER A) : 3ᵉ valeur, rare, de l'arête.
  const extraWait = (from, to) => data.adjacency[from].find(([target]) => target === to)?.[2] ?? 0;
  let legStart = chain[0];
  let legExtra = 0;
  const closeLeg = (legEnd) => {
    steps.push({
      kind: "ride",
      route: graph.route[legStart],
      text: `${name(legStart)} → ${name(legEnd)}`,
      wait: graph.wait[legStart] + legExtra,
      minutes: solution.dist[legEnd] - solution.dist[legStart] - legExtra,
    });
  };
  for (let i = 1; i < chain.length; i += 1) {
    const from = chain[i - 1];
    const to = chain[i];
    if (graph.route[from] === graph.route[to] && graph.station[from] !== graph.station[to]) {
      legExtra += extraWait(from, to);
      continue;
    }
    closeLeg(from);
    // Couloirs et quais : tout le temps de la correspondance, sauf l'attente de la ligne suivante (affichée avec elle).
    const minutes = solution.dist[to] - solution.dist[from] - graph.wait[to];
    const text = graph.station[from] === graph.station[to] ? `Correspondance à ${name(to)}` : `Correspondance à pied vers ${name(to)}`;
    steps.push({ kind: "walk", text, minutes });
    legStart = to;
    legExtra = 0;
  }
  closeLeg(chain[chain.length - 1]);
  // La sortie du quai (métro) est comptée avec la marche finale.
  const exit = graph.access[chain[chain.length - 1]];
  steps.push({ kind: "walk", text: "À pied jusqu'à l'arrivée", minutes: result.walk + exit });
  return { minutes: result.minutes, steps };
}

// --- Grille des temps ---------------------------------------------------------

/** Comble les cases sans valeur (eau, hors carte) avec la moyenne de leurs voisines, `passes` fois. */
function fillGaps(values, cols, rows, passes) {
  const filled = Float32Array.from(values);
  for (let pass = 0; pass < passes; pass += 1) {
    const source = Float32Array.from(filled);
    for (let index = 0; index < source.length; index += 1) {
      if (!Number.isNaN(source[index])) continue;
      const row = Math.floor(index / cols);
      const col = index % cols;
      let sum = 0;
      let count = 0;
      for (const [dr, dc] of NEIGHBOURS) {
        const r = row + dr;
        const c = col + dc;
        if (r < 0 || c < 0 || r >= rows || c >= cols) continue;
        const value = source[r * cols + c];
        if (!Number.isNaN(value)) {
          sum += value;
          count += 1;
        }
      }
      if (count) filled[index] = sum / count;
    }
  }
  return filled;
}

function computeGrid(solution) {
  const { cells, meta } = app.data;
  const { gridCols: cols, gridRows: rows } = meta;
  const times = new Float32Array(cols * rows).fill(NaN);
  for (const cell of cells) {
    // cell.access donne déjà la vraie distance à pied (build_data.py) ; la marche directe se calcule ici.
    let best = Infinity;
    for (const [station, meters] of cell.access) {
      const time = solution.stationTime[station] + walkMinutes(meters);
      if (time < best) best = time;
    }
    if (walkMinutes(hypot(solution.point, cell.point)) < best) best = Math.min(best, walkMinutes(walkMeters(solution.point, cell.point)));
    // Case enclavée derrière un cours d'eau, sans arrêt de son côté : très loin, sans infini qui contaminerait le lissage.
    times[cell.row * cols + cell.col] = Math.min(best, 180);
  }
  // Les isochrones enjambent les fleuves (comblés avec les valeurs des rives) au lieu d'en faire le tour ;
  // elles sont ensuite découpées sur la terre ferme au dessin.
  const bridged = fillGaps(times, cols, rows, RIVER_BRIDGE_CELLS);
  return { times, smooth: smoothGrid(bridged, cols, rows), cols, rows, contours: {} };
}

/** Moyenne 3×3 limitée à la terre ferme, pour des isochrones moins crénelées. */
function smoothGrid(times, cols, rows) {
  const out = new Float32Array(times.length).fill(NaN);
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const index = row * cols + col;
      if (Number.isNaN(times[index])) continue;
      let sum = 0;
      let weight = 0;
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dx = -1; dx <= 1; dx += 1) {
          const r = row + dy;
          const c = col + dx;
          if (r < 0 || c < 0 || r >= rows || c >= cols) continue;
          const value = times[r * cols + c];
          if (Number.isNaN(value)) continue;
          const w = dx === 0 && dy === 0 ? 2 : 1;
          sum += value * w;
          weight += w;
        }
      }
      out[index] = sum / weight;
    }
  }
  return out;
}

/** Peint la grille dans une image (HEAT_UPSAMPLE² pixels par cellule, interpolation bilinéaire). */
function paintHeat(grid, { fast = false } = {}) {
  const { cols, rows, times } = grid;
  const upsample = fast ? 1 : HEAT_UPSAMPLE;
  const heat = app.heatCanvas;
  heat.width = cols * upsample;
  heat.height = rows * upsample;
  const heatCtx = heat.getContext("2d");
  const image = heatCtx.createImageData(heat.width, heat.height);

  // Étend les valeurs d'un cran hors de la terre pour que le lissage ne fonce pas les côtes.
  const filled = fillGaps(times, cols, rows, 2);

  // Table de couleurs précalculée : t ∈ [0, 1 + BEYOND_FADE] découpé en LUT_SIZE pas.
  const lutMax = 1 + BEYOND_FADE;
  const lut = new Uint32Array(LUT_SIZE);
  const lutBytes = new Uint8Array(lut.buffer);
  for (let i = 0; i < LUT_SIZE; i += 1) {
    const t = (i / (LUT_SIZE - 1)) * lutMax;
    const [r, g, b] = paletteColor(Math.min(t, 1));
    const alpha = t <= 1 ? 255 : Math.round(clamp(1 - (t - 1) / BEYOND_FADE, 0, 1) * 255);
    lutBytes.set([r, g, b, alpha], i * 4);
  }
  const pixels = new Uint32Array(image.data.buffer);
  const toLut = (LUT_SIZE - 1) / (app.maxMinutes * lutMax);
  const step = 1 / upsample;
  const width = heat.width;
  for (let y = 0; y < heat.height; y += 1) {
    const gy = (y + 0.5) * step - 0.5;
    const row0 = clamp(Math.floor(gy), 0, rows - 1);
    const row1 = Math.min(row0 + 1, rows - 1);
    const ty = clamp(gy - row0, 0, 1);
    for (let x = 0; x < width; x += 1) {
      const gx = (x + 0.5) * step - 0.5;
      const col0 = clamp(Math.floor(gx), 0, cols - 1);
      const col1 = Math.min(col0 + 1, cols - 1);
      const tx = clamp(gx - col0, 0, 1);
      const v00 = filled[row0 * cols + col0];
      const v01 = filled[row0 * cols + col1];
      const v10 = filled[row1 * cols + col0];
      const v11 = filled[row1 * cols + col1];
      let sum = 0;
      let weight = 0;
      let w = (1 - tx) * (1 - ty);
      if (v00 === v00) { sum += v00 * w; weight += w; }
      w = tx * (1 - ty);
      if (v01 === v01) { sum += v01 * w; weight += w; }
      w = (1 - tx) * ty;
      if (v10 === v10) { sum += v10 * w; weight += w; }
      w = tx * ty;
      if (v11 === v11) { sum += v11 * w; weight += w; }
      if (weight < 0.25) continue;
      const index = Math.round((sum / weight) * toLut);
      if (index < LUT_SIZE) pixels[y * width + x] = lut[index];
    }
  }
  heatCtx.putImageData(image, 0, 0);
}

/** Marching squares sur les centres de cellules ; renvoie des segments en coordonnées monde. */
function contourSegments(grid, threshold) {
  const { cols, rows, smooth } = grid;
  const [minX, minY, maxX, maxY] = app.data.meta.bounds;
  const cellW = (maxX - minX) / cols;
  const cellH = (maxY - minY) / rows;
  const value = (row, col) => {
    const v = smooth[row * cols + col];
    return Number.isNaN(v) ? Infinity : v;
  };
  const center = (row, col) => [minX + (col + 0.5) * cellW, minY + (row + 0.5) * cellH];
  const between = (pa, va, pb, vb) => {
    const t = Number.isFinite(va) && Number.isFinite(vb) ? clamp((threshold - va) / (vb - va), 0, 1) : 0.5;
    return [pa[0] + (pb[0] - pa[0]) * t, pa[1] + (pb[1] - pa[1]) * t];
  };

  const segments = [];
  for (let row = 0; row < rows - 1; row += 1) {
    for (let col = 0; col < cols - 1; col += 1) {
      // Coins dans le sens trigonométrique : bas-gauche, bas-droite, haut-droite, haut-gauche.
      const corners = [
        [center(row, col), value(row, col)],
        [center(row, col + 1), value(row, col + 1)],
        [center(row + 1, col + 1), value(row + 1, col + 1)],
        [center(row + 1, col), value(row + 1, col)],
      ];
      const inside = corners.map(([, v]) => v <= threshold);
      const crossings = [];
      for (let k = 0; k < 4; k += 1) {
        const a = corners[k];
        const b = corners[(k + 1) % 4];
        if (inside[k] !== inside[(k + 1) % 4]) crossings.push(between(a[0], a[1], b[0], b[1]));
      }
      if (crossings.length === 2) segments.push(crossings);
      else if (crossings.length === 4) segments.push([crossings[0], crossings[1]], [crossings[2], crossings[3]]);
    }
  }
  return segments;
}

// --- Vue et rendu -------------------------------------------------------------

function buildPaths(data) {
  const [ox, oy] = app.offset;
  const ringPath = (path, ring) => {
    ring.forEach(([x, y], i) => (i ? path.lineTo(x - ox, y - oy) : path.moveTo(x - ox, y - oy)));
    path.closePath();
  };
  const polygonsPath = (polygons) => {
    const path = new Path2D();
    for (const polygon of polygons) for (const ring of polygon) ringPath(path, ring);
    return path;
  };
  const communeLines = new Path2D();
  for (const area of [...data.boroughs, ...(data.arrondissements ?? [])]) for (const ring of area.outline) ringPath(communeLines, ring);

  const routes = new Map();
  for (const route of data.routes) {
    if (!routes.has(route.id)) routes.set(route.id, { color: route.color, path: new Path2D() });
    const { path } = routes.get(route.id);
    route.points.forEach(([x, y], i) => (i ? path.lineTo(x - ox, y - oy) : path.moveTo(x - ox, y - oy)));
  }
  return {
    land: polygonsPath(data.boroughs.flatMap((commune) => commune.polygons)),
    // Terres voisines des villes côtières : ce qui reste découvert autour est la mer.
    context: polygonsPath(data.context ?? []),
    // Un chemin par polygone, rempli en « evenodd » : les îles (trous) restent de la terre ferme,
    // sans que deux plans d'eau qui se chevauchent s'annulent.
    water: data.water.map((polygon) => polygonsPath([polygon])),
    parks: data.parks.map((polygon) => polygonsPath([polygon])),
    communeLines,
    routes: [...routes.values()].reverse(),
  };
}

function project(point) {
  const { cx, cy, scale } = app.view;
  return [app.size.width / 2 + (point[0] - cx) * scale, app.size.height / 2 - (point[1] - cy) * scale];
}

function unproject(x, y) {
  const { cx, cy, scale } = app.view;
  return [cx + (x - app.size.width / 2) / scale, cy - (y - app.size.height / 2) / scale];
}

function fitView() {
  const [minX, minY, maxX, maxY] = app.data.meta.viewBounds;
  const { width, height } = app.size;
  const pad = width < 720 ? 12 : 40;
  const scale = Math.min((width - pad * 2) / (maxX - minX), (height - pad * 2) / (maxY - minY));
  app.view = { cx: (minX + maxX) / 2, cy: (minY + maxY) / 2, scale, fitScale: scale };
}

function zoomAt(factor, screenX, screenY) {
  const before = unproject(screenX, screenY);
  const { fitScale } = app.view;
  app.view.scale = clamp(app.view.scale * factor, fitScale * MIN_ZOOM_FACTOR, fitScale * MAX_ZOOM_FACTOR);
  const after = unproject(screenX, screenY);
  app.view.cx += before[0] - after[0];
  app.view.cy += before[1] - after[1];
  requestRender();
}

/** Passe le contexte en coordonnées monde (mètres, origine décalée, axe y vers le nord). */
function useWorldTransform() {
  const { cx, cy, scale } = app.view;
  const { width, height, dpr } = app.size;
  const [ox, oy] = app.offset;
  ctx.setTransform(
    dpr * scale,
    0,
    0,
    -dpr * scale,
    dpr * (width / 2 + (ox - cx) * scale),
    dpr * (height / 2 - (oy - cy) * scale),
  );
}

function useScreenTransform() {
  const { dpr } = app.size;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function drawHaloText(text, x, y, { font, color, halo = "rgba(255,255,255,0.92)", width = 3.5 }) {
  ctx.font = font;
  ctx.lineJoin = "round";
  ctx.strokeStyle = halo;
  ctx.lineWidth = width;
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}

function drawIsochrones() {
  if (!app.grid || !app.isochrones.length) return;
  const px = 1 / app.view.scale;
  const [ox, oy] = app.offset;
  const labels = [];
  for (const threshold of [...app.isochrones].sort((a, b) => a - b)) {
    app.grid.contours[threshold] ??= contourSegments(app.grid, threshold);
    const segments = app.grid.contours[threshold];
    if (!segments.length) continue;
    useWorldTransform();
    const path = new Path2D();
    for (const [a, b] of segments) {
      path.moveTo(a[0] - ox, a[1] - oy);
      path.lineTo(b[0] - ox, b[1] - oy);
    }
    ctx.save();
    ctx.clip(app.paths.land, "evenodd");
    ctx.lineCap = "round";
    ctx.strokeStyle = "rgba(255,255,255,0.8)";
    ctx.lineWidth = 4.5 * px;
    ctx.stroke(path);
    ctx.strokeStyle = COLORS.contour;
    ctx.lineWidth = (threshold >= 30 ? 2 : 1.4) * px;
    ctx.stroke(path);
    ctx.restore();

    // Étiquette sur le point le plus au nord de la courbe encore visible, à l'écart des marqueurs
    // et des étiquettes déjà posées.
    const avoid = [app.from, app.to].filter(Boolean).map((place) => project(place.point));
    avoid.push(...labels.map((label) => label.at));
    const candidates = [];
    for (const [a, b] of segments) {
      const world = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      const [x, y] = project(world);
      if (x < 60 || x > app.size.width - 60 || y < 24 || y > app.size.height - 24) continue;
      if (avoid.some(([ax, ay]) => Math.abs(x - ax) < 70 && y - ay > -60 && y - ay < 40)) continue;
      candidates.push({ world, at: [x, y] });
    }
    candidates.sort((p, q) => p.at[1] - q.at[1]);
    const best = candidates.find((candidate) => isOnLand(candidate.world));
    if (best) labels.push({ text: `${threshold} min`, at: best.at });
  }
  useScreenTransform();
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const { text, at } of labels) {
    drawHaloText(text, at[0], at[1], { font: "700 12px Inter, sans-serif", color: COLORS.contour, width: 5 });
  }
}

function drawStops() {
  const { stations } = app.data;
  if (app.includeBus) {
    ctx.fillStyle = "rgba(60, 60, 60, 0.45)";
    for (const station of stations) {
      if (station.rail) continue;
      const [x, y] = project(station.point);
      ctx.fillRect(x - 1, y - 1, 2, 2);
    }
  }
  const radius = app.view.scale > STOP_LABEL_SCALE ? 3.2 : 2.2;
  for (const station of stations) {
    if (!station.rail) continue;
    const [x, y] = project(station.point);
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = "#fff";
    ctx.fill();
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = "#333";
    ctx.stroke();
  }
  if (app.view.scale > STOP_LABEL_SCALE) {
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    for (const station of stations) {
      if (!station.rail) continue;
      const [x, y] = project(station.point);
      if (x < -50 || y < -20 || x > app.size.width + 50 || y > app.size.height + 20) continue;
      drawHaloText(station.name, x + 6, y, { font: "500 11px Inter, sans-serif", color: "#333" });
    }
  }
}

function drawCommuneNames() {
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const font = `600 ${app.view.scale > app.view.fitScale * 2 ? 13 : 10.5}px Inter, sans-serif`;
  const arrondissements = app.data.arrondissements ?? [];
  // Une commune découpée en arrondissements (Marseille) laisse la place aux noms de ses arrondissements.
  const communes = app.data.boroughs.filter((commune) => !arrondissements.some((a) => a.name.startsWith(`${commune.name} `)));
  for (const commune of [...communes, ...arrondissements]) {
    const [x, y] = project(commune.label);
    if (x < 0 || y < 0 || x > app.size.width || y > app.size.height) continue;
    drawHaloText((commune.short ?? commune.name).toUpperCase(), x, y, { font, color: "rgba(40, 40, 40, 0.55)", halo: "rgba(255,255,255,0.6)" });
  }
}

function drawMarker(point, color, label) {
  const [x, y] = project(point);
  ctx.beginPath();
  ctx.arc(x, y, 15, 0, Math.PI * 2);
  ctx.fillStyle = `${color}2e`;
  ctx.fill();
  ctx.beginPath();
  ctx.arc(x, y, 8, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.lineWidth = 3;
  ctx.strokeStyle = "#fff";
  ctx.stroke();
  if (!label) return;
  ctx.font = "700 12px Inter, sans-serif";
  const width = ctx.measureText(label).width + 16;
  const left = clamp(x - width / 2, 6, app.size.width - width - 6);
  const top = y - 42;
  ctx.beginPath();
  ctx.roundRect(left, top, width, 22, 7);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, left + width / 2, top + 11.5);
}

function render() {
  app.frameRequested = false;
  if (!app.data) return;
  const { width, height, dpr } = app.size;
  const px = 1 / app.view.scale;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  // Villes côtières : le fond est la mer, et les terres voisines sont dessinées par-dessus.
  const sea = app.data.meta.sea;
  ctx.fillStyle = sea ? COLORS.water : COLORS.background;
  ctx.fillRect(0, 0, width, height);

  useWorldTransform();
  if (sea) {
    ctx.fillStyle = COLORS.background;
    ctx.fill(app.paths.context);
  }
  ctx.fillStyle = COLORS.land;
  ctx.fill(app.paths.land, "evenodd");

  if (app.grid) {
    const [minX, minY, maxX, maxY] = app.data.meta.bounds;
    const [ox, oy] = app.offset;
    ctx.save();
    ctx.clip(app.paths.land, "evenodd");
    ctx.globalAlpha = HEAT_ALPHA;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    // L'image a sa ligne 0 au sud : avec l'axe y inversé, elle se dessine dans le bon sens.
    ctx.drawImage(app.heatCanvas, minX - ox, minY - oy, maxX - minX, maxY - minY);
    ctx.restore();
  }

  ctx.fillStyle = COLORS.park;
  for (const park of app.paths.parks) ctx.fill(park, "evenodd");
  ctx.fillStyle = COLORS.water;
  for (const water of app.paths.water) ctx.fill(water, "evenodd");
  ctx.strokeStyle = COLORS.communeLine;
  ctx.lineWidth = 1.1 * px;
  ctx.stroke(app.paths.communeLines);

  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const route of app.paths.routes) {
    ctx.strokeStyle = route.color;
    ctx.lineWidth = 3 * px;
    ctx.stroke(route.path);
  }

  drawIsochrones();
  useScreenTransform();
  drawCommuneNames();
  drawStops();
  if (app.to) {
    const minutes = app.solution ? formatMinutes(travelTo(app.solution, app.to.point).minutes) : null;
    drawMarker(app.to.point, COLORS.to, app.heatFrom === "to" ? `Arrivée · ${minutes}` : minutes);
  }
  if (app.from) drawMarker(app.from.point, COLORS.from, "Départ");
}

function requestRender() {
  if (app.frameRequested) return;
  app.frameRequested = true;
  requestAnimationFrame(render);
}

function resize() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const first = !app.size.width;
  const ratio = app.size.width ? rect.width / app.size.width : 1;
  app.size = { width: rect.width, height: rect.height, dpr };
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  if (!app.data) return;
  if (first) {
    fitView();
  } else {
    app.view.scale *= ratio;
    app.view.fitScale *= ratio;
  }
  requestRender();
}

// --- État, panneau et URL -------------------------------------------------------

/** Nom de lieu : la station de tram/métro proche si elle existe (plus parlante qu'un arrêt de bus), sinon l'arrêt le plus proche. */
function nearestStopName(point) {
  let best = null;
  let bestDistance = Infinity;
  let rail = null;
  let railDistance = Infinity;
  for (const station of app.data.stations) {
    const d = hypot(point, station.point);
    if (d < bestDistance) {
      bestDistance = d;
      best = station.name;
    }
    if (station.rail && d < railDistance) {
      railDistance = d;
      rail = station.name;
    }
  }
  return railDistance <= RAIL_NAME_RADIUS ? rail : best;
}

function describePlace(point) {
  const stop = nearestStopName(point);
  const commune = communeAt(point);
  return commune && commune !== CITY.name ? `Près de ${stop} (${commune})` : `Près de ${stop}`;
}

function heatSource() {
  return app.heatFrom === "to" && app.to ? app.to : app.from;
}

function recompute({ fast = false } = {}) {
  if (!app.from) return;
  app.solution = solveFrom(app.from.point);
  app.heatSolution = heatSource() === app.from ? app.solution : solveFrom(app.to.point);
  app.grid = computeGrid(app.heatSolution);
  paintHeat(app.grid, { fast });
  updatePanel();
  requestRender();
}

function setFrom(point, label = null, { quiet = false, fast = false } = {}) {
  if (!isOnLand(point)) return false;
  app.from = { point, label: label || describePlace(point) };
  recompute({ fast });
  if (!quiet) syncUrl();
  return true;
}

function setTo(point, label = null, { quiet = false, fast = false } = {}) {
  if (!isOnLand(point)) return false;
  app.to = { point, label: label || describePlace(point) };
  if (app.heatFrom === "to") {
    recompute({ fast });
  } else {
    updatePanel();
    requestRender();
  }
  if (!quiet) syncUrl();
  return true;
}

function removeTo() {
  app.to = null;
  setHeatFrom("from");
  syncUrl();
}

function setHeatFrom(source) {
  app.heatFrom = source === "to" && app.to ? "to" : "from";
  for (const button of $("heatFrom").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.source === app.heatFrom));
  }
  recompute();
}

function updatePanel() {
  $("tripFrom").textContent = app.from?.label ?? "—";
  const result = $("tripResult");
  if (!app.to || !app.solution) {
    result.hidden = true;
    $("tripHint").hidden = false;
  } else {
    const itinerary = buildItinerary(app.solution, app.to.point);
    result.hidden = false;
    $("tripHint").hidden = true;
    $("tripTo").textContent = app.to.label;
    $("tripDuration").textContent = formatMinutes(itinerary.minutes);
    $("tripSteps").replaceChildren(
      ...itinerary.steps
        .filter((step) => step.kind === "ride" || step.minutes >= 0.5)
        .map((step) => {
          const item = document.createElement("li");
          const badge = document.createElement("span");
          badge.className = "badge";
          if (step.kind === "ride") {
            const info = app.data.routeInfo[step.route];
            badge.textContent = info.name;
            badge.style.background = info.color;
            badge.style.color = contrastText(info.color);
            badge.title = routeLabel(step.route);
          } else {
            badge.classList.add("walk");
            badge.textContent = "🚶";
          }
          const text = document.createElement("span");
          text.textContent = step.kind === "ride" ? `${step.text} · attente ~${Math.round(step.wait)} min` : step.text;
          const minutes = document.createElement("span");
          minutes.className = "minutes";
          minutes.textContent = formatMinutes(step.minutes);
          item.append(badge, text, minutes);
          return item;
        }),
    );
  }

  if (app.heatSolution) {
    const source = heatSource();
    const tram = app.data.stations.map((station, index) => ({ station, index })).filter(({ station }) => station.rail);
    const reachable = tram.filter(({ station, index }) => {
      const byFoot = walkMinutes(hypot(source.point, station.point));
      return Math.min(byFoot, app.heatSolution.stationTime[index]) <= REACH_MINUTES;
    }).length;
    const percent = Math.round((reachable / tram.length) * 100);
    const where = source === app.from ? "de ce départ" : "de cette arrivée";
    $("reach").textContent = `${percent} % des ${CITY.railStations} sont à moins de ${REACH_MINUTES} minutes ${where}${
      app.includeBus ? ` (${CITY.railNoun} + ${CITY.busNoun})` : ""
    }.`;
  }
}

function contrastText(hex) {
  const value = parseInt(hex.slice(1), 16);
  const luminance = 0.299 * (value >> 16) + 0.587 * ((value >> 8) & 255) + 0.114 * (value & 255);
  return luminance > 150 ? "#111" : "#fff";
}

function updateLegend() {
  const stops = PALETTE.map(([t, [r, g, b]]) => `rgb(${r}, ${g}, ${b}) ${Math.round(t * 100)}%`);
  $("legendBar").style.background = `linear-gradient(90deg, ${stops.join(", ")})`;
  $("legendMid").textContent = `${Math.round(app.maxMinutes / 2)} min`;
  $("legendMax").textContent = `${app.maxMinutes} min`;
  $("maxValue").textContent = `${app.maxMinutes} min`;
}

function formatPair(point) {
  const { lat, lon } = toLatLon(point);
  return `${lat.toFixed(5)},${lon.toFixed(5)}`;
}

function parsePair(value) {
  const match = /^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/.exec(value || "");
  return match ? toWorld(Number(match[1]), Number(match[2])) : null;
}

function syncUrl() {
  const params = new URLSearchParams();
  if (app.from) params.set("from", formatPair(app.from.point));
  if (app.to) params.set("to", formatPair(app.to.point));
  if (app.to && app.heatFrom === "to") params.set("carte", "arrivee");
  if (app.includeBus) params.set("bus", "1");
  if (app.maxMinutes !== DEFAULT_MAX) params.set("max", String(app.maxMinutes));
  const iso = [...app.isochrones].sort((a, b) => a - b).join(",");
  if (iso !== DEFAULT_ISOCHRONES.join(",")) params.set("iso", iso || "0");
  const query = params.toString().replaceAll("%2C", ",");
  history.replaceState(null, "", query ? `?${query}` : location.pathname);
}

function restoreFromUrl() {
  const params = new URLSearchParams(location.search);
  app.includeBus = params.get("bus") === "1";
  $("busToggle").checked = $("busToggleMap").checked = app.includeBus;
  const max = Number(params.get("max"));
  if (max >= 20 && max <= 90) app.maxMinutes = max;
  $("maxRange").value = String(app.maxMinutes);
  if (params.has("iso")) {
    app.isochrones = params
      .get("iso")
      .split(",")
      .map(Number)
      .filter((value) => ISOCHRONE_OPTIONS.includes(value));
  }
  for (const input of $("isoToggles").querySelectorAll("input")) input.checked = app.isochrones.includes(Number(input.value));
  updateLegend();

  const from = parsePair(params.get("from"));
  if (!from || !setFrom(from, null, { quiet: true })) {
    setFrom(toWorld(DEFAULT_FROM.lat, DEFAULT_FROM.lon), DEFAULT_FROM.label, { quiet: true });
  }
  const to = parsePair(params.get("to"));
  if (to && setTo(to, null, { quiet: true }) && params.get("carte") === "arrivee") setHeatFrom("to");
}

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    element.hidden = true;
  }, 2200);
}

// --- Interactions sur la carte ----------------------------------------------

function eventPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return [event.clientX - rect.left, event.clientY - rect.top];
}

function pointerKind(event) {
  return event.pointerType === "mouse" ? "mouse" : "touch";
}

function markerAt(screen, kind = "mouse") {
  for (const key of ["to", "from"]) {
    if (app[key] && hypot(screen, project(app[key].point)) <= MARKER_HIT_RADIUS[kind]) return key;
  }
  return null;
}

canvas.addEventListener("pointerdown", (event) => {
  const screen = eventPoint(event);
  app.pointers.set(event.pointerId, screen);
  canvas.setPointerCapture(event.pointerId);
  if (app.pointers.size === 2) {
    const [a, b] = [...app.pointers.values()];
    app.drag = { kind: "pinch", distance: hypot(a, b) };
    return;
  }
  const pointer = pointerKind(event);
  const marker = markerAt(screen, pointer);
  app.drag = marker
    ? { kind: "marker", marker, start: screen }
    : { kind: "pan", start: screen, last: screen, moved: false, slop: CLICK_SLOP[pointer] };
  // Saisir un marqueur recentre la heatmap sur lui, comme sur la version parisienne.
  if (marker && marker !== app.heatFrom) setHeatFrom(marker);
});

canvas.addEventListener("pointermove", (event) => {
  const screen = eventPoint(event);
  if (app.pointers.has(event.pointerId)) app.pointers.set(event.pointerId, screen);
  const drag = app.drag;

  if (!drag) {
    canvas.classList.toggle("over-marker", Boolean(markerAt(screen)));
    return;
  }
  if (drag.kind === "pinch" && app.pointers.size === 2) {
    const [a, b] = [...app.pointers.values()];
    const distance = hypot(a, b);
    zoomAt(distance / drag.distance, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
    drag.distance = distance;
  } else if (drag.kind === "marker") {
    const world = unproject(...screen);
    if (drag.marker === "from") setFrom(world, null, { quiet: true, fast: true });
    else setTo(world, null, { quiet: true, fast: true });
  } else if (drag.kind === "pan") {
    if (!drag.moved && hypot(screen, drag.start) < drag.slop) return;
    drag.moved = true;
    canvas.classList.add("panning");
    app.view.cx -= (screen[0] - drag.last[0]) / app.view.scale;
    app.view.cy += (screen[1] - drag.last[1]) / app.view.scale;
    drag.last = screen;
    requestRender();
  }
});

function endPointer(event) {
  app.pointers.delete(event.pointerId);
  const drag = app.drag;
  if (!drag) return;
  if (drag.kind === "pinch") {
    if (!app.pointers.size) app.drag = null;
    return;
  }
  app.drag = null;
  canvas.classList.remove("panning");
  if (event.type === "pointercancel") return;
  if (drag.kind === "pan" && !drag.moved) {
    if (!setTo(unproject(...eventPoint(event)))) toast("Ce point est hors de la Métropole ou sur l'eau.");
  } else if (drag.kind === "marker") {
    recompute();
    syncUrl();
  }
}

canvas.addEventListener("dblclick", (event) => {
  if (markerAt(eventPoint(event)) === "to") removeTo();
});

canvas.addEventListener("pointerup", endPointer);
canvas.addEventListener("pointercancel", endPointer);
canvas.addEventListener(
  "wheel",
  (event) => {
    event.preventDefault();
    const [x, y] = eventPoint(event);
    zoomAt(Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0018)), x, y);
  },
  { passive: false },
);

// --- Commandes ----------------------------------------------------------------

// Changer de ville : le nom de la ville dans le titre ouvre un panneau avec recherche.
const cityPanel = $("cityPanel");
const cityTrigger = $("cityTrigger");
const citySearch = $("citySearch");
const cityItems = [...cityPanel.querySelectorAll(".city-item")];

function setCityPanel(open) {
  cityPanel.hidden = !open;
  cityTrigger.setAttribute("aria-expanded", String(open));
  if (open) {
    citySearch.value = "";
    filterCities();
    citySearch.focus();
  }
}

function filterCities() {
  // Recherche sur le début des mots : « s » donne Saint-Étienne et Strasbourg, « et » Saint-Étienne.
  const query = normalize(citySearch.value);
  for (const item of cityItems) item.hidden = !normalize(item.dataset.name).split(" ").some((word) => word.startsWith(query));
}

cityTrigger.addEventListener("click", (event) => {
  event.stopPropagation();
  setCityPanel(cityPanel.hidden);
});
$("cityClose").addEventListener("click", () => setCityPanel(false));
citySearch.addEventListener("input", filterCities);
citySearch.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  const first = cityItems.find((item) => !item.hidden);
  if (first) location.href = first.href;
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !cityPanel.hidden) {
    setCityPanel(false);
    cityTrigger.focus();
  }
});
document.addEventListener("click", (event) => {
  if (!cityPanel.hidden && !cityPanel.contains(event.target)) setCityPanel(false);
});

$("zoomIn").addEventListener("click", () => zoomAt(1.4, app.size.width / 2, app.size.height / 2));
$("zoomOut").addEventListener("click", () => zoomAt(1 / 1.4, app.size.width / 2, app.size.height / 2));
$("recenter").addEventListener("click", () => {
  fitView();
  requestRender();
});
// L'iPhone ne sait pas passer un élément de page en plein écran : on masque le bouton.
$("fullscreen").hidden = !document.fullscreenEnabled;
$("fullscreen").addEventListener("click", () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else stage.requestFullscreen?.();
});

// La même option sous la carte et sur la carte, où elle se voit sans faire défiler.
for (const id of ["busToggle", "busToggleMap"]) {
  $(id).addEventListener("change", (event) => {
    app.includeBus = event.target.checked;
    $("busToggle").checked = $("busToggleMap").checked = app.includeBus;
    recompute();
    syncUrl();
  });
}

$("isoToggles").addEventListener("change", () => {
  app.isochrones = [...$("isoToggles").querySelectorAll("input:checked")].map((input) => Number(input.value));
  requestRender();
  syncUrl();
});

$("maxRange").addEventListener("input", (event) => {
  app.maxMinutes = Number(event.target.value);
  updateLegend();
  if (app.grid) paintHeat(app.grid);
  requestRender();
  syncUrl();
});

$("swap").addEventListener("click", () => {
  if (!app.to) {
    toast("Posez d'abord une arrivée sur la carte.");
    return;
  }
  [app.from, app.to] = [app.to, app.from];
  setHeatFrom("from");
  syncUrl();
});

$("removeTo").addEventListener("click", removeTo);
$("heatFrom").addEventListener("click", (event) => {
  const source = event.target.closest("button")?.dataset.source;
  if (source && source !== app.heatFrom) {
    setHeatFrom(source);
    syncUrl();
  }
});

$("locate").addEventListener("click", () => {
  if (!navigator.geolocation) {
    toast("La géolocalisation n'est pas disponible.");
    return;
  }
  navigator.geolocation.getCurrentPosition(
    ({ coords }) => {
      if (!setFrom(toWorld(coords.latitude, coords.longitude), "Ma position")) toast("Vous êtes hors de la Métropole.");
    },
    (error) =>
      toast(
        error.code === error.PERMISSION_DENIED
          ? "Position refusée : autorisez la localisation, ou cherchez une adresse."
          : "Impossible d'obtenir votre position : cherchez plutôt une adresse.",
      ),
    // Sans délai maximal, certains navigateurs intégrés (X, Reddit…) n'appellent jamais aucun des deux rappels.
    { timeout: 10000, maximumAge: 60000 },
  );
});

$("share").addEventListener("click", async () => {
  const url = location.href;
  if (navigator.share) {
    try {
      await navigator.share({ title: document.title, url });
      return;
    } catch {
      /* partage annulé : on retombe sur la copie */
    }
  }
  try {
    await navigator.clipboard.writeText(url);
    toast("Lien copié !");
  } catch {
    toast(url);
  }
});

// --- Recherche d'adresse (Base Adresse Nationale) ----------------------------

const searchInput = $("searchInput");
const searchResults = $("searchResults");
let searchTimer = null;
let searchController = null;

function normalize(text) {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** Arrêts de tram dont le nom contient tous les mots tapés. */
function searchStops(query) {
  const words = normalize(query).split(" ");
  return app.data.stations
    .filter((station) => station.rail && words.every((word) => normalize(station.name).includes(word)))
    .slice(0, 3)
    .map((station) => ({
      label: station.name,
      context: `Station · ${station.routes
        .filter((id) => app.data.routeInfo[id]?.rail)
        .map((id) => routeLabel(id))
        .join(", ")}`,
      point: station.point,
    }));
}

/** Photon gives the parts of an address: « 1200 Rue Saint-Denis », « Ville-Marie, Montréal ». */
function photonLabel(properties) {
  const street = [properties.housenumber, properties.street].filter(Boolean).join(" ");
  const label = properties.name || street || properties.city || "";
  const context = [properties.name && street, properties.district, properties.city]
    .filter((part, index, parts) => part && part !== label && parts.indexOf(part) === index)
    .join(", ");
  return { label, context };
}

async function searchAddress(query) {
  const stops = searchStops(query);
  searchController?.abort();
  searchController = new AbortController();
  const params = new URLSearchParams({ q: query, limit: "6", lat: String(DEFAULT_FROM.lat), lon: String(DEFAULT_FROM.lon) });
  if (CITY.geocoder === "photon") {
    const [south, west, north, east] = CITY.searchBbox;
    params.set("lang", "fr");
    params.set("bbox", [west, south, east, north].join(","));
  }
  let payload = { features: [] };
  try {
    const response = await fetch(`${GEOCODER_URL}?${params}`, { signal: searchController.signal });
    payload = await response.json();
  } catch (error) {
    if (error.name === "AbortError" || !stops.length) throw error;
  }
  const addresses = payload.features
    .map((feature) => {
      const [lon, lat] = feature.geometry.coordinates;
      const { label, context } = CITY.geocoder === "photon" ? photonLabel(feature.properties) : feature.properties;
      return { label, context, point: toWorld(lat, lon) };
    })
    .filter((result) => isOnLand(result.point));
  return [...stops, ...addresses].slice(0, 7);
}

function showResults(results) {
  searchResults.replaceChildren(
    ...results.map((result) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = result.label;
      const context = document.createElement("small");
      context.textContent = result.context;
      button.append(context);
      button.addEventListener("click", () => chooseResult(result));
      item.append(button);
      return item;
    }),
  );
  searchResults.hidden = !results.length;
}

function chooseResult(result) {
  searchResults.hidden = true;
  searchInput.value = result.label;
  setFrom(result.point, result.label);
  const [sx, sy] = project(result.point);
  if (sx < 0 || sy < 0 || sx > app.size.width || sy > app.size.height) {
    [app.view.cx, app.view.cy] = result.point;
    requestRender();
  }
}

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const query = searchInput.value.trim();
  if (query.length < 3) {
    searchResults.hidden = true;
    return;
  }
  searchTimer = setTimeout(async () => {
    try {
      showResults(await searchAddress(query));
    } catch (error) {
      if (error.name !== "AbortError") searchResults.hidden = true;
    }
  }, 250);
});

$("searchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = searchInput.value.trim();
  if (query.length < 3) return;
  try {
    const results = await searchAddress(query);
    if (results.length) chooseResult(results[0]);
    else toast("Adresse introuvable dans la Métropole.");
  } catch (error) {
    if (error.name !== "AbortError") toast("La recherche d'adresse ne répond pas.");
  }
});

document.addEventListener("click", (event) => {
  if (!$("searchForm").contains(event.target)) searchResults.hidden = true;
});

// --- Démarrage ----------------------------------------------------------------

async function init() {
  resize();
  const response = await fetch(DATA_URL);
  app.data = await response.json();
  app.offset = [app.data.meta.bounds[0], app.data.meta.bounds[1]];
  app.graph = prepareGraph(app.data);
  app.rivers = indexRivers(app.data.rivers);
  app.paths = buildPaths(app.data);
  app.size.width = 0;
  resize();
  restoreFromUrl();
  new ResizeObserver(resize).observe(canvas);
}

init().catch((error) => {
  console.error(error);
  $("tripFrom").textContent = "Impossible de charger le réseau.";
});
