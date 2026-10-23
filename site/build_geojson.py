'''
build_geojson.py

Turn the raw project data into small GeoJSON files the web page can load.

Step 2 of the project pipeline: read the raw files (read-only) and write
files the page can load. Three sources are done so far: the metro stops, the
city's green spaces, and the street intersections the step 3 map measures
from. Later steps add the food stores and the block distances. Which file
becomes which is the README's steps table.

The metro output keeps every raw column the STM exported and adds four
popup_* keys next to them (the clean name, the point type, the STM's own word
on an entrance's wheelchair accessibility, and the STM page URL), so the page
can build its popups without any raw data being thrown away. Only the stations
and the entrances become points. The STM's platform rows are read, because the
clean station name and the STM page sit on them, but they are not written: a
platform is underground, and the page never draws one.

The intersections output has one point per intersection: a place where three
or more street ends meet, once the ends the file draws a centimetre or two
apart for the same corner have been snapped together and the motorways have
been dropped. That is the set of places a walk can turn at, so it is what the
step 3 map measures from. The points are numbered in a fixed order rather than
in whatever order the file is read in, so two runs hand out the same numbers.

That file is also where step 3 writes its walks: it adds three properties to
each corner - the outline it stands in, whether the page draws it, and its 22
walks - and leaves everything else here - the points, the numbers, the order,
the popups - exactly as this file wrote it, so `site/data/` holds one
intersections file rather than a map of corners and a table of walks that can
disagree. Run this file again after changing the corners and step 3 has to run
again after that, which the README's steps table says.

Every output is checked by reading the file back, and the checks run before
the file is moved into place, so a run that fails leaves the output the page
loads untouched.

Run it with:  ./.venv/Scripts/python.exe site/build_geojson.py
'''

import collections
import csv
import io
import json
import math
import re
import zipfile
from pathlib import Path

# The projection lives in network.py, which also checks it against the city's
# own metres. Only that one name is taken: this file has a `network` of its own
# inside check_intersections, which is the street graph rather than the module.
from network import to_metres

# Paths are worked out from this file's own location, so it does not matter
# which folder you run it from.
SITE_DIR = Path(__file__).resolve().parent          # .../project/site
PROJECT_DIR = SITE_DIR.parent                       # .../project

METRO_ZIP = PROJECT_DIR / 'data' / 'metro_stops' / 'gtfs_stm.zip'
METRO_OUT = SITE_DIR / 'data' / 'metro_stops.geojson'
PARKS_JSON = PROJECT_DIR / 'data' / 'city_parks' / 'espace_vert.json'
PARKS_OUT = SITE_DIR / 'data' / 'city_parks.geojson'
STREETS_JSON = PROJECT_DIR / 'data' / 'street_network' / 'geobase.json'
INTERSECTIONS_OUT = SITE_DIR / 'data' / 'intersections.geojson'

# A metro stop_code is five digits starting with 10 (10118 = Angrignon).
METRO_CODE = re.compile(r'10[0-9]{3}')

# Station names as the STM writes them: a platform row reads "Station
# Beaubien", so the "Station " prefix and any trailing "-Zone B" are stripped
# to leave the clean "Beaubien". The access detail marker is written as an
# escape, so it does not depend on how this file itself is saved.
STATION_PREFIX = re.compile(r'^Station\s+', re.IGNORECASE)
ZONE_SUFFIX = re.compile(r'\s*-\s*Zone\s+[A-Za-z]\s*$')
ACCES_DETAIL = re.compile('Acc\u00e8s')  # accent written as an escape

# GTFS wheelchair_boarding as the STM fills it in on its entrances, and the
# line the popup shows for each. The STM uses two values: 1 says the entrance
# is accessible and 2 says it is not. Anything else - 0, which GTFS defines as
# "no information", or a blank - means the STM has said nothing about the
# entrance, so the popup shows no accessibility line at all rather than
# claiming that an entrance it has not judged is not accessible.
WHEELCHAIR_WORDING = {
    '1': 'Wheelchair accessible',
    '2': 'Not wheelchair accessible',
}


# Street right-of-way spaces: the city lists the strips along its streets
# ("Espace voirie") - islands, medians, grass verges, shared streets, green
# alleys - among its green spaces. They are not parks, so the parks file
# leaves them out. The raw city file still has every row.
STREET_SPACE_TYPE = 'Espace voirie'


# --- Park popups: the wording the page shows ------------------------------

# The city classifies every green space with TYPO1. The page colours the
# parks by that classification, so each one gets a short key (the page knows
# no French) and the English wording the popup shows. The broadest kind comes
# first here, which is the order the kinds are read in everywhere below; which
# polygon is drawn on top of which is settled by size instead, in
# sort_park_features.
PARK_KINDS = [
    ('Grand parc', 'grand_parc', 'City Park'),
    ("Parc d'arrondissement", 'arrondissement', 'Borough Park'),
    ('Autre espace vert', 'autre', 'Other green space'),
    ('BD - Jardin', 'jardin', 'Garden'),
    ('En cours de validation', 'pending', 'Pending validation'),
]

# Three rows carry no TYPO1 at all: they still get a key, so the page can
# draw them, but no wording - an honest dash beats a made-up type.
PARK_KIND_UNKNOWN = 'unknown'

# Built from the table above: one lookup for the popup wording and the
# colour key, used by both the popup fields and the checks below.
PARK_KIND_BY_NAME = {name: (key, word) for name, key, word in PARK_KINDS}

