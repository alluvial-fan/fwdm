# Feature Walking Distance Map

This project takes public data, converts it to GeoJSON, and maps it with LeafletJS.

## About (Story)

Welcome to the Montreal distance-from-feature locator map.

While we don't pick where we're born, later in life we get to pick where we will live; this map aims to make that process ever so slightly easier. 

Currently, a prospective homebuyer or renter in Montreal cannot easily get a good idea of which areas of the city are located within reasonable walking distances from physical features of interest to them.

This map is for them, and attempts to map areas based on their walking distance proximity to two types of sought after features, metro stations, and green spaces.

On the site, you can specify which features you care about (metros, parks, or both), and filter according to distance ranges, specific green space types and sizes, and whether or not a metro station is wheelchair friendly or not. Each query will modify the map and narrow it down, attempting to clearly show you how different areas rank.

## State of the project

This project will not cover the entirety of the island (we are not including independent municipalities), nor will it be adding significantly more features or fine-graining to the filtering options.

The two datasets I started with are metro stops and parks (with street networks to calculate intersections).

### MoSCoW state

Core action: a prospective Montreal renter or buyer can see which street corners are within a chosen walking time of the parks and/or metro stations they care about.

A user on my site must:
- Be able to see where parks and metro stations are.
- Be able to see which street corners are within walking distance of the features they choose.
- Be able to tell which areas are farther away than the time they set (e.g. 15 minutes).

A user on my site should:
- Be able to specify the type of green space and the kind of metro entrance they are interested in.
- Be able to see extra data about each feature when they click it.

A user on my site could (in a later iteration):
- Add food stores as a third feature type.

My project does not, and will not (this term):
- Cover the whole island (the independent municipalities).
- Add significantly more feature types or finer filter options.

### Not in this iteration

While I have the data for non-Montreal municipalities on the island of Montreal, and I have data for an additional feature type (food stores), the project as it stands does not, and will not for the foreseeable future, include them. Doing so would require handling data taken from OpenStreetMap, which is user-submitted and would need significantly more checks and validation than the current pre-existing data straight from the source. The one thing I will build next is adding the food stores as a third feature.

### How to run

The site can be run by running VS Code (or VSCodium)'s "Live Server" extension on [index.html](site/index.html).

Note: running the html directly in browser won't work.

## Data sources

### Metro Stops (data/metro_stops)

