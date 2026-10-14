# À portée de tram

Cartes interactives des temps de trajet en **tram et métro** (et, en option, en **bus**) dans les grandes villes françaises, à Bruxelles et à Montréal.

👉 **https://tram.camilleroux.com/**

25 villes : [Angers](https://tram.camilleroux.com/angers/) · [Besançon](https://tram.camilleroux.com/besancon/) · [Bordeaux](https://tram.camilleroux.com/bordeaux/) · [Brest](https://tram.camilleroux.com/brest/) · [Bruxelles](https://tram.camilleroux.com/bruxelles/) · [Clermont-Ferrand](https://tram.camilleroux.com/clermont-ferrand/) · [Dijon](https://tram.camilleroux.com/dijon/) · [Grenoble](https://tram.camilleroux.com/grenoble/) · [Le Mans](https://tram.camilleroux.com/le-mans/) · [Lille](https://tram.camilleroux.com/lille/) · [Lyon](https://tram.camilleroux.com/lyon/) · [Marseille](https://tram.camilleroux.com/marseille/) · [Montpellier](https://tram.camilleroux.com/montpellier/) · [Montréal](https://tram.camilleroux.com/montreal/) · [Nantes](https://tram.camilleroux.com/nantes/) · [Nice](https://tram.camilleroux.com/nice/) · [Orléans](https://tram.camilleroux.com/orleans/) · [Paris](https://tram.camilleroux.com/paris/) · [Reims](https://tram.camilleroux.com/reims/) · [Rennes](https://tram.camilleroux.com/rennes/) · [Rouen](https://tram.camilleroux.com/rouen/) · [Saint-Étienne](https://tram.camilleroux.com/saint-etienne/) · [Strasbourg](https://tram.camilleroux.com/strasbourg/) · [Toulouse](https://tram.camilleroux.com/toulouse/) · [Tours](https://tram.camilleroux.com/tours/)

Idée originale : le [NYC Transit Time Cartogram](https://castrio.me/nyc/) d'Anthony Castrio, puis sa
[déclinaison parisienne](https://github.com/JulesGrandin/paris-temps-transport) par Jules Grandin.

Fonctionnalités : heatmap et isochrones depuis un départ déplaçable, arrivée au clic avec itinéraire détaillé
(lignes, correspondances, marche), recherche d'adresse (Base Adresse Nationale) ou de station, tram seul ou
tram + bus, déplacement et zoom de la carte, lien de partage.

## Lancer

```bash
python3 build.py --fetch            # télécharge les sources, calcule chaque ville, génère pages et images d'aperçu
python3 build.py lyon nice --no-og  # seulement ces villes, sans régénérer les images
python3 -m http.server 8000 --directory site
```

Puis ouvrir [http://localhost:8000](http://localhost:8000). `build.py` affiche à la fin un tableau de contrôle par ville
(jour de référence, poids, part du réseau à moins de 30 min, station la plus éloignée, fréquences).

Étapes séparées si besoin : `fetch_data.py <ville>`, `build_data.py <ville>`, `build_pages.py`,
`tools/render_og.py <ville>|home|classements|all` (Chrome et ImageMagick requis), `tools/rankings.py` (classements
lus directement dans les horaires : dernier tram du samedi soir au centre, fréquence à l'heure de pointe, station la plus desservie, ligne la plus longue,
trajets par jour ; écrit `sources/rankings.json`, publié sur `/classements/` avec une page par classement). Une ville avec `"rankingsOnly": true` (et
`externalUrl` vers sa carte) figure dans les classements sans avoir de carte ici. À Paris, les classements comptent le métro et le
tram, sans le RER (mode `rer`) ni le Transilien (mode `train`) que montre la carte. `node tools/check_trips.mjs <ville>` sonde les
trajets depuis le centre jusqu'aux terminus et aux gares, et signale les vitesses anormales.

Les sources brutes (`data/<ville>/` : GTFS, communes, OSM) ne sont pas versionnées : elles restent en local et
`fetch_data.py <ville>` les retélécharge. Seules les données calculées pour le site (`site/data/<ville>.json`) le sont.

## Provenance des données

Chaque calcul écrit `sources/<ville>.json` (versionné) : URL de chaque fichier source, date de téléchargement, taille et
empreinte SHA-256, période couverte par le GTFS, jour de référence retenu, lignes exclues. `fetch_data.py` tient à jour
le détail des téléchargements dans `data/<ville>/manifest.json`.

Le tableau complet (licence, date de téléchargement, validité du GTFS, jour de référence pour chaque ville) est
généré dans [sources/README.md](sources/README.md).

Particularités : le GTFS TCL (Lyon) se télécharge à la main sur data.grandlyon.com (compte requis) ; les GTFS Tisséo
(Toulouse) et STAR (Rennes) ne couvrent que quelques semaines et sont à retélécharger souvent ; à Marseille et dans
les nouvelles villes (`"communes": "served"`), la carte se limite aux communes réellement desservies.
Options de carte : `"arrondissements": "<code INSEE>"` trace les arrondissements municipaux dans la commune (Marseille),
et `"view": "stops"` cadre la vue initiale sur tous les arrêts, bus compris, plutôt que sur le seul réseau tram/métro.
`"viewBbox": [sud, ouest, nord, est]` impose ce cadrage, `"gridCellMeters"` agrandit les cases de la carte (200 m par défaut ; 300 m pour Montréal, très étendue), et `"stopsBbox"` coupe les trajets aux arrêts de ce rectangle
(GTFS régional d'Île-de-France : le RER va jusqu'à Creil). `routeModes` accepte un `route_id` quand le nom est ambigu
(RER A et bus A). `originalMap` met en avant, en tête de page, une carte qui existait avant celle-ci (Paris : celle de
Jules Grandin).
`"rivers": ["La Loire", "L'Erdre"]` rend ces cours d'eau (et leurs bras, « La Loire - Bras de Pirmil ») infranchissables
à pied ailleurs que sur un pont : la marche passe par le meilleur pont OSM, sinon il faut prendre le tram, le bus ou le
bateau (`fetch_data.py <ville> --rivers-only` télécharge cours d'eau et ponts).

## Organisation du site

- `/` : accueil, avec la liste des villes, le mode d'emploi et la FAQ. Les anciens liens de partage de Montpellier
  (`/?from=…&to=…`) sont redirigés vers `/montpellier/`.
- `/<ville>/` : carte, chiffres clés et FAQ de la ville, calculés à partir de `sources/<ville>.json`.
- Modèles : `templates/home.html` et `templates/city.html`, assemblés par `build_pages.py`.

## Ajouter une ville

1. Créer `cities/<ville>.json` avec l'essentiel : `slug`, `order`, `name`, `kind` (`tram` par défaut, `metro` ou
   `metro+tram`), `network`, `metropole`, `epci` (SIREN de l'intercommunalité), `gtfsUrl`, `gtfsDataset`, `gtfsLicence`
   (`lo`, `odbl` ou `mobilites`), `defaultFrom` (centre de la carte), `searchExample`, `published`.
   `cities.py` déduit le reste (titres, libellés, zones OSM…) ; chaque valeur peut être surchargée dans le JSON.
2. Options utiles : `"communes": "served"` (seulement les communes desservies), `routeModes` (corriger le mode d'une
   ligne), `modeAccess` (temps d'accès au quai), `agencies` (filtrer un GTFS régional), `railGeometry: "osm"` et
   `osmRefAliases` (GTFS sans tracés), `seaDepartments` / `contextOsmRelations` (villes côtières : la mer en bleu).
   Hors de France (Montréal) : `country` (code pays, `FR` par défaut ; les villes étrangères ont une carte mais restent
   hors des classements), `communesOsm` (identifiants des relations OSM des communes ou arrondissements, à la place
   d'`epci`), recherche d'adresse via Photon (komoot, OSM) au lieu de la BAN, `stopNameRewrites` (paires regex →
   remplacement pour nettoyer les noms d'arrêts), `stopNameLanguage` (noms tirés de `translations.txt`, STIB), licence
   `ccby` (CC BY 4.0).
   Plusieurs GTFS (REM et trains exo à côté de la STM) : `gtfsExtra` (`slug`, `network`, `url`, `dataset`, `licence`) les fusionne dans
   `data/<ville>/gtfs_merged.zip`, identifiants préfixés par le slug (`rem:S1`) ; `gtfsNetwork` nomme le flux principal,
   `mergeRoutes` regroupe les services d'une même ligne, `routeNames` renomme une ligne.
3. `python3 build.py <ville> --fetch`, puis vérifier le tableau de contrôle et `node tools/check_trips.mjs <ville>`.

## Données

- GTFS théoriques des réseaux via [transport.data.gouv.fr](https://transport.data.gouv.fr/) (TCL via [data.grandlyon.com](https://data.grandlyon.com/)), sous Licence Ouverte, ODbL ou Licence Mobilités selon les réseaux
- Tracés des lignes (quand le GTFS n'en fournit pas), eau et parcs : © contributeurs OpenStreetMap (ODbL), via Overpass
- Contours des communes de chaque métropole ([geo.api.gouv.fr](https://geo.api.gouv.fr/))
- Recherche d'adresse côté navigateur : [api-adresse.data.gouv.fr](https://adresse.data.gouv.fr/)
- Mesure d'audience : Cloudflare Web Analytics (sans cookie)

## Modèle

Les temps viennent des horaires GTFS d'un mardi ou jeudi de semaine scolaire type, entre 7 h et 20 h :

- jour de référence = programme de service le plus courant parmi les mardis et jeudis à venir bien remplis ;
- durée de chaque inter-station = médiane des durées planifiées ;
- attente = moitié de l'intervalle moyen entre deux passages à l'arrêt (bornée entre 1 et 15 min) ;
- correspondance = 1,5 min de marche + attente de la ligne suivante ; marche possible entre arrêts proches (< 450 m) ;
- marche à pied à 75 m/min (4,5 km/h) à vol d'oiseau, sans pénalité d'accès (arrêts en surface).

Pas de temps réel ni de perturbations. Les trajets à la demande (TaD) sont exclus.

## Licences

- Code : licence MIT (voir [LICENSE](LICENSE)).
- Données calculées (`site/data/*.json`, `sources/*.json`) : bases de données dérivées publiées sous
  [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), comme l'exigent OpenStreetMap et les GTFS sous ODbL ou
  Licence Mobilités.
- Mentions légales et licence de chaque source : https://tram.camilleroux.com/mentions-legales/