# The city Type column, in English. It is a much rougher column than TYPO1:
# 43 values, and 697 rows leave it empty. So it only decorates the name - when
# it is empty the popup falls back to "(Park)" for the rows TYPO1 calls a park,
# and to the bare name otherwise. A value the city adds later that is missing
# here still prints, in the city's own wording. Accents are written as escapes,
# so they do not depend on how this file itself is saved.
PARK_TYPES = {
    'Parc': 'Park',
    'Grand parc': 'City Park',
    'Parc-nature': 'Nature Park',
    'Parc-\u00e9cole': 'School Park',
    'Parc-\u00e9cole primaire': 'Primary school park',
    'Parc lin\u00e9aire': 'Linear park',
    'Parc-canin': 'Dog park',
    'Parc \u00e9cran': 'Screen park',
    'Futur parc': 'Future park',
    'Mini-parc': 'Mini Park',
    'Place': 'Plaza',
    'Placette': 'Pocket plaza',
    'Promenade': 'Promenade',
    'Square': 'Square',
    'Carr\u00e9': 'Square',
    'Esplanade': 'Esplanade',
    'Golf': 'Golf course',
    'Tennis': 'Tennis courts',
    'Stade': 'Stadium',
    'Aire de repos': 'Rest area',
    "Aire d'exercice": 'Exercise area',
    'Terrasse': 'Terrace',
    'Belv\u00e9d\u00e8re': 'Lookout',
    'Cimeti\u00e8re': 'Cemetery',
    'Mairie': 'Borough hall',
    'Gare': 'Station',
    'Mus\u00e9e': 'Museum',
    '\u00c9cole': 'School',
    'C\u00e9gep': 'CEGEP',
    'Coll\u00e8ge': 'College',
    'Usine': 'Plant',
    'Marina': 'Marina',
    'Av.': 'Avenue',
    'Jardin': 'Garden',
    'Toit-vert': 'Green roof',
    'Bois\u00e9': 'Woodland',
    'Domaine': 'Estate',
    'Complexe': 'Complex',
    'Espace': 'Space',
    '\u00cele': 'Island',
    '\u00celot': 'Block',
    'Seuil': 'Doorstep',
    'CHSLD': 'Care home',
}


# The city keeps one park name in three columns: Type is the head noun, Lien is
# the linking word and Nom is the distinctive part, so "Parc" + "des" +
# "Muguets" is the Parc des Muguets. Where the city filled in both the Type and
# the Lien we show the name it spells out, in French, and the page sets it in
# italics to mark it as the city's own wording. Lien ends in an apostrophe on
# the rows that read "de l'Ouest", and nothing goes between an apostrophe and
# what follows it.
NAME_JOIN_PARTICLES = {
    'de', 'du', 'des', 'de la', "de l'", "d'", 'le', 'la', 'les', 'au', 'aux',
}

# 39 rows carry a Lien but no Type, and every one of them is TYPO1
# "Parc d'arrondissement", so the missing head noun is the city's own word for
# them and is restored from it: "Parc" + "des" + "Muguets" again. The one row
# whose Lien is instead the noun "place" keeps the English wording in
# add_park_popup_fields below rather than being forced into the pattern.
PARK_NAME_HEAD_WHEN_UNTYPED = 'Parc'


# --- Metro stops: read the zip, write the station and entrance points ---

def read_metro_rows():
    '''Open the zip, read stops.txt, and split out the metro rows.

    Returns the rows that become points - the station and entrance rows - and
    the platform rows, which are not written: a platform is underground, so the
    page never draws one. They are still read, because the clean station name
    and the STM page URL sit on them rather than on the station row.
    '''
    with zipfile.ZipFile(METRO_ZIP) as archive:
        text = archive.read('stops.txt').decode('utf-8-sig')
    all_rows = list(csv.DictReader(io.StringIO(text)))
    metro_rows = [r for r in all_rows if METRO_CODE.fullmatch(r['stop_code'] or '')]
    rows = [r for r in metro_rows if r['location_type'] != '0']
    platforms = [r for r in metro_rows if r['location_type'] == '0']
    print(f'{len(all_rows)} rows in stops.txt, {len(metro_rows)} are metro rows: '
          f'{len(rows)} stations and entrances written, '
          f'{len(platforms)} platform rows read for their names and URLs')
    return rows, platforms


def build_metro_features(rows):
    '''One GeoJSON Point per station or entrance row. Coordinates that cannot be
    read are skipped and counted, rather than silently kept.'''
    features = []
    skipped = 0
    for row in rows:
        assert row['location_type'] in ('1', '2'), 'a platform row got this far'
        try:
            lon = float(row['stop_lon'])
            lat = float(row['stop_lat'])
        except (ValueError, TypeError):
            skipped += 1
            continue

        feature = {
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},  # longitude first
            # Keep every original column for now, so nothing is lost while we
            # are still deciding what the page needs.
            'properties': dict(row),
        }
        features.append(feature)

    print(f'{len(features)} features built, {skipped} rows skipped')
    return features, skipped


def add_metro_popup_fields(features, platforms):
    '''Add the four popup_* keys the page reads, next to the raw columns.

    Nothing is removed here: the nine columns the STM exported stay exactly
    as they are, and the new keys are appended after them. Each station is a
    family of written points (its station point, its entrances) linked by the
    GTFS fields: a child row names its station through parent_station. The
    page only needs four extra values:

        popup_name        "Metro Beaubien" (the same on every point of a station)
        popup_type        Station or Entrance
        popup_accessible  an entrance only, and only as far as the STM says
        popup_url         the station point only: the STM page for that station

    The clean name and the URL come from the platform rows, which are not part
    of the file: those read "Station Beaubien" and carry the STM page, while a
    station row is upper case ("STATION BEAUBIEN") and carries no URL. A few
    platform rows also trail a "-Zone B". A station has to have exactly one
    name and one page, so its points all agree.
    '''
    # One clean name and one STM page per station, read off its platform rows.
    names = {}
    urls = {}
    for row in platforms:
        key = row['parent_station']
        assert key, 'a platform row names no station'
        name = ZONE_SUFFIX.sub('', STATION_PREFIX.sub('', row['stop_name']))
        assert names.setdefault(key, name) == name, 'a station has more than one name: ' + key
        if row['stop_url']:
            assert urls.setdefault(key, row['stop_url']) == row['stop_url'], \
                'a station has more than one URL: ' + key

    # Group the written points by station: a station row stands for itself,
    # everyone else names its station through parent_station.
    families = {}
    for feat in features:
        props = feat['properties']
        key = props['stop_id'] if props['location_type'] == '1' else props['parent_station']
        families.setdefault(key, []).append(feat)

    station_ids = {f['properties']['stop_id'] for f in features
                   if f['properties']['location_type'] == '1'}
    assert len(families) == len(station_ids), 'a point names a station that is missing'
    assert set(names) == station_ids, 'a station has no platform row to name it'
    assert set(urls) == station_ids, 'a station has no platform row to link it'

    for key, members in families.items():
        assert key in station_ids, 'a point names a station that is missing'
        name = 'Metro ' + names[key]
        url = urls[key]

        for feat in members:
            props = feat['properties']
            location = props['location_type']
            assert location in ('1', '2'), 'unexpected location_type: ' + location

            if location == '1':
                popup_type, popup_url = 'Station', url
            else:
                popup_type, popup_url = 'Entrance', ''

            if location == '2':
                popup_accessible = WHEELCHAIR_WORDING.get(props['wheelchair_boarding'], '')
            else:
                popup_accessible = ''

            props['popup_name'] = name
            props['popup_type'] = popup_type
            props['popup_accessible'] = popup_accessible
            props['popup_url'] = popup_url

    print(f'{len(families)} stations, popup fields added')