Taken from the STM developer homepage ([www.stm.info/en/about/developers](https://www.stm.info/en/about/developers)).

File URL: [www.stm.info/sites/default/files/gtfs/gtfs_stm.zip](https://www.stm.info/sites/default/files/gtfs/gtfs_stm.zip)

Within `gtfs_stm.zip` lies `stops.txt`, a file which contains the location data for the STM's metro and bus stops.

**Licence:** CC BY 4.0 (https://www.stm.info/en/about/developers/terms-use).

### Parks (data/city_parks)

Taken from the Montreal City's open data portal ([donnees.montreal.ca/fr/dataset/grands-parcs-parcs-d-arrondissements-et-espaces-publics](https://donnees.montreal.ca/fr/dataset/grands-parcs-parcs-d-arrondissements-et-espaces-publics)).

Data available as CSV, JSON and SHP files; we'll be using the JSON `espace_vert.json` as it doesn't require extra processing.

| Type | URL |
| --- | --- |
| JSON | [donnees.montreal.ca/dataset/2e9e4d2f-173a-4c3d-a5e3-565d79baa27d/resource/35796624-15df-4503-a569-797665f8768e/download/espace_vert.json](https://donnees.montreal.ca/dataset/2e9e4d2f-173a-4c3d-a5e3-565d79baa27d/resource/35796624-15df-4503-a569-797665f8768e/download/espace_vert.json) |

**Licence:** CC BY 4.0 (https://donnees.montreal.ca/pages/licence-d-utilisation).

### Neighbourhoods (data/neighbourhoods)

Taken from the Montreal City's open data portal ([donnees.montreal.ca/fr/dataset/limites-administratives-agglomeration](https://donnees.montreal.ca/fr/dataset/limites-administratives-agglomeration)).

Two formats, GeoJSON and SHP, each in NAD83/MTM zone 8 and WGS 84 (four files).

| Type | URL |
| --- | --- |
| GeoJSON (NAD83/MTM zone 8) | [donnees.montreal.ca/dataset/9797a946-9da8-41ec-8815-f6b276dec7e9/resource/6b313375-d9bc-4dc3-af8e-ceae3762ae6e/download/limites-administratives-agglomeration-nad83.geojson](https://donnees.montreal.ca/dataset/9797a946-9da8-41ec-8815-f6b276dec7e9/resource/6b313375-d9bc-4dc3-af8e-ceae3762ae6e/download/limites-administratives-agglomeration-nad83.geojson) |
| SHP (NAD83/MTM zone 8) | [donnees.montreal.ca/dataset/9797a946-9da8-41ec-8815-f6b276dec7e9/resource/e8bea324-044c-4544-bbd1-2e0d08a24216/download/limites-administratives-agglomeration-nad83.zip](https://donnees.montreal.ca/dataset/9797a946-9da8-41ec-8815-f6b276dec7e9/resource/e8bea324-044c-4544-bbd1-2e0d08a24216/download/limites-administratives-agglomeration-nad83.zip) |
| GeoJSON (WGS84) | [donnees.montreal.ca/dataset/9797a946-9da8-41ec-8815-f6b276dec7e9/resource/e18bfd07-edc8-4ce8-8a5a-3b617662a794/download/limites-administratives-agglomeration.geojson](https://donnees.montreal.ca/dataset/9797a946-9da8-41ec-8815-f6b276dec7e9/resource/e18bfd07-edc8-4ce8-8a5a-3b617662a794/download/limites-administratives-agglomeration.geojson) |
| SHP (WGS84) | [donnees.montreal.ca/dataset/9797a946-9da8-41ec-8815-f6b276dec7e9/resource/93385165-d849-418f-aaa3-0497ed9e4702/download/limites-administratives-agglomeration.zip](https://donnees.montreal.ca/dataset/9797a946-9da8-41ec-8815-f6b276dec7e9/resource/93385165-d849-418f-aaa3-0497ed9e4702/download/limites-administratives-agglomeration.zip) |


**Licence:** CC BY 4.0 (https://donnees.montreal.ca/pages/licence-d-utilisation).

### Food stores (data/food_stores)

Taken from OpenStreetMap using the Overpass Turbo API ([overpass-turbo.eu/](https://overpass-turbo.eu/)).

Fetches all `supermarkets`, `convenience stores`, `frozen food` stores, and `greengrocers` within what OSM identifies as Montreal.

```
/*
This has been generated by the overpass-turbo wizard.
The original search was:
“(shop=supermarket or shop=convenience or shop=frozen_food or shop=greengrocer) in Montreal”
*/
[out:json][timeout:25];
// fetch area “Montreal” to search in
{{geocodeArea:Montreal}}->.searchArea;
// gather results
(
  nwr["shop"="supermarket"](area.searchArea);
  nwr["shop"="convenience"](area.searchArea);
  nwr["shop"="frozen_food"](area.searchArea);
  nwr["shop"="greengrocer"](area.searchArea);
);
// print results
out geom;
```

**Licence:** ODbL (https://www.openstreetmap.org/copyright).

### Street Network (data/street_network)

aka Geobase

Taken from the Montreal City's open data portal ([donnees.montreal.ca/dataset/geobase](https://donnees.montreal.ca/dataset/geobase)).

Three files, a CSV, a ZIP, and a GeoJSON.


| File | URL |
| --- | --- |
| CSV | [donnees.montreal.ca/dataset/984f7a68-ab34-4092-9204-4bdfcca767c5/resource/cdf491fa-bf3d-4eb1-8692-c9aa1df462cd/download/geobase.csv](https://donnees.montreal.ca/dataset/984f7a68-ab34-4092-9204-4bdfcca767c5/resource/cdf491fa-bf3d-4eb1-8692-c9aa1df462cd/download/geobase.csv) |
| ZIP | [donnees.montreal.ca/dataset/984f7a68-ab34-4092-9204-4bdfcca767c5/resource/70c1f8c7-91a0-4553-b602-89c3edb959b5/download/geobase.zip](https://donnees.montreal.ca/dataset/984f7a68-ab34-4092-9204-4bdfcca767c5/resource/70c1f8c7-91a0-4553-b602-89c3edb959b5/download/geobase.zip) |
| GeoJSON | [donnees.montreal.ca/dataset/984f7a68-ab34-4092-9204-4bdfcca767c5/resource/9d3d60d8-4e7f-493e-8d6a-dcd040319d8d/download/geobase.json](https://donnees.montreal.ca/dataset/984f7a68-ab34-4092-9204-4bdfcca767c5/resource/9d3d60d8-4e7f-493e-8d6a-dcd040319d8d/download/geobase.json) |

**Licence:** CC BY 4.0 (https://donnees.montreal.ca/pages/licence-d-utilisation).

## Approach

`site/index.html` is a LeafletJS map of the 19 boroughs of the city of Montreal. It reads GeoJSON data files for metro points, green spaces, street corners (with walk times), and borough outlines (for masking). A coloured dot is applied for each intersection with the time requested by the user per feature type.

### Steps

| Step | Command | Reads | Writes |
| --- | --- | --- | --- |
| 1 | n/a | n/a | `data/metro_stops/gtfs_stm.zip`, `data/city_parks/espace_vert.json`, `data/street_network/geobase.json`, `data/neighbourhoods/limites-administratives-agglomeration*.geojson` |
| 2 | `python site/build.py geojson` | `data/metro_stops/gtfs_stm.zip`, `data/city_parks/espace_vert.json`, `data/street_network/geobase.json` | `site/data/metro_stops.geojson` (68 station points and 134 entrances), `site/data/city_parks.geojson` (1981 green spaces), `site/data/intersections.geojson` (24,669 intersections) |
| 3 | `python site/build.py walks` | `data/street_network/geobase.json`, `data/neighbourhoods/limites-administratives-agglomeration.geojson.json`, and step 2's three output geojsons| a rewritten `site/data/intersections.geojson` with walk times written into the corners, and `site/data/boroughs.geojson` for the visual masking and borough labelling |

Before steps 2 and 3, `python site/build.py check` holds the projection every distance in this project is measured in (MTM zone 8, EPSG:32188) against the city's own metres: it projects the city's 34 outlines and compares them with the city's own MTM copy, point for point, in both directions. It reads no built file and writes nothing, so it is the first thing to run when a distance looks wrong.

Within a venv and from the project folder:
```bash
python site/build.py # the projection check, step 2 and step 3, in that order
node site/app.js 
```

Naming a step runs only that one, always in the order above: `python site/build.py walks` is enough after changing a walking speed, a park band or a size (about a minute), because step 2's files are unchanged. Step 3 reads the intersections file step 2 writes, so a step 2 change needs `python site/build.py geojson` and then `walks`.

### Checks

`site/build.py` checks each file it writes before that file takes the place of the one the page loads: every output is written to a temporary name, read back off the disk, checked as it was written, and only then moved into place, so a failed run leaves the page with the files it already had.

- Every question the page's filters can ask has a layer of its own (22 layers: 20 green-space combinations and 2 metro ones), so the page can never ask for something the build did not measure.
- A corner is either given a walk or marked with the reason it has none. The build writes four statements (outside the 34 outlines, no network link, no reachable destination, unreachable); `check_statements()` checks the first two dot by dot and by count. On this run 0 corners were outside and 0 had no link, in all 22 layers.
- No corner is quicker to a step-free entrance than to any entrance, at least one corner reaches a step-free entrance, and the any-entrance layer has a walk under an hour (`check_reach()`). On this run 24,438 of 24,669 corners reach a step-free entrance.
- A derived layer is the minimum of its parents on every 97th corner, and the destination number carried is the parent's. This checks that "Any" is a real minimum across kinds and bands (`check_derived()`).
- On a fixed sample of 364 walks over the 14 searched layers, no walk is shorter than the crow-flies distance to the park edge or station entrance it reached, less one metre of slack (`check_crow_flies()`); the closest sample was 2 m longer than the crow flies.
- The two ways of deciding whether a corner is inside the city (one corner at a time in `in_city()`, and all at once in `mark_focus()`) agree about every corner: 24,669 inside, 19,360 in the 19 drawn boroughs and 5,309 in the 15 masked linked cities, with no corner counted twice. The 19 boroughs the page draws and the 19 the build measured are compared by name.
- Each step 2 output is read back before it is moved into place: 202 metro features (68 stations, 134 entrances), 1,981 parks, and 24,669 corners, with the fields the page reads.

It also prints the nine metro entrances it could not attach to a street within 100 m, with their label and station, instead of guessing a walk for them: two at Vendôme (M70/M71, inside Côte-des-Neiges–NDG), two at Cartier, one at Concorde, one at Montmorency, and three at Longueuil–Université-de-Sherbrooke. The seven outside Vendôme are in Laval and Longueuil, outside the 19 drawn boroughs. A station inside the city with no attached entrance at all stops the build (Vendôme is not one: its other two entrances attach), and both files written are read back and checked before the run ends.

`node site/app.js` reads `site/data/intersections.geojson` through `readWalkData()`, the same function used by the page, and runs 212 checks with no browser. A passing run ends with `all 212 checks passed` and exit code 0; a failure prints what differed and exits 1.

#### Human Tests

The notebook's check cell passes (202 metro points, 1,981 parks, 24,669 corners, 22 walk layers) and `node site/app.js` ends with `all 212 checks passed`. On asking a friend to test out the site, I realized that when refreshing the filter labels didn't change, but the data did and reset. Page didn't register the filters until they were changed once again. Following that I implemented a check that will keep the filters active, but ensure they get displayed.

### Limitations

Distances to metro stations are really distances to the entrance, meaning that actual distance from entrance to the platform will increase distance slightly and in an unequal fashion between each station. The street file only has "real" roads, meaning no shortcuts or pathways through parks or other public land will be calculated. It also assumes a constant walking speed and doesn't take into consideration construction or walking up hills or being stopped at traffic lights. Some street types (class 8, motorways) are ignored across the board as they SHOULD all be expressways pedestrians cannot walk on. Two of Vendôme's four entrances (M70/M71) are not attached to the street network; the station is still reachable through its other two entrances.

## A2 AI Disclosure

**Lane:** Blue (bring your own agent).

This project was built with an AI coding agent: `Cline` inside `VSCodium`, using the `DeepSeek` API with the `V4.X Flash` and `V4 Pro` models. I used it first as a brainstorming aid to plan the walk-time calculations, then gave it small, specific steps: turn the raw data into GeoJSON, keep only the fields the map needs, draw the results, and repeat.

The agent often needed several tries to understand the scope I wanted, and it sometimes hallucinated instructions, especially when I asked it to limit what it processed. For every answer I checked it was solving the right problem, then ran a Live Server to confirm any map change did what I expected. I kept the project in git and committed after each stage I trusted, so there was always a fallback.

The main dead end was drawing polygons for every city block; I rejected that for static street-corner points, which were simpler to calculate and display. Throughout, the problem, the audience, the core action, and every decision to accept, change, or reject what the agent produced stayed mine.

## After the demo
At my demo, the one thing I don't think I could accurately explain is how the dots color themselves when not park green or metro blue.

The one thing I want to fix with my code is simplifying it. It works and I can "understand" what happens, but it's fairly heavy and I think there'd be a way to make it lighter.

### Five-second test

_For context, the map was open on the metro-only layer._

"Map (based on title) talks about looking at walking distance to green spaces. From looking at it in 5 seconds, I cannot tell what the different colours mean."