def check_metro(rows, skipped, out_path):
    '''Test the metro output before we trust it: count, world bounds, codes.

    The file is read back from disk and tested as it was written, rather than
    tested in memory, so anything JSON cannot round-trip fails here. out_path
    is the file just written, which is a temporary one until it has passed.
    '''
    with open(out_path, encoding='utf-8') as f:
        data = json.load(f)

    n = len(data['features'])

    # Count: every station and entrance row with coordinates should have become
    # a feature. The platform rows were left out of `rows` on purpose.
    assert n == len(rows) - skipped, 'feature count does not match'

    # Right part of the world: every point should be near Montreal.
    for feat in data['features']:
        lon, lat = feat['geometry']['coordinates']
        name = feat['properties']['stop_name']
        assert 45.3 < lat < 45.8 and -74.1 < lon < -73.3, name + ' is not near Montreal'

    # Every feature must still carry a metro stop code.
    for feat in data['features']:
        code = feat['properties']['stop_code']
        assert METRO_CODE.fullmatch(code), 'not a metro stop code: ' + code

    # --- Popup fields added next to the raw columns -----------------------

    props = [feat['properties'] for feat in data['features']]

    # The platforms are not in the file at all: a platform is underground, and
    # the page draws a station and its entrances, never a platform.
    assert all(p['location_type'] != '0' for p in props), 'a platform was written'

    # The nine raw columns must all still be there, plus the four new keys.
    raw_keys = {'stop_id', 'stop_code', 'stop_name', 'stop_lat', 'stop_lon',
                'location_type', 'parent_station', 'wheelchair_boarding', 'stop_url'}
    popup_keys = {'popup_name', 'popup_type', 'popup_accessible', 'popup_url'}
    for p in props:
        assert raw_keys <= set(p), 'a raw column went missing'
        assert popup_keys <= set(p), 'a popup key went missing'

    # The type in the popup must agree with the raw location_type on every row.
    expected_type = {'1': 'Station', '2': 'Entrance'}
    counts = collections.Counter()
    for p in props:
        assert p['popup_type'] == expected_type[p['location_type']], p['stop_id']
        counts[p['popup_type']] += 1
    assert counts['Station'] == 68, counts['Station']
    assert counts['Entrance'] == 134, counts['Entrance']

    # One name per station: every point of a station shares the same name.
    names_by_station = collections.defaultdict(set)
    for p in props:
        names_by_station[p['stop_code']].add(p['popup_name'])
    assert len(names_by_station) == 68, 'expected 68 stations'
    assert all(len(v) == 1 for v in names_by_station.values()), 'a station has two names'

    # The names are clean: no "Station", no "-Zone X", no access detail.
    names = {name for v in names_by_station.values() for name in v}
    assert len(names) == 68, 'station names are not unique'
    for name in names:
        assert name.startswith('Metro '), name
        assert 'Station' not in name and 'Zone' not in name, name
        assert not ACCES_DETAIL.search(name), name

    # Accessibility is shown for entrances only, and only as far as the STM
    # states it: 28 of the 134 entrances are accessible and 106 are not. The
    # wording has to be the table's reading of the raw wheelchair_boarding
    # column, so a row the STM has not judged would show no line at all.
    entrances = [p for p in props if p['location_type'] == '2']
    assert len(entrances) == 134, len(entrances)
    for p in entrances:
        wanted = WHEELCHAIR_WORDING.get(p['wheelchair_boarding'], '')
        assert p['popup_accessible'] == wanted, p['stop_id']
    accessible = [p for p in entrances if p['popup_accessible'] == 'Wheelchair accessible']
    assert len(accessible) == 28, len(accessible)
    assert all(p['popup_accessible'] == '' for p in props if p['location_type'] != '2')
    # Every entrance states it in this feed. If this fails, the STM has started
    # leaving the column blank: the page shows no line for such an entrance, so
    # the new count belongs in this check.
    assert sum(1 for p in entrances if p['popup_accessible']) == 134, \
        'an entrance no longer states its accessibility'

    # The accents survived the read: 106 of the 134 entrances carry an access
    # detail, and that detail is the one accented thing in the file.
    assert sum(1 for p in entrances if ACCES_DETAIL.search(p['stop_name'])) == 106

    # The STM link is shown for the station point only.
    linked = [p for p in props if p['popup_url']]
    assert len(linked) == 68, len(linked)
    assert all(p['location_type'] == '1' for p in linked), 'a URL on a non-station row'
    assert len({p['popup_url'] for p in linked}) == 68, 'station URLs are not unique'
    assert all('/metro/' in p['popup_url'] for p in linked), 'not an STM metro page'

    # A short preview, so the two kinds of popup can be eyeballed here.
    print('Popup preview:')
    for want in ('Station', 'Entrance'):
        p = next(p for p in props if p['popup_type'] == want)
        lines = [p['popup_name'] + ' (' + p['popup_type'] + ')']
        if p['popup_accessible']:
            lines.append(p['popup_accessible'])
        if p['popup_url']:
            lines.append(p['popup_url'])
        print('  ' + ' | '.join(lines))

    print(f'All checks passed. {n} features.')


# --- Green spaces: the city file is already GeoJSON, so we keep it as is ---

def read_parks():
    '''Read the city's green-space export.

    This file is already GeoJSON in WGS84 (longitude, latitude), so reading
    it takes one line: there is nothing to convert and nothing to reproject.
    '''
    with open(PARKS_JSON, encoding='utf-8') as f:
        data = json.load(f)
    features = data['features']
    print(f'{len(features)} features in {PARKS_JSON.name}')
    return data


def build_park_features(source):
    '''One GeoJSON Feature per green space, keeping every attribute.

    The city's export is already the shape we want, so each feature is copied
    across unchanged: same geometry, same properties. Nothing is rounded,
    filtered or simplified, apart from the street right-of-way spaces that
    STREET_SPACE_TYPE names, which are left out. We keep the raw values and
    worry about the file size later.
    '''
    features = []
    skipped = 0
    for feat in source['features']:
        if feat['properties']['TYPO1'] == STREET_SPACE_TYPE:
            skipped += 1
            continue

        feature = {
            'type': 'Feature',
            'geometry': {
                'type': feat['geometry']['type'],
                'coordinates': feat['geometry']['coordinates'],
            },
            'properties': dict(feat['properties']),   # all 11 columns, untouched
        }
        features.append(feature)

    print(f'{len(features)} features built, {skipped} street spaces skipped')
    return features


def city_park_name(head, lien, name):
    '''The city's own name for a park, from its three columns.

    "Parc" + "des" + "Muguets" reads Parc des Muguets. The space is dropped
    after an apostrophe, so "de l'" + "Ouest" reads l'Ouest and not l' Ouest.
    '''
    separator = '' if lien.endswith("'") else ' '
    return head + ' ' + lien + separator + name


def add_park_popup_fields(features):
    '''Add the popup_* keys the page reads, next to the city's own columns.

    The first line of the popup is the park's name. Where the city filled in
    both the Type and the Lien columns, that name is the one it spells out:
    "Parc" + "du" + "Bois-Franc" is the Parc du Bois-Franc. That is how the 46
    polygons on the island's west end and the 20 on its east end, which carry
    only the word "Ouest" or "Est", come out as the Grand parc de l'Ouest
    (1388 ha) and the Grand parc de l'Est (261 ha) instead of "Ouest City
    Park". 39 more rows have a Lien but no Type at all, and every one of them
    is TYPO1 "Parc d'arrondissement", so their missing head noun is restored
    from it, which is how "des" + "Muguets" becomes the Parc des Muguets. That
    is 38 of the 39: the one whose Lien is the noun "place" is left to the
    wording below rather than forced into the pattern. Those 464 names are the
    city's own French ones, so popup_title_source says "city" and the page sets
    them in italics.

    858 rows carry a Type but no Lien, and are worded in English instead: the
    name with the type after it ("Jarry Park"), or the bare name for the rows
    the city left unclassified. 267 more are borough parks with an empty Type
    column, which read "Angrignon (Park)": most of the city's parks are only
    ever called "parc" in TYPO1, so the bare name would not say what the space
    is. The other empty-Type rows are gardens, campuses and cemeteries, which
    read better bare. A green space the city has not named yet says "Unnamed
    green space". Those four wordings are popup_title_source "derived".

    popup_kind is the English for TYPO1 and popup_kind_key is the short key the
    page colours the polygon by: both come from PARK_KINDS above, so the page
    holds no French. The three rows with no TYPO1 keep the "unknown" key and an
    empty wording, and the page shows a dash rather than inventing a type.
    popup_size is the SUPERFICIE column, in hectares, to three decimals.
    '''
    for feature in features:
        props = feature['properties']
        name = (props['Nom'] or '').strip()
        raw_type = (props['Type'] or '').strip()
        lien = (props['Lien'] or '').strip()
        typo1 = (props['TYPO1'] or '').strip()
        kind = PARK_KIND_BY_NAME.get(typo1)

        # "city" marks the names that are the city's own, which the page sets
        # in italics; "derived" is the English wording built here.
        if not name:
            popup_title, source = 'Unnamed green space', 'derived'
        elif raw_type and lien:
            popup_title = city_park_name(raw_type, lien, name)
            source = 'city'
        elif lien in NAME_JOIN_PARTICLES and typo1 == "Parc d'arrondissement":
            popup_title = city_park_name(PARK_NAME_HEAD_WHEN_UNTYPED, lien, name)
            source = 'city'
        elif raw_type:
            popup_title = name + ' ' + PARK_TYPES.get(raw_type, raw_type)
            source = 'derived'
        elif typo1 == "Parc d'arrondissement":
            popup_title, source = name + ' (Park)', 'derived'
        else:
            popup_title, source = name, 'derived'

        props['popup_title'] = popup_title
        props['popup_title_source'] = source
        props['popup_kind'] = kind[1] if kind else ''
        props['popup_kind_key'] = kind[0] if kind else PARK_KIND_UNKNOWN
        props['popup_size'] = '%.3f ha' % float(props['SUPERFICIE'])

    print(f'{len(features)} green spaces, popup fields added')


def sort_park_features(features):
    '''Write the green spaces biggest first, which is what decides the clicks.

    The page draws the polygons in the order it reads them and a click finds
    the last one it drew, so writing the largest first leaves the small ones
    on top, and clickable, whatever large park they sit inside. Sorting is
    stable, so spaces of the same size keep the order the city sent them in
    and the file stays reproducible.
    '''
    features.sort(key=lambda f: -float(f['properties']['SUPERFICIE']))
    print(f'{len(features)} green spaces, biggest first')


def check_parks(source, out_path):
    '''Test the parks output before we trust it.

    The file is read back from disk and tested as it was written, rather than
    tested in memory. out_path is the file just written, which is a temporary
    one until it has passed.
    '''
    with open(out_path, encoding='utf-8') as f:
        data = json.load(f)

    n = len(data['features'])

    # Count: every green space in the source, minus the street right-of-way
    # spaces that build_park_features leaves out.
    kept = sum(1 for f in source['features']
               if f['properties']['TYPO1'] != STREET_SPACE_TYPE)
    assert n == kept, 'feature count does not match'

    # Every feature must keep every attribute the city provided, plus the five
    # popup keys the page reads.
    popup_keys = {'popup_title', 'popup_title_source', 'popup_kind',
                  'popup_kind_key', 'popup_size'}
    expected = set(source['features'][0]['properties']) | popup_keys
    for feat in data['features']:
        assert set(feat['properties']) == expected, 'a feature lost an attribute'

    # Every feature must still be a polygon, near Montreal, with closed rings.
    for feat in data['features']:
        geometry = feat['geometry']
        assert geometry['type'] in ('Polygon', 'MultiPolygon'), 'not a polygon: ' + geometry['type']

        if geometry['type'] == 'Polygon':
            rings = geometry['coordinates']
        else:
            rings = [ring for polygon in geometry['coordinates'] for ring in polygon]

        for ring in rings:
            assert ring[0] == ring[-1], 'a ring is not closed'

        lon, lat = rings[0][0]
        obj = feat['properties']['OBJECTID']
        assert 45.3 < lat < 45.8 and -74.1 < lon < -73.3, 'green space ' + obj + ' is not near Montreal'

    # --- Popup fields added next to the raw columns -----------------------

    props = [feat['properties'] for feat in data['features']]

    # The file is written biggest green space first, because that is the order
    # the page draws them in and a click takes the last polygon it drew: it is
    # what puts the small spaces on top, so it is pinned here.
    sizes = [float(p['SUPERFICIE']) for p in props]
    assert sizes == sorted(sizes, reverse=True), 'the green spaces are not written biggest first'

    # Every title says what the space is, and the six ways of wording one are
    # counted: 426 rows get the name the city's Type, Lien and Nom columns
    # spell out, 38 more are the same with the head noun restored from TYPO1,
    # 858 get an English type after the name, 267 are borough parks whose Type
    # column is empty and so read "(Park)", 96 keep the bare name and 296 have
    # no name at all.
    titles = collections.Counter()
    sources = collections.Counter()
    samples = {}
    for p in props:
        name = (p['Nom'] or '').strip()
        raw_type = (p['Type'] or '').strip()
        lien = (p['Lien'] or '').strip()
        typo1 = (p['TYPO1'] or '').strip()
        kind = PARK_KIND_BY_NAME.get(typo1)

        # The join from city_park_name above, written out again on purpose: if
        # the builder starts wording names differently, this test fails.
        separator = '' if lien.endswith("'") else ' '

        if not name:
            branch, wanted = 'unnamed', 'Unnamed green space'
            wanted_source = 'derived'
        elif raw_type and lien:
            branch = 'city name'
            wanted = raw_type + ' ' + lien + separator + name
            wanted_source = 'city'
        elif lien in NAME_JOIN_PARTICLES and typo1 == "Parc d'arrondissement":
            branch = 'city name, head noun restored'
            wanted = PARK_NAME_HEAD_WHEN_UNTYPED + ' ' + lien + separator + name
            wanted_source = 'city'
        elif raw_type:
            branch, wanted = 'named with a Type', name + ' ' + PARK_TYPES[raw_type]
            wanted_source = 'derived'
        elif typo1 == "Parc d'arrondissement":
            branch, wanted = 'named, borough park', name + ' (Park)'
            wanted_source = 'derived'
        else:
            branch, wanted = 'named, bare', name
            wanted_source = 'derived'

        titles[branch] += 1
        sources[wanted_source] += 1
        samples.setdefault(branch, p)
        assert p['popup_title'] == wanted, p['OBJECTID']
        assert p['popup_title_source'] == wanted_source, p['OBJECTID']
        assert p['popup_kind'] == (kind[1] if kind else ''), p['OBJECTID']
        assert p['popup_kind_key'] == (kind[0] if kind else PARK_KIND_UNKNOWN), p['OBJECTID']

        # The size line is the SUPERFICIE column in hectares, to three decimals.
        assert p['popup_size'] == '%.3f ha' % float(p['SUPERFICIE']), p['OBJECTID']

        # The city's own names carry the Lien that joined them, and none is left
        # with the gap where a dropped space used to be.
        if wanted_source == 'city':
            assert lien in p['popup_title'], p['OBJECTID']
            assert '  ' not in p['popup_title'], p['OBJECTID']

    assert titles == {'city name': 426, 'city name, head noun restored': 38,
                      'named with a Type': 858, 'named, borough park': 267,
                      'named, bare': 96, 'unnamed': 296}, titles
    assert sources == {'city': 464, 'derived': 1517}, sources

    # The two umbrella parks are the rows the old wording broke: the city named
    # 46 polygons of the Grand parc de l'Ouest and 20 of the Grand parc de
    # l'Est by the compass word alone, and no other park name starts that way.
    umbrella = collections.Counter(p['popup_title'] for p in props
                                   if p['popup_title'].startswith('Grand parc'))
    assert umbrella == {'Grand parc de l\'Ouest': 46,
                        'Grand parc de l\'Est': 20}, umbrella
    for p in props:
        if p['Nom'] in ('Ouest', 'Est'):
            assert p['popup_title_source'] == 'city', p['OBJECTID']
            assert p['NUM_INDEX'] in ('9512-000', '9514-000'), p['OBJECTID']
            assert p['TYPO2'] == 'Parc-nature', p['OBJECTID']

    # Every value the city uses must be one of the tables at the top, so a new
    # one fails here rather than reaching the page. An empty column arrives as
    # null rather than as empty text, so both are read the same way.
    for p in props:
        raw_type = (p['Type'] or '').strip()
        typo1 = (p['TYPO1'] or '').strip()
        assert not raw_type or raw_type in PARK_TYPES, p['Type']
        assert not typo1 or typo1 in PARK_KIND_BY_NAME, p['TYPO1']

    # The page colours each polygon by the kind key, so the six keys are pinned
    # here, with the number of spaces sitting behind each of the greens.
    kinds = collections.Counter(p['popup_kind_key'] for p in props)
    assert kinds == {'arrondissement': 1384, 'pending': 270, 'autre': 184,
                     'grand_parc': 139, 'unknown': 3, 'jardin': 1}, kinds

    # A short preview, so the six kinds of popup can be eyeballed here, with
    # the source in brackets standing in for the italics the page draws.
    print('Popup preview:')
    for branch in ('city name', 'city name, head noun restored', 'named with a Type',
                   'named, borough park', 'named, bare', 'unnamed'):
        p = samples[branch]
        print('  [' + p['popup_title_source'] + '] ' + p['popup_title'] +
              ' | Type: ' + (p['popup_kind'] or '-') + ' | Size: ' + p['popup_size'])

    # The two biggest rows of the umbrella parks, the ones this wording fixes.
    biggest = {}
    for p in props:
        if p['Nom'] in ('Ouest', 'Est'):
            best = biggest.get(p['Nom'])
            if best is None or float(best['SUPERFICIE']) < float(p['SUPERFICIE']):
                biggest[p['Nom']] = p
    for p in biggest.values():
        print('  [' + p['popup_title_source'] + '] ' + p['popup_title'] +
              ' | Type: ' + (p['popup_kind'] or '-') + ' | Size: ' + p['popup_size'])

    print(f'All checks passed. {n} features.')


# --- Street intersections --------------------------------------------------

# The city draws its streets as troncons: one LineString for each stretch of
# street between two corners, 48031 of them, in degrees. CLASSE says what kind
# of road each one is, and that column is the one filter here, the same one
# step 3 uses. The motorways and the service ways beside them (class 8) go
# first, before anything else is done, because nobody walks there. So no point
# in this file is an intersection a person cannot stand on, and the points and
# the walks measured from them agree about what a street is.
UNWALKABLE_CLASSES = ('8',)

# The file draws one corner once for every street that touches it, a
# centimetre or two apart. Two ends this close together are the same corner,
# and the first one read decides where that corner sits, right down to the
# degrees the file wrote it in.
SNAP_METRES = 0.1

# How many street ends a corner needs before it is written out. A corner with
# two ends is the middle of a street and a corner with one is a dead end, so
# neither is an intersection: what this file is about is the places a walk can
# turn at. How many of each there are is printed, so what is left out is on
# the record. (At 1 the file would cover every street end, dead ends and all.)
MIN_DEGREE = 3

# Coordinates are written rounded to this many decimals. Seven decimals of a
# degree is about 1.1 cm on the ground, finer than the 10 cm the snapping above
# is done in, so two corners the network keeps apart stay apart in the file.
# Six would be 11 cm, which would let the snapping's own tolerance through and
# put two neighbouring corners on the same point.
COORD_DIGITS = 7

# Side of the little squares the corner lookup uses to skip most of the
# comparisons it would otherwise make. It is a lookup size, not a measurement:
# nothing is measured against it or rounded to it.
CELL_METRES = 25.0

# The city's own placeholder for a way it has not named, which is 1079 of the
# troncons a pedestrian can walk. It is not a name, so it is kept out of a
# corner's title: "Rue Sherbrooke Est / Voie Non-nommee" names one street and
# then says the other has no name, which the one name already says. A corner
# with no named street at all falls back to UNNAMED_WAY_TITLE, worded here the
# way the green spaces word a space the city has not named.
UNNAMED_WAY = 'Voie Non-nomm\u00e9e'
UNNAMED_WAY_TITLE = 'Unnamed way'


# --- Degrees to metres -----------------------------------------------------

# The distances here are metres on the ground and the file's are in degrees, so
# the two are bridged by network.py's to_metres: MTM zone 8, EPSG code 32188,
# worked out from the city's own .prj file. The one copy of that maths in the
# repo is in network.py, which also checks it against the city's own metres.


# --- Reading the streets, and finding the corners -------------------------

def read_streets():
    '''The troncons a pedestrian may walk, each one projected into metres.

    Every troncon is kept with its own columns and with both copies of its
    shape: the points as the file wrote them, in degrees, and the same points
    in metres. The degrees are what a corner's own position is written out
    from, so a point on the map is a place the city drew rather than one
    worked backwards out of metres; the metres are what the snapping below is
    measured in. The length of what the one filter drops is printed, so the
    size of that decision is on the record.
    '''
    with open(STREETS_JSON, encoding='utf-8') as f:
        source = json.load(f)

    lines = []
    points = 0
    dropped = 0
    dropped_metres = 0.0
    for feature in source['features']:
        props = feature['properties']
        degrees = [(lon, lat) for lon, lat in feature['geometry']['coordinates']]
        metres = [to_metres(lon, lat) for lon, lat in degrees]
        if str(props['CLASSE']) in UNWALKABLE_CLASSES:
            dropped += 1
            dropped_metres += line_length(metres)
            continue
        points += len(degrees)
        lines.append({'props': props, 'metres': metres, 'degrees': degrees})

    walked_metres = sum(line_length(line['metres']) for line in lines)
    print(f'{len(lines)} troncons read and projected to metres, '
          f'{points} points, {walked_metres / 1000.0:.0f} km of street')
    print(f'{dropped} troncons dropped as motorway or motorway service, '
          f'{dropped_metres / 1000.0:.0f} km not walked')
    return lines


def line_length(shape):
    '''How long a list of points is, in metres.'''
    total = 0.0
    x0, y0 = shape[0]
    for x1, y1 in shape:
        total += math.hypot(x1 - x0, y1 - y0)
        x0, y0 = x1, y1
    return total


def build_corners(lines):
    '''Give every shared corner one number, and keep the ones that are corners.

    Every vertex of every troncon is walked, and a vertex within SNAP_METRES of
    one already read becomes that corner: the first one read decides where the
    corner sits, and the degrees it was read in are kept with it. The lookup is
    by square, so a vertex is only compared with the corners in its own square
    and the eight around it rather than with all 216,000 of them.

    The links between those corners are built at the same time, and it is the
    links that say what a corner's degree is, since the degree is how many of
    them leave it. A vertex in the middle of a troncon has two links; a corner
    where two troncon ends meet and a third street crosses has four.

    What comes back is the corners kept, one dict each (its metres, the degrees
    the file wrote it in, its degree, and the troncons that met there), and the
    size of the whole network those corners were picked out of.
    '''
    nodes = []          # [easting, northing] in metres
    node_wgs = []       # the same corner as the file wrote it, [lon, lat]
    links = []          # (corner, corner) for every step between two vertices
    at_node = {}        # which troncons have a vertex at a corner
    squares = {}

    def node_of(x, y, lon, lat):
        square = (math.floor(x / CELL_METRES), math.floor(y / CELL_METRES))
        for cx in (square[0] - 1, square[0], square[0] + 1):
            for cy in (square[1] - 1, square[1], square[1] + 1):
                for node in squares.get((cx, cy), ()):
                    nx, ny = nodes[node]
                    if abs(nx - x) <= SNAP_METRES and abs(ny - y) <= SNAP_METRES:
                        return node
        nodes.append([x, y])
        node_wgs.append([lon, lat])
        squares.setdefault(square, []).append(len(nodes) - 1)
        return len(nodes) - 1

    for number, line in enumerate(lines):
        previous = None
        for (lon, lat), (x, y) in zip(line['degrees'], line['metres']):
            node = node_of(x, y, lon, lat)
            at_node.setdefault(node, []).append(number)
            if previous is not None and node != previous:
                links.append((previous, node))
            previous = node

    print(f'{len(nodes)} corners and {len(links)} links after snapping the '
          f'ends that are the same corner onto it')

    degree = [0] * len(nodes)
    for start, end in links:
        degree[start] += 1
        degree[end] += 1

    kept = [{'x': nodes[node][0], 'y': nodes[node][1],
             'wgs': node_wgs[node], 'degree': degree[node],
             'troncons': sorted(set(at_node[node]))}
            for node in range(len(nodes)) if degree[node] >= MIN_DEGREE]

    histogram = collections.Counter(node['degree'] for node in kept)
    print(f'{len(kept)} of them have {MIN_DEGREE} street ends or more and are '
          f'written out: ' + ', '.join(f'{ends} ends {histogram[ends]}'
                                       for ends in sorted(histogram)))
    below = {ends: count for ends, count in collections.Counter(degree).items()
             if ends < MIN_DEGREE}
    print(f'{sum(below.values())} are left out, {MIN_DEGREE} ends being the cut: '
          + ', '.join(f'{ends} ends {below[ends]}' for ends in sorted(below)))
    return kept, {'corners': len(nodes), 'links': len(links)}


def sort_corners(corners):
    '''Number the corners south to north, and west to east along each line.

    Two runs over the same file have to hand out the same numbers, or a map
    built on top of this one cannot be checked against it, so the order is
    decided here rather than left to the order the file happens to be read in.
    The corners are sorted by their position as it is written out - latitude
    first, then longitude, both rounded the way they are written - so the file
    runs south to north and, along each line of latitude, west to east.

    Not by the metres, though the metres are what the corners were found in: a
    transverse Mercator bends its lines of latitude, by about 110 m across this
    city, so ordering by projected northing would put corners 28 km apart next
    to each other in the file. The written degrees are the order a reader can
    see, and the check tests exactly that.
    '''
    corners.sort(key=lambda corner: (round(corner['wgs'][1], COORD_DIGITS),
                                     round(corner['wgs'][0], COORD_DIGITS)))
    for number, corner in enumerate(corners):
        corner['id'] = number
    return corners


def build_intersection_features(corners, lines):
    '''One GeoJSON Point per intersection, in the order given (see sort_corners).

    The properties are the corner's own three: its number, how many street ends
    meet there, and the city's ID_TRC for every troncon that met there, which is
    the only record of where the point came from. The popup keys are added next,
    in add_intersection_popup_fields below, the way the other two outputs do it.
    '''
    features = []
    for corner in corners:
        lon, lat = corner['wgs']
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point',
                         'coordinates': [round(lon, COORD_DIGITS),
                                         round(lat, COORD_DIGITS)]},
            'properties': {
                'id': corner['id'],
                'degree': corner['degree'],
                'troncons': ','.join(str(lines[number]['props']['ID_TRC'])
                                     for number in corner['troncons']),
            },
        })

    print(f'{len(features)} intersection features built')
    return features


def add_intersection_popup_fields(features, corners, lines):
    '''Add the two popup_* keys the page reads, next to the corner's own values.

        popup_title   the streets that meet at the corner, named once each and
                      sorted, as a person would say them: "Avenue du Parc /
                      Rue Saint-Viateur". The names are the city's own, from the
                      ODONYME column of the troncons that met there, less the
                      ones the city has not named at all.
        popup_degree  the wording for the degree: "4 street ends meet here".

    The count is ends and the list is names, and the two differ wherever a
    street comes back to the same corner: that is two ends but one name.
    '''
    for feat, corner in zip(features, corners):
        props = feat['properties']
        # The file pads every name with a trailing space, and one street can be
        # drawn on several troncons, so the names are stripped and carried
        # through a set before they are sorted. What is left when the unnamed
        # ways are taken out is the corner's name; a corner with none of those
        # keeps the city's placeholder's English wording instead.
        names = sorted({lines[number]['props']['ODONYME'].strip()
                        for number in corner['troncons']} - {UNNAMED_WAY})
        props['popup_title'] = ' / '.join(names) if names else UNNAMED_WAY_TITLE
        props['popup_degree'] = f'{props["degree"]} street ends meet here'

    print('Popup fields added')


# --- The checks -----------------------------------------------------------

def check_intersections(corners, network, rows, out_path):
    '''Test the intersections output before we trust it.

    As with the other two files, what is tested is what landed on disk, read
    back, so anything JSON cannot round-trip or that was dropped on the way out
    fails here. The corners still in memory are the yardstick for the count,
    the numbering, the degrees and the places, and the network they were picked
    out of is the yardstick for the snapping.

    The last check is against a second source, the STM's own points: they are
    surveyed, the corners here are drawn from the city's streets, and neither
    is ours.
    '''
    with open(out_path, encoding='utf-8') as f:
        data = json.load(f)
    features = data['features']
    props = [feat['properties'] for feat in features]

    # Count and numbering: one feature per corner kept, numbered from zero in
    # the order they were sorted into, with nothing missing in between.
    assert len(features) == len(corners), 'feature count does not match'
    assert [p['id'] for p in props] == list(range(len(features)))

    # The corners behind them: the network read out of the file and snapped is
    # pinned, so a change in how the troncons are read or the ends are joined
    # fails here rather than quietly moving every point on the map.
    assert network == {'corners': 216183, 'links': 232080}, network

    # The order the numbers are in: south to north, and west to east along each
    # line of latitude, read off the degrees as written. Nothing here has to be
    # recovered from the metres, so the test is exact and a reader can follow it.
    written = [(feat['geometry']['coordinates'][1],
                feat['geometry']['coordinates'][0]) for feat in features]
    assert written == sorted(written), 'the file is not in the order it is numbered in'

    # Keys: the three the corner has plus the two the page reads, and no more,
    # so a change to the shape of a feature fails here rather than in the page.
    for p in props:
        assert set(p) == {'id', 'degree', 'troncons', 'popup_title',
                          'popup_degree'}, sorted(p)

    # Every point is a Point, in this city, written at COORD_DIGITS and no
    # finer, and the troncon ids are the city's own ID_TRC values.
    for feat in features:
        assert feat['type'] == 'Feature' and feat['geometry']['type'] == 'Point'
        lon, lat = feat['geometry']['coordinates']
        assert 45.3 < lat < 45.8 and -74.1 < lon < -73.3, 'not near Montreal'
        assert round(lon, COORD_DIGITS) == lon and round(lat, COORD_DIGITS) == lat
        for troncon in feat['properties']['troncons'].split(','):
            assert troncon.isdigit(), troncon

    # No two intersections are written at the same place: two written points
    # would have to be closer than a centimetre, and the snapping never leaves
    # two corners that close, so a repeat here would mean one corner was
    # written twice.
    places = {tuple(feat['geometry']['coordinates']) for feat in features}
    assert len(places) == len(features), 'two intersections share a place'

    # Degrees: every point is a place a walk can turn at, the number written is
    # the number the network has there, and the wording agrees with it. The
    # histogram is pinned too, so a change in how corners are found fails here.
    degrees = collections.Counter(p['degree'] for p in props)
    assert degrees == {3: 16242, 4: 8156, 5: 257, 6: 13, 7: 1}, degrees
    assert sum(degrees.values()) == len(features)
    for p, corner in zip(props, corners):
        assert p['degree'] == corner['degree'], p['id']
        assert p['popup_degree'] == f'{p["degree"]} street ends meet here'
        assert p['popup_title'] == p['popup_title'].strip(), p['id']
        assert p['popup_title'], p['id']

    # The city's placeholder for an unnamed way never reaches a title, and the
    # corners with nothing named at all are pinned: if the city rewords its
    # placeholder, this fails here rather than filling the page with it.
    for p in props:
        assert UNNAMED_WAY not in p['popup_title'].split(' / '), p['id']
    unnamed = sum(1 for p in props if p['popup_title'] == UNNAMED_WAY_TITLE)
    assert unnamed == 70, unnamed

    # The degrees are a place the city drew: projected back into metres, every
    # written point has to land within a centimetre of the corner the network
    # was snapped at, or the two copies of the shape have drifted apart.
    worst = 0.0
    for feat, corner in zip(features, corners):
        x, y = to_metres(*feat['geometry']['coordinates'])
        worst = max(worst, math.hypot(x - corner['x'], y - corner['y']))
    assert worst < 0.05, f'a written point is {worst:.3f} m from its corner'

    # A preview, so a few popups can be eyeballed here.
    print('Popup preview:')
    for feat in (features[0], features[len(features) // 2], features[-1]):
        p = feat['properties']
        print(f'  {p["popup_title"]} | {p["popup_degree"]} | '
              f'id {p["id"]}, troncons {p["troncons"]}')


    # --- The STM's own points, as a second source -------------------------

    # A station point is a surveyed place on a street, and the corners here are
    # drawn from the city's streets: two files, neither of them ours. Every
    # station point has to land near a corner this file draws. The stations the
    # city's street file does not cover at all - the ones in Laval and on the
    # South Shore - cannot, and that list is pinned below.
    corner_metres = [(corner['x'], corner['y']) for corner in corners]
    stations = [row for row in rows if row['location_type'] == '1']
    distances = []
    for row in stations:
        x, y = to_metres(float(row['stop_lon']), float(row['stop_lat']))
        distances.append((min(math.hypot(x - cx, y - cy)
                              for cx, cy in corner_metres), row))

    near = [item for item in distances if item[0] <= 250.0]
    far = [item for item in distances if item[0] > 250.0]
    print(f'{len(near)} of the {len(stations)} metro stations have an '
          f'intersection within 250 m. The far ones, which are the stations '
          f'the city street file has no streets at:')
    for distance, row in sorted(far, key=lambda item: item[0], reverse=True):
        print(f'  {distance / 1000.0:6.1f} km  {row["stop_code"]}  {row["stop_name"]}')

    # Pinned, so a change in either file has to be looked at rather than
    # quietly passing the check. The four are the three stations in Laval
    # (Cartier, De la Concorde, Montmorency) and the one on the South Shore
    # (Longueuil), all outside the area the city's street file draws at all.
    assert [row['stop_code'] for _, row in far] == ['10282', '10286', '10288',
                                                   '10454'], far

    print(f'All checks passed. {len(features)} features.')


# --- Writing --------------------------------------------------------------

def write_geojson(features, out_path, indent=2):
    '''Write a FeatureCollection to out_path, and nothing else.

    The metro file is small, so it is written pretty-printed and easy to read
    by eye. The green spaces hold about 159,000 coordinate pairs and the street
    intersections 24,669 points, and the same indenting would add roughly 9 MB
    and 1 MB of blank space for nothing, so main() passes indent=None for those
    two and they are written compact instead.
    '''
    out_path.parent.mkdir(parents=True, exist_ok=True)
    geojson = {'type': 'FeatureCollection', 'features': features}

    if indent is None:
        text = json.dumps(geojson, ensure_ascii=False, separators=(',', ':'))
    else:
        text = json.dumps(geojson, ensure_ascii=False, indent=indent)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(text)


def write_checked(features, out_path, check, indent=2):
    '''Write a file and check it, without ever leaving a bad file behind.

    The checks read the file back and test what actually landed on disk, so
    the write has to come first. It goes to a temporary name beside the real
    file, and only moves into place once the checks have passed: a run that
    fails then leaves the file the page loads exactly as it was, instead of
    half updated and dirtying git status. The temporary name sits in the same
    folder, so the move is a single step.
    '''
    temp_path = out_path.with_name(out_path.name + '.new')
    write_geojson(features, temp_path, indent=indent)
    check(temp_path)
    temp_path.replace(out_path)
    print(f'Written to {out_path} ({out_path.stat().st_size // 1024} KB)')


def main():
    # Metro stops: the points are the stations and their entrances; the
    # platform rows are read for each station's name and STM page and dropped.
    rows, platforms = read_metro_rows()
    metro_features, skipped = build_metro_features(rows)
    add_metro_popup_fields(metro_features, platforms)
    write_checked(metro_features, METRO_OUT, indent=2,
                  check=lambda path: check_metro(rows, skipped, path))

    # Green spaces.
    source = read_parks()
    park_features = build_park_features(source)
    add_park_popup_fields(park_features)
    sort_park_features(park_features)
    write_checked(park_features, PARKS_OUT, indent=None,
                  check=lambda path: check_parks(source, path))

    # Street intersections: one point per place three or more street ends meet.
    lines = read_streets()
    corners, network = build_corners(lines)
    sort_corners(corners)
    features = build_intersection_features(corners, lines)
    add_intersection_popup_fields(features, corners, lines)
    write_checked(features, INTERSECTIONS_OUT, indent=None,
                  check=lambda path: check_intersections(corners, network, rows, path))


if __name__ == '__main__':
    main()
