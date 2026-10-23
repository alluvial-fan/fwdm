'''
build_walk_times.py

Step 3, second half: how long the walk is from every street corner of Montreal
to what the page's two filters are looking for. Step 2's intersections are the
corners; network.py builds the graph out of the street file and walks it; this
file measures the 22 walks and writes them back onto step 2's own features, so
that the page reads four GeoJSON files and nothing else.

What the 22 questions are, where a walk starts and where it ends, what a
corner's two numbers mean, and the five checks that run before anything is
written are in the README - "What step 3 measures", "What step 3 writes" and
"What step 3 does not know" - said once there for the whole project rather than
twice, here and there.

Step 2 writes the corners; this step adds three properties to each of them and
writes the file back where it was. Run it again after step 2 has run, because
a corner that step 2 has just rewritten carries no walks at all.

Run it with:  ./.venv/Scripts/python.exe site/build_walk_times.py
'''

import json
import math
import time
from array import array
from pathlib import Path

# The two modules in this folder. Running the script puts its own folder on the
# import path, so these are found however the script is started.
import build_geojson
import network

# Paths are worked out from this file's own location, so it does not matter
# which folder you run it from.
SITE_DIR = Path(__file__).resolve().parent          # .../project/site
PROJECT_DIR = SITE_DIR.parent                       # .../project

STREETS_JSON = PROJECT_DIR / 'data' / 'street_network' / 'geobase.json'
OUTLINES_JSON = (PROJECT_DIR / 'data' / 'neighbourhoods'
                 / 'limites-administratives-agglomeration.geojson.json')
METRO_GEOJSON = SITE_DIR / 'data' / 'metro_stops.geojson'
PARKS_GEOJSON = SITE_DIR / 'data' / 'city_parks.geojson'
DOTS_GEOJSON = SITE_DIR / 'data' / 'intersections.geojson'

BOROUGHS_GEOJSON = SITE_DIR / 'data' / 'boroughs.geojson'


# --- The knobs ------------------------------------------------------------

# How far a gateway or a metro entrance may sit from the nearest street and
# still be taken to be beside it. The file draws no paths inside parks, so a
# space's own edge can be a street's width from the road that serves it, and
# 100 m is generous enough to reach the far side of a wide space and tight
# enough to refuse a point with no street drawn near it at all. What is not
# thrown away by allowing it is the distance itself: the last stretch from the
# street to the edge is added to every walk, so a walk is a chain of lines and
# can never be shorter than the straight line.
ATTACH_METRES = 100.0

# Points are taken along a green space's edge this far apart, and each one is
# pulled onto the nearest street: they are the gateways a walk to that space
# can finish at. 20 m keeps the number of points affordable while leaving no
# street along a park without a doorway onto it.
GATEWAY_METRES = 20.0

# How far a dot may be from the corner of the network it is matched with. Both
# files come out of the same vertices, so the distance is the rounding in the
# intersections file (seven decimals, about a centimetre) and nothing else. The
# limit is here to fail loudly if the two files ever stop being the same
# corners, rather than to allow a slack.
TIE_METRES = 0.05

# The file's CLASSE 8 is its motorways, the expressway in its trench (the file
# calls it Route 136), the bridges a motorway crosses on, and the unnamed
# service ways that run beside them. It is the one class a pedestrian may not
# walk, so it is dropped rather than walked. Every other class - rues, avenues,
# boulevards, the bridges that carry people - is kept as it stands.
UNWALKABLE_CLASSES = ('8',)

# The size bands green space is asked for by. Each band is [low, high) in
# hectares, so the four of them hold every space exactly once and none of them
# counts a boundary hectare twice. "Any size" is the page's fifth choice and is
# not here: it is the minimum of the four, worked out after they are measured.
BANDS = (
    ('0to1', 'under 1 ha', 0.0, 1.0),
    ('1to5', '1 to 5 ha', 1.0, 5.0),
    ('5to10', '5 to 10 ha', 5.0, 10.0),
    ('10up', '10 ha or more', 10.0, None),
)
ANY_BAND = ('any', 'Any size')

# The kinds of green space the page offers, against the city's own six. The
# keys are the ones build_geojson.py wrote into the parks file from TYPO1: a
# Grand parc is a city park, a Parc d'arrondissement is a borough park, and the
# rest - other green spaces, the one garden, the rows still being validated and
# the three with no type at all - are grouped as other green space rather than
# given a chip each. "Any kind" is the minimum of the three, as "any size" is
# of the four bands.
KINDS = (
    ('city', 'City park'),
    ('borough', 'Borough park'),
    ('other', 'Other green space'),
)
ANY_KIND = ('any', 'Any green space')
PARK_KIND_OF_KEY = {
    'grand_parc': 'city',
    'arrondissement': 'borough',
    'autre': 'other',
    'jardin': 'other',
    'pending': 'other',
    'unknown': 'other',
}

# The two metro questions. An entrance the STM calls wheelchair accessible is
# one of the entrances, so the step-free layer is a search over a subset of the
# same points. Each is (id, label, kind, kind wording), and the kind is what
# the page's two metro choices are.
METRO_LAYERS = (
    ('metro_any', 'Any metro entrance within reach', 'any', 'Any entrance'),
    ('metro_stepfree', 'A step-free metro entrance within reach',
     'stepfree', 'Step-free only'),
)

# Which of the published outlines the page draws: the ones the city's own file
# labels an Arrondissement, its 19 boroughs, rather than the 15 cities linked to
# it. The label is the file's own - its TYPE column - so the set the page is
# about is read out of the city's file rather than typed in here a second time,
# and the same answer is written beside each outline the page is given, as a flag
# it can read without a list of names of its own. Both labels are named, and a
# build that found a third would stop rather than quietly draw a smaller map.
# The 15 are not drawn and are not measured for: they are what the page's grey
# mask covers, so the page says nothing about their corners at all.
FOCUS_TYPE = 'Arrondissement'
OTHER_TYPE = 'Ville liée'

# What a dot's two numbers mean when there is no walk to put there. The order
# matters: a minimum is taken over layers by number, so every statement has to
# be larger than any walk and "outside" has to be larger than "no link", which
# is larger than "no route".
OUTSIDE = 65535          # not in any of the 34 published outlines
NO_STREET = 65534        # in the city, on a corner carrying no link at all
UNREACHABLE = 65533      # on the network, but nothing of this layer is
NO_DESTINATION = 65535   # the mirror of OUTSIDE, for the destination number
MAX_SECONDS = 65532      # the longest walk that fits in a dot's number

# The times the page's filters offer, in minutes. Each layer's counts of corners
# within each of these are taken as it is measured and go into the file the page
# reads them from: they are what the page checks its own reading of a layer
# against, without repeating any of this work in the browser.
TICK_MINUTES = (5, 15, 30)

# Dots walked through at a fixed stride for the crow-flies check, per layer
# measured. The check is a bound rather than an equality, so a sample is
# enough, and it is the one check that reads the city's own geometry rather
# than this build's.
CHECK_DOTS = 25

# Coordinates are written to this many decimal places, the same seven
# build_geojson.py writes its own points with. Seven is about a centimetre on
# the ground, finer than any screen will show, and it keeps the file small.
COORD_DIGITS = 7


# --- Reading the five files -----------------------------------------------

def read_streets():
    '''The troncons a pedestrian may walk, each one projected into metres.

    The one filter is the road class: UNWALKABLE_CLASSES holds the motorways,
    the expressway and the service ways beside them, and those troncons are
    dropped before anything else happens, so no walk in this build can be
    routed along a motorway. What is left is every rue, avenue and boulevard
    the city draws, plus the bridges and viaducts that carry them, and the
    length of what was dropped is printed so the size of that decision is on
    the record.

    The troncons are read in the file's own order, which is what the corners
    are numbered in: the intersections file this build measures from was built
    from the same troncons in the same order, so the two agree about which
    corner is which without either of them being asked.
    '''
    with open(STREETS_JSON, encoding='utf-8') as f:
        source = json.load(f)

    lines = []
    points = 0
    dropped = 0
    dropped_metres = 0.0
    for feature in source['features']:
        props = feature['properties']
        shape = [network.to_metres(lon, lat)
                 for lon, lat in feature['geometry']['coordinates']]
        if str(props['CLASSE']) in UNWALKABLE_CLASSES:
            dropped += 1
            dropped_metres += _line_length(shape)
            continue
        points += len(shape)
        lines.append(shape)

    walked_metres = sum(_line_length(shape) for shape in lines)
    print(f'{len(lines)} troncons read and projected to metres, '
          f'{points} points, {walked_metres / 1000.0:.0f} km of street')
    print(f'{dropped} troncons dropped as motorway or motorway service, '
          f'{dropped_metres / 1000.0:.0f} km not walked')
    return lines


def _line_length(shape):
    '''How long a list of points is, in metres.'''
    total = 0.0
    x0, y0 = shape[0]
    for x1, y1 in shape:
        total += math.hypot(x1 - x0, y1 - y0)
        x0, y0 = x1, y1
    return total


def read_dots():
    '''The street corners from step 2, in the order they are numbered in.

    Every dot is a place a walk can start from, so each one keeps its own
    number, where it is in metres, the streets that meet there and how many
    street ends do. The number is checked against the position in the file: the
    page looks a dot up by its number, and a corner's own readings are written
    beside it on the same feature, so the two have to be the same thing.

    The file's own features are handed back as well, and they are what the rest
    of this step writes: this file is the only place the walks can go, so what
    came out of step 2 goes back to disk with three properties added to each
    corner rather than being rebuilt here. Nothing else in the file is touched,
    and step 2's geometry - the same seven decimals, the same separators - is
    what the page reads afterwards.
    '''
    with open(DOTS_GEOJSON, encoding='utf-8') as f:
        source = json.load(f)

    dots = []
    degrees = {}
    for feature in source['features']:
        props = feature['properties']
        assert props['id'] == len(dots), ('the dots are not in the order they '
                                          'are numbered in')
        lon, lat = feature['geometry']['coordinates']
        degree = props['degree']
        degrees[degree] = degrees.get(degree, 0) + 1
        dots.append({
            'id': len(dots), 'point': network.to_metres(lon, lat),
            'title': props['popup_title'], 'degree': degree,
            'degree_word': props['popup_degree'],
        })

    print(f'{len(dots)} street corners read from the intersections file: '
          + ', '.join(f'{ends} ends {degrees[ends]}'
                      for ends in sorted(degrees)))
    return dots, source['features']


def read_entrances():
    '''The metro entrances from step 2, in metres, with their station.

    Only the entrances are wanted. A platform is a place underground that a
    pedestrian cannot reach at all - step 2 reads those rows for their station
    names and leaves them out of the file - and a station point is a label on
    the map rather than a way in, so what a walk ends at is a doorway on the
    pavement, which is what the STM's entrance rows are. Each entrance carries
    the name of its station and whether the STM calls it wheelchair accessible;
    an entrance the STM has not judged is not step-free, because the page offers
    a step-free way in and an unjudged doorway is not that promise.

    An entrance's number is the feature's number in the file, not its place
    among the entrances: the page has the same file in front of it, so a number
    it can look up is worth more than a shorter list here. The build insists
    that every station has at least one entrance, since a station with none
    would be a station nobody could walk to, which would mean the entrance rows
    of the file had been misread.
    '''
    with open(METRO_GEOJSON, encoding='utf-8') as f:
        source = json.load(f)

    stations = set()
    served = set()
    entrances = []
    for number, feature in enumerate(source['features']):
        props = feature['properties']
        if props['location_type'] == '1':
            stations.add(props['stop_id'])
        elif props['location_type'] == '2':
            lon, lat = feature['geometry']['coordinates']
            served.add(props['parent_station'])
            entrances.append({
                'id': number, 'label': 'M%d' % len(entrances),
                'station': props['popup_name'],
                'step_free': props['wheelchair_boarding'] == '1',
                'point': network.to_metres(lon, lat),
            })

    assert served == stations, 'a station has no entrance to walk to'
    step_free = sum(1 for entrance in entrances if entrance['step_free'])
    print(f'{len(entrances)} metro entrances at {len(stations)} stations, '
          f'{step_free} of them step-free')
    return entrances


def read_spaces():
    '''The green spaces from step 2: their names, kinds, sizes and outlines.

    The size is the city's own SUPERFICIE in hectares, and it is the size of
    the whole space even where the city drew that space as several polygons.
    The outlines are those polygons' outer rings in metres: a hole inside a
    space is left out, because measuring to the edge of a hole would claim the
    space was nearer than it is.

    A space's number is the feature's number in the file, as an entrance's is:
    the page has the parks file in front of it and can name what a walk reached
    without being handed a second copy of the names. The file arrives biggest
    first - build_geojson.py sorts it that way - and that order is kept, because
    it is also the order the searches read: where two spaces share a street
    corner, the bigger one is the one a walk there is named after.
    '''
    with open(PARKS_GEOJSON, encoding='utf-8') as f:
        source = json.load(f)

    spaces = []
    kinds = {}
    bands = {}
    for number, feature in enumerate(source['features']):
        props = feature['properties']
        kind_key = props['popup_kind_key']
        assert kind_key in PARK_KIND_OF_KEY, kind_key
        hectares = float(props['SUPERFICIE'])
        band = band_of(hectares)
        kinds[kind_key] = kinds.get(kind_key, 0) + 1
        bands[band] = bands.get(band, 0) + 1
        spaces.append({
            'id': number, 'label': 'P%d' % number,
            'name': props['popup_title'], 'city_kind': kind_key,
            'kind': PARK_KIND_OF_KEY[kind_key], 'ha': hectares, 'band': band,
            'rings': outer_rings(feature['geometry']),
        })

    print(f'{len(spaces)} green spaces read: '
          + ', '.join(f'{key} {count}' for key, count in sorted(kinds.items())))
    print('  by size: ' + ', '.join(f'{key} {bands.get(key, 0)}'
                                    for key, _, _, _ in BANDS))
    return spaces


def band_of(hectares):
    '''Which size band a green space falls in, as one of BANDS' keys.

    The bands are half-open, so a space of exactly five hectares is in the
    five-to-ten band and not in the one below it, and every space lands in
    exactly one of them.
    '''
    for key, _, _, high in BANDS:
        if high is None or hectares < high:
            return key
    raise AssertionError('no band holds %.4f ha' % hectares)


def outer_rings(geometry):
    '''The outer ring of each polygon of a shape, in metres.

    A shape is a polygon or a multipolygon in degrees, and each of its polygons
    has an outer ring and any number of holes. Only the outer rings come back:
    for a green space the hole is not part of the space, while for a borough
    outline it is not part of the borough either way round, so read_outlines
    keeps every ring instead.
    '''
    return [[network.to_metres(lon, lat) for lon, lat in polygon[0]]
            for polygon in polygons_of(geometry)]


def polygons_of(geometry):
    '''The polygons of a shape, each as its list of rings.'''
    if geometry['type'] == 'Polygon':
        return [geometry['coordinates']]
    return geometry['coordinates']


def read_outlines():
    '''The 34 published outlines: their names, their labels, and their rings.

    These are the agglomeration's arrondissements and the linked cities around
    them, and the TYPE column says which is which: the 19 the page draws are the
    city's own Arrondissements, and the 15 linked cities are what the page's grey
    mask covers. They decide whether a dot is in the city at all - a dot outside
    all 34 is not measured - and they say which of the ones drawn a dot belongs
    to. They are also written to site/data/boroughs.geojson, in degrees, for the
    page to draw its edge round and its mask out of. So both copies of each ring
    are kept: the degrees the city published and the metres this build measures
    in. So is the label, which is what lets the page know which of the 34 it
    draws without a list of names of its own.

    Every ring is kept, holes included, and a point counts as inside an outline
    when it is inside an odd number of its rings - so a hole really is outside
    the borough that surrounds it, which is how a linked city enclosed by a
    borough (Mont-Royal, say) stays outside it. Green spaces are read the other
    way round (outer rings only, in outer_rings): a hole in a park is not part of
    the park.
    '''
    with open(OUTLINES_JSON, encoding='utf-8') as f:
        source = json.load(f)

    outlines = []
    for feature in source['features']:
        degrees = polygons_of(feature['geometry'])
        polygons = [[[network.to_metres(lon, lat) for lon, lat in ring]
                     for ring in polygon] for polygon in degrees]
        rings = [ring for polygon in polygons for ring in polygon]
        outlines.append({
            'name': feature['properties']['NOM'],
            'type': feature['properties']['TYPE'],
            'rings': rings,
            'bounds': network.bounds_of_rings(rings),
            'degrees': [[[[round(lon, COORD_DIGITS), round(lat, COORD_DIGITS)]
                          for lon, lat in ring] for ring in polygon]
                        for polygon in degrees],
        })

    drawn = sum(1 for outline in outlines if outline['type'] == FOCUS_TYPE)
    print(f'{len(outlines)} published outlines read, {drawn} of them the page '
          'draws, from ' + outlines[0]['name'] + ' to ' + outlines[-1]['name'])
    return outlines


def in_city(point, outlines):
    '''True when a point falls inside one of the 34 published outlines.'''
    for outline in outlines:
        west, south, east, north = outline['bounds']
        if not (west <= point[0] <= east and south <= point[1] <= north):
            continue
        if network.point_in_rings(point, outline['rings']):
            return True
    return False


def mark_focus(dots, outlines):
    '''Which dot is in which of the outlines the page draws, and in which of the
    linked cities the ones it does not draw are.

    The drawn outlines are the ones the city's file labels FOCUS_TYPE, and a dot
    belongs to the first of the 34 that holds it, in the order the city's file
    gives them - the rule the whole build uses, so a dot is never counted twice
    however two outlines abut. A dot in none of the 34 is not in the city and is
    counted nowhere. The count in each of the 19 is printed, because those counts
    are what the page's legend is built on: a legend that says 19,360 corners is
    only worth reading if the 19 really hold that many. So is the count in the 15
    linked cities, which no walk is ever drawn for and which the page covers with
    its grey.

    Every dot that is inside any outline is matched to one of them here, and
    main() checks that against the independent sweep in in_city(), so a rule that
    left a dot out or counted one twice would stop the build rather than shrink
    the map without saying so.

    Returns (the drawn outlines, the masked outlines, the borough of every dot or
    None, the count in each of the 34).
    '''
    labels = {}
    for outline in outlines:
        labels[outline['type']] = labels.get(outline['type'], 0) + 1
    assert set(labels) == {FOCUS_TYPE, OTHER_TYPE}, labels
    drawn = [outline for outline in outlines if outline['type'] == FOCUS_TYPE]
    masked = [outline for outline in outlines if outline['type'] == OTHER_TYPE]

    borough = []
    counts = {}
    for dot in dots:
        here = None
        x, y = dot['point']
        for outline in outlines:
            west, south, east, north = outline['bounds']
            if not (west <= x <= east and south <= y <= north):
                continue
            if network.point_in_rings(dot['point'], outline['rings']):
                here = outline['name']
                break
        borough.append(here)
        if here is not None:
            counts[here] = counts.get(here, 0) + 1
    for outline in drawn:
        assert counts.get(outline['name']), ('no street corner at all in '
                                             + outline['name'])

    drawn_names = [outline['name'] for outline in drawn]
    masked_names = [outline['name'] for outline in masked]
    print(f'{sum(counts[name] for name in drawn_names)} of the {len(dots)} '
          f'corners are in the {len(drawn)} boroughs the page draws: '
          + ', '.join(f'{name} {counts[name]}' for name in drawn_names))
    print(f'{sum(counts.get(name, 0) for name in masked_names)} of them are in '
          f'the {len(masked)} linked cities it masks: '
          + ', '.join(f'{name} {counts.get(name, 0)}' for name in masked_names))
    return drawn, masked, borough, counts


# --- Building the network, and putting the destinations on it -------------

def build_network(lines):
    '''The street network as a graph, plus a report of what was done to it.

    The four steps are the ones network.py sets out: snap the troncons that
    share a corner onto one corner, join the troncon ends that land a few
    centimetres off the side of another troncon, join the crossings that have
    no corner on either line, and drop the links the file holds twice. What is
    left is a network whose corners meet where the map meets, which is the
    whole of what walking needs.

    The corners' positions are also kept as they were before the second and
    third steps moved any of them, because a dot from step 2 stands where the
    vertex was read, and that is where its own corner of the network is found.
    Those two steps only ever nudge a corner by a few centimetres onto a line
    it already ends on, so this is bookkeeping about which corner is which
    rather than a second network.

    Returns (first positions, corners, links, corners carrying no link, report).
    '''
    nodes, edges = network.merge_endpoints(lines)
    first = list(nodes)
    report = {'corners': len(nodes), 'links': len(edges)}
    print(f"{report['corners']} corners and {report['links']} links after "
          'snapping the troncons that share a corner onto it')

    report['split'], report['rebuilt'] = network.split_crossings(nodes, edges)
    report['crossings'] = network.join_crossing_links(nodes, edges)
    edges, report['copies'], report['stubs'] = network.drop_duplicate_edges(edges)
    report['links'] = len(edges)

    pieces, _, lonely = network.count_components(nodes, edges)
    print(f"{report['split']} troncon ends found landing on the side of a "
          f"street, {report['crossings']} mid-air crossings joined")
    print(f"{report['copies']} doubled links and {report['stubs']} stubs "
          f"dropped, {report['links']} links left to walk")
    print(f'the network comes in {pieces} pieces, and {len(lonely)} corners '
          'carry no link')
    return first, nodes, edges, lonely, report


def corner_nodes(first, dots):
    '''Which corner of the network each dot is, and the check that it is one.

    Both files come out of the same vertices in the same order: step 2 walked
    every troncon's points, kept the ones within a tenth of a metre of a corner
    it had already seen, and wrote out the ones three or more street ends meet
    at. This build reads the same troncons the same way, so a dot and its
    corner of the network are the same point to within the rounding in the file
    - a centimetre or so - and the match is made by distance rather than by
    assuming an order, which would quietly tie the wrong walk to every dot the
    day either file changed.

    The lookup is by 25 m square, as the network's own lookups are, so a dot is
    compared with the handful of corners around it rather than with all of
    them. Every dot has to find a corner, no two dots may claim the same
    corner, and the loosest match is printed: a build that cannot tie a dot to
    a corner stops here rather than measuring from somewhere else.
    '''
    squares = {}
    for index, point in enumerate(first):
        squares.setdefault(_square(point), []).append(index)

    found = []
    worst = 0.0
    for dot in dots:
        x, y = dot['point']
        cx, cy = _square(dot['point'])
        best = None
        for sx in (cx - 1, cx, cx + 1):
            for sy in (cy - 1, cy, cy + 1):
                for index in squares.get((sx, sy), ()):
                    px, py = first[index]
                    gap = math.hypot(px - x, py - y)
                    if best is None or gap < best[0]:
                        best = (gap, index)
        assert best is not None and best[0] <= TIE_METRES, (dot['id'], best)
        worst = max(worst, best[0])
        found.append(best[1])

    assert len(set(found)) == len(dots), 'two dots claim one street corner'
    print(f'{len(dots)} dots tied to their own corner of the network, the '
          f'loosest by {worst * 100:.1f} cm')
    return found


def _square(point):
    '''Which 25 m square of the lookup a point is in.'''
    return (math.floor(point[0] / network.CELL_METRES),
            math.floor(point[1] / network.CELL_METRES))


def attach_destinations(nodes, edges, spaces, entrances, outlines):
    '''Put every metro entrance and every park gateway onto the network.

    An entrance is a point from step 2. A gateway is a point laid along the
    edge of a green space every GATEWAY_METRES, and it is what gives a walk
    somewhere to finish: the park's own edge, reached from the nearest street.
    Both are the same operation - the nearest link to a point, split at it -
    so both go on in one pass and the network is rebuilt once.

    Each destination keeps the corner it was attached to and the short distance
    from that corner to the point itself, because that stretch is the end of
    the walk: the search starts from the corner with that distance already
    covered. Where two gateways of one space share a corner, the nearer wins,
    and the space's other corners are kept as they are.

    Returns the corners of each destination, by label. Points that found no
    street are reported by name: an entrance with no street within ATTACH_METRES
    is worth knowing about - most of them are entrances the city's file has no
    streets at, stations that are themselves outside the 34 outlines - and a
    gateway on the far side of a park is not. The build insists that no station
    inside the city is left with no entrance at all, which is the promise the
    page makes about the metro.
    '''
    points = [(entrance['label'], entrance['point']) for entrance in entrances]
    entrances_laid = len(points)
    for space in spaces:
        for ring in space['rings']:
            for point in network.sample_ring(ring, GATEWAY_METRES):
                points.append((space['label'], point))

    corners, approaches, missed = network.attach_points(nodes, edges, points,
                                                        ATTACH_METRES)
    nearest = {}
    for (label, _), corner, approach in zip(points, corners, approaches):
        if corner is None:
            continue
        per_label = nearest.setdefault(label, {})
        if corner not in per_label or approach < per_label[corner]:
            per_label[corner] = approach
    corners_of = {label: list(per_label.items())
                  for label, per_label in nearest.items()}

    entrance_missed = [entrances[number] for number in range(entrances_laid)
                       if corners[number] is None]
    gateways_missed = len(missed) - len(entrance_missed)
    attached = {entry['station'] for entry, corner in zip(entrances, corners)
                if corner is not None}
    stranded = sorted({entry['station'] for entry, corner in zip(entrances,
                                                                 corners)
                       if corner is None and in_city(entry['point'], outlines)})
    print(f'{entrances_laid} metro entrances and '
          f'{len(points) - entrances_laid} park gateways laid along the park '
          f'edges every {GATEWAY_METRES:.0f} m')
    print(f'{len(corners_of)} destinations have a corner on the network; '
          f'{gateways_missed} gateways and {len(entrance_missed)} entrances '
          f'have no street within {ATTACH_METRES:.0f} m')
    if entrance_missed:
        print('  entrances with no street near them: '
              + ', '.join(f"{entry['label']} {entry['station']}"
                          for entry in entrance_missed))
    assert not set(stranded) - attached, (
        f'stations inside the city with no entrance on the network: {stranded}')

    return corners_of


# --- What the layers are --------------------------------------------------

def plan_layers():
    '''Every layer the page can ask for, in the order they are built.

    Twelve green-space layers are searched for, one per pair of a kind and a
    size band. Eight are then the minimum of those twelve, dot by dot: the four
    bands' minimum is "any size" of each kind, the three kinds' minimum is "any
    kind" of each band, and all twenty green-space layers together are the
    layer that is any space at all. The two metro layers are searched for and
    nothing is derived from them, because the two filters are separate: a
    person can want the metro without wanting a park, and no layer here adds
    the two together.
    '''
    plan = []
    for kind_key, kind_label in KINDS:
        for band_key, band_label, _, _ in BANDS:
            plan.append(_layer('park_%s_%s' % (kind_key, band_key), 'park',
                               kind_key, kind_label, band_key, band_label))
    for kind_key, kind_label in KINDS:
        plan.append(_layer('park_%s_any' % kind_key, 'park', kind_key,
                           kind_label, ANY_BAND[0], ANY_BAND[1],
                           ['park_%s_%s' % (kind_key, band_key)
                            for band_key, _, _, _ in BANDS]))
    for band_key, band_label, _, _ in BANDS:
        plan.append(_layer('park_any_%s' % band_key, 'park', ANY_KIND[0],
                           ANY_KIND[1], band_key, band_label,
                           ['park_%s_%s' % (kind_key, band_key)
                            for kind_key, _ in KINDS]))
    plan.append(_layer('park_any_any', 'park', ANY_KIND[0], ANY_KIND[1],
                       ANY_BAND[0], ANY_BAND[1],
                       ['park_any_%s' % band_key
                        for band_key, _, _, _ in BANDS]))
    for layer_id, label, kind, kind_label in METRO_LAYERS:
        plan.append({'id': layer_id, 'family': 'metro', 'kind': kind,
                     'kind_label': kind_label, 'band': 'metro',
                     'band_label': '', 'label': label, 'parents': None})
    return plan


def _layer(layer_id, family, kind, kind_label, band, band_label, parents=None):
    '''One green-space layer of the plan: what it is called, what it is made of.

    A layer with no parents is searched for; one with parents is the minimum of
    them. The wording here is what the page's menus and its legend are built
    from, so the page holds no vocabulary of its own and a layer cannot be
    described in two places.
    '''
    return {'id': layer_id, 'family': family, 'kind': kind,
            'kind_label': kind_label, 'band': band, 'band_label': band_label,
            'label': '%s, %s' % (kind_label, band_label), 'parents': parents}


def layer_by_choice(plan, family, kind, band):
    '''The one layer a set of filter choices is: the build's own name for it.

    The page's controls are a family, a kind and a size band, and each of the
    twenty-two combinations is exactly one layer; a combination with no layer
    is a menu choice the page offers and the build cannot answer, so the build
    refuses to write readings it would not be able to serve.
    '''
    found = [layer for layer in plan if layer['family'] == family
             and layer['kind'] == kind and layer['band'] == band]
    assert len(found) == 1, (family, kind, band, len(found))
    return found[0]


def check_plan(plan):
    '''Every combination the page's menus can make has a layer of its own.

    Twenty green-space combinations and two metro ones. Asked here rather than
    left to the page, which cannot answer a question the build never measured.
    '''
    asked = []
    for kind_key, _ in (ANY_KIND,) + KINDS:
        asked.append(('park', kind_key, ANY_BAND[0]))
        for band_key, _, _, _ in BANDS:
            asked.append(('park', kind_key, band_key))
    for _, _, kind, _ in METRO_LAYERS:
        asked.append(('metro', kind, 'metro'))

    assert len(asked) == len(plan), (len(asked), len(plan))
    for family, kind, band in asked:
        layer_by_choice(plan, family, kind, band)
    print(f'{len(plan)} layers, one for each of the {len(asked)} questions '
          'the page can ask')
    return plan


def spaces_for(spaces, kind, band):
    '''The green spaces a layer is about, in the order the city's file puts them.

    "Any" is not a group of its own: a layer that asks for any kind or any size
    is about every space. The same few lines therefore answer for a searched
    layer and for a derived one, which is why the two never disagree about what
    "any" means.
    '''
    return [space for space in spaces
            if (kind == 'any' or space['kind'] == kind)
            and (band == 'any' or space['band'] == band)]


def layer_sources(layer, spaces, entrances, corners_of):
    '''The corners a layer's search starts from, under its destination's name.

    Each corner arrives with the metres from it to the destination's own door
    or edge - the last little stretch of the walk, which the search starts from
    with that much already covered.

    A destination with several ways in - a space ringed by streets, a station
    with four entrances - arrives as several corners, and every one of them is
    a place a walk can finish at. Where two destinations arrive at one corner
    the first listed keeps it, and the destinations are listed in the order the
    city's file puts them: biggest first, so a corner is named after the bigger
    space.
    '''
    sources = {}
    if layer['family'] == 'metro':
        for entrance in entrances:
            if layer['kind'] == 'stepfree' and not entrance['step_free']:
                continue
            for corner, approach in corners_of.get(entrance['label'], ()):
                sources.setdefault(corner, (entrance['id'], approach))
        return sources

    for space in spaces_for(spaces, layer['kind'], layer['band']):
        for corner, approach in corners_of.get(space['label'], ()):
            sources.setdefault(corner, (space['id'], approach))
    return sources


# --- Measuring the layers -------------------------------------------------

def measure_layer(dots, nodes_of, inside, lonely, metres, reached):
    '''One searched layer: every dot's walk in seconds, and what it reached.

    A dot is a corner of the network, so its walk is the search's own answer
    with nothing added at the near end: the distance from that corner to the
    door or the edge of what it reached, which the search was handed as the
    first link of every walk. That is the whole of the arithmetic - no cell is
    rounded to a grid, because a dot is already a place.

    The destination comes from the corner the search reached it at, so a dot
    ends up naming one particular space or station rather than only a number of
    minutes.

    Returns (seconds, destination), one entry per dot in the file's own order,
    with the statements from the top of this file where there is no walk to put
    there.
    '''
    lonely = set(lonely)
    seconds = array('H', [OUTSIDE]) * len(dots)
    destination = array('H', [NO_DESTINATION]) * len(dots)
    speed = network.WALK_SPEED_MPS
    to_metres = metres.get

    for number in range(len(dots)):
        if not inside[number]:
            continue
        node = nodes_of[number]
        if node in lonely:
            seconds[number] = NO_STREET
            continue
        value = to_metres(node)
        if value is None:
            # the dot is on the network, but its piece of it reaches none of
            # this layer's destinations
            seconds[number] = UNREACHABLE
            continue

        walked = int(value / speed + 0.5)
        assert walked <= MAX_SECONDS, (dots[number]['id'], walked)
        seconds[number] = walked
        destination[number] = reached[node]

    return seconds, destination


# --- The checks -----------------------------------------------------------

def check_statements(dots, inside, nodes_of, lonely, layers):
    '''Every dot that cannot be measured carries the right statement.

    Two things stop a dot being measured - it is outside the 34 outlines, or it
    stands on a corner the network has no link at - and those are the only two,
    so a dot is either measurable or exactly one of them. Each of those dots is
    looked at in each of the layers and has to carry the statement that belongs
    to it, and the counts of both statements are then compared with the number
    of dots that should have them, which is what rules out the other half of
    that mistake: a statement written over a dot the build did measure.
    '''
    lonely = set(lonely)
    outside = [number for number in range(len(dots)) if not inside[number]]
    no_link = [number for number in range(len(dots))
               if inside[number] and nodes_of[number] in lonely]

    for layer_id, (seconds, _) in layers.items():
        for number in outside:
            assert seconds[number] == OUTSIDE, (layer_id, number,
                                                seconds[number])
        for number in no_link:
            assert seconds[number] == NO_STREET, (layer_id, number,
                                                  seconds[number])
        assert seconds.count(OUTSIDE) == len(outside), layer_id
        assert seconds.count(NO_STREET) == len(no_link), layer_id

    print(f'{len(outside)} dots lie outside the outlines and {len(no_link)} '
          f'are in the city on a corner with no link; all {len(layers)} layers '
          'say so')


def check_reach(layers):
    '''No layer is quicker than the layer it is a subset of.

    A step-free entrance is one of the entrances, so no dot can be quicker to a
    step-free entrance than to an entrance as such: the step-free search starts
    from fewer places, and a search from fewer places cannot find a nearer
    anything. Every dot that can be walked from also has to hold a real walk in
    both layers - the statements are the only other thing a number can be, and
    where one belongs the check above says so.
    '''
    stepfree = layers['metro_stepfree'][0]
    any_entrance = layers['metro_any'][0]
    reachable = 0
    for number in range(len(stepfree)):
        assert stepfree[number] >= any_entrance[number], (number,
                                                          stepfree[number],
                                                          any_entrance[number])
        if stepfree[number] < UNREACHABLE:
            reachable += 1
    assert reachable, 'no dot at all reaches a step-free entrance'
    assert min(any_entrance) < 3600, min(any_entrance)

    print('the step-free layer is never quicker than the any-entrance layer, '
          f'dot by dot, and {reachable} of the {len(stepfree)} dots can be '
          'walked from to one')


def check_derived(layers, by_id, plan):
    '''A derived layer is the minimum of its parents, and names what they named.

    The build makes these layers by taking that minimum itself, so what is
    checked here is the bookkeeping around it: that the destination number
    carried out of each parent is the number that parent had reached, which is
    what a translation done wrongly would get wrong. The dots are walked with a
    fixed stride rather than picked at random, so a run that fails can be
    looked at again with the same dots.
    '''
    total = len(layers[plan[0]['id']][0])
    stride = 97
    checked = 0
    for layer in plan:
        if not layer['parents']:
            continue
        seconds, destination = layers[layer['id']]
        parents = [by_id[parent_id] for parent_id in layer['parents']]
        for number in range(0, total, stride):
            values = [layers[parent['id']][0][number] for parent in parents]
            assert seconds[number] == min(values), (layer['id'], number)
            winner = parents[values.index(min(values))]
            reached = layers[winner['id']][1][number]
            if reached == NO_DESTINATION:
                assert destination[number] == NO_DESTINATION, (layer['id'],
                                                               number)
            else:
                assert destination[number] == reached, (layer['id'], number)
            checked += 1

    print(f'{checked} dots checked over the derived layers: each one is the '
          'minimum of its parents, and names the destination a parent named')


def crow_flies_distance(point, layer, destination, spaces, entrances):
    '''The shortest line on the map from a point to the destination reached.

    For a metro entrance that is the line to the doorway itself. For a green
    space it is the line to the nearest point of its edge, and zero when the
    point is inside the space - which is the distance the walk would find if
    walking through a park were modelled, and on this map it is not.

    Spaces and entrances are looked up by their number, which is the number
    their own file gives them, and not by their place in a list: a number in a
    layer file means a place, and this is the one place in the build that has
    to turn a number back into one.
    '''
    if layer['family'] == 'metro':
        x, y = entrances[destination]['point']
        return math.hypot(x - point[0], y - point[1])

    best = float('inf')
    for ring in spaces[destination]['rings']:
        if network.point_box_distance(point, network.ring_bounds(ring)) >= best:
            continue
        best = min(best, network.point_ring_distance(point, ring))
    return best


def check_crow_flies(dots, layers, plan, spaces, entrances):
    '''Every walk beats the crow-flies distance to what it reached.

    The one check that reads the city's own geometry rather than this build's.
    A walk here is a chain of lines: along the streets from the dot to whatever
    it reached, and the last stretch from the street to that destination's own
    door or edge. A chain of lines is never shorter than the straight line
    between its ends, so a walk can never be shorter than the crow-flies
    distance to the thing it reached. The comparison is made with a metre of
    room in it, and that metre is the whole of the slack there is: half a second
    at the walking speed, 0.675 m, can be missing from a walk that is written in
    whole seconds, and the corner the dot stands for is read here out of a file
    that rounds it to seven decimals, about a centimetre. A walk more than that
    shorter than the crow flies would mean the network had been joined somewhere
    the map does not join, which is the mistake the mid-air crossing joins can
    make.

    The dots are taken with a fixed stride, so the sample is the same on every
    run, and spaces and entrances are keyed by their own numbers, which are
    what a layer file's destination numbers are.
    '''
    speed = network.WALK_SPEED_MPS
    searched = [layer for layer in plan if not layer['parents']]
    measurable = [dot for dot in range(len(dots))
                  if layers[searched[0]['id']][0][dot] < UNREACHABLE]
    step = max(1, len(measurable) // CHECK_DOTS)
    tightest = None
    checked = 0
    for layer in searched:
        seconds, destination = layers[layer['id']]
        for number in measurable[::step]:
            crow = crow_flies_distance(dots[number]['point'], layer,
                                       destination[number], spaces, entrances)
            walked = seconds[number] * speed
            assert walked + 1.0 >= crow, (layer['id'], number, walked, crow)
            margin = walked - crow
            tightest = margin if tightest is None else min(tightest, margin)
            checked += 1

    print(f'{checked} walks over the {len(searched)} searched layers beat the '
          f'crow-flies distance to what they reached, the closest by '
          f'{tightest:.0f} m')


# --- Counting what the page will draw -------------------------------------

def within_counts(seconds, numbers, minutes=TICK_MINUTES):
    '''How many of the page's dots are within each of a few times.

    Counted over the corners of the boroughs the page draws and no others,
    because those are the only dots a colour is ever put on. Each
    number counts everything shorter than it as well, so a dot within five
    minutes is one of the dots within fifteen: these are the numbers the page's
    legend adds up to, and the page counts the same corners off the file it
    reads and checks itself against this one.

    The statements are all larger than any limit, so a dot with no walk is
    never counted into a band by arithmetic accident.
    '''
    limits = [minute * 60 for minute in minutes]
    counts = [0] * len(limits)
    for number in numbers:
        value = seconds[number]
        for index, limit in enumerate(limits):
            if value <= limit:
                counts[index] += 1
    return {str(minute): count for minute, count in zip(minutes, counts)}


def reached_count(seconds, numbers):
    '''How many of the page's dots have a walk on the layer at all.

    Everything that is neither outside the outlines, nor on a corner with no
    link, nor in a piece of network that reaches nothing - which is what the
    page needs to know to say how many of its dots a layer can colour.
    '''
    return sum(1 for number in numbers if seconds[number] < UNREACHABLE)


def derive_layer(parents):
    '''A layer that is the minimum of others, dot by dot.

    The minimum is exact rather than a convenience: the four size bands hold
    every green space once each, and so do the three kinds, so the nearest
    space of any size or kind really is the nearest of the parts. The
    statements at the top of this file are larger than any walk, and a dot's
    statement is the same in every layer - a dot outside the outlines is
    outside them whatever is being asked for - so taking the smaller of two
    numbers always lands on the truth.

    The destination is carried over from whichever parent won, and it needs no
    translation: a destination's number is its own number in the city's file,
    so the same number means the same space in every one of the 22 layers.
    '''
    total = len(parents[0][0])
    seconds = array('H', [OUTSIDE]) * total
    destination = array('H', [NO_DESTINATION]) * total

    for number in range(total):
        best = None
        for parent_seconds, parent_destination in parents:
            value = parent_seconds[number]
            if best is None or value < best[0]:
                best = (value, parent_destination[number])
        seconds[number] = best[0]
        destination[number] = best[1]

    return seconds, destination


# --- Writing --------------------------------------------------------------
#
# Two files come out of this step: the intersections step 2 wrote, with this
# step's readings added to every corner of it, and the 34 outlines the page's
# mask is cut round and its edges are drawn on. Both go to a temporary name
# first, are read back and checked, and only then take their place, so a run
# that fails leaves the page with the files it already had rather than with
# half of new ones.

def borough_features(outlines):
    '''The 34 outlines as GeoJSON features: a name, whether it is drawn, a shape.

    Only the name, the flag and the shape are written. The page uses the shapes
    to cut its mask round the ones it draws and to draw their edges, and the flag
    to know which those are, so it needs no list of nineteen names of its own and
    cannot disagree with this build about the extent of the map. The city's other
    columns - a third of a megabyte of French - are dropped: the page reads
    nothing else.
    '''
    return [{'type': 'Feature',
             'properties': {'name': outline['name'],
                            'drawn': outline['type'] == FOCUS_TYPE},
             'geometry': {'type': 'MultiPolygon',
                          'coordinates': outline['degrees']}}
            for outline in outlines]


def write_boroughs(outlines):
    '''Write the 34 outlines, and check the file that lands.

    build_geojson.write_checked does the work: it writes to a temporary name,
    reads the file back, and moves it into place only if the checks pass. The
    checks here are the count of features, the name and the drawn flag of each
    one, and the number of points in each shape, which is what would catch a ring
    written twice or a shape written empty.
    '''
    def check(path):
        with open(path, encoding='utf-8') as f:
            written = json.load(f)
        assert len(written['features']) == len(outlines), 'a different count'
        for feature, outline in zip(written['features'], outlines):
            assert feature['properties']['name'] == outline['name']
            assert feature['properties']['drawn'] == (
                outline['type'] == FOCUS_TYPE), outline['name']
            points = sum(len(ring)
                         for polygon in feature['geometry']['coordinates']
                         for ring in polygon)
            wanted = sum(len(ring) for polygon in outline['degrees']
                         for ring in polygon)
            assert points == wanted, (outline['name'], points, wanted)

    build_geojson.write_checked(borough_features(outlines), BOROUGHS_GEOJSON,
                                indent=None, check=check)


def build_walk_file(features, plan, readings, borough, names, counts, coverage):
    '''Step 2's file, with this step's answers written onto every corner.

    Three properties are added to each corner and nothing else is touched: the
    outline it stands in, whether the page may colour it - one of the 19
    boroughs and not one of the 15 cities linked to the city - and its 22 walks.
    A walk is a pair, [seconds, destination], in the order walk_layers lists
    them, so the same question is at the same place on every corner of the map
    and the page reads one column of one question without a second file to keep
    in step with the first. A destination is the destination's own number in
    city_parks.geojson or metro_stops.geojson, the two files the page already
    has open, so the same number means the same place in every layer.

    Everything true of the whole map is written once, at the top, where the
    manifest used to be: the wording of both filters rather than the page
    inventing it, the four statements, the counts the page checks itself
    against, the numbers its notes carry, and the 22 layers with the number of
    corners each reaches and how many are within each of the times the filters
    offer. The walks on the corners are what the layer files used to be, so the
    page fetches four GeoJSON files and there is no second file that can fall
    out of step with what is written beside each corner.

    The counts each layer was measured to are counted over the corners the page
    may colour and no others, which is what makes them checkable: the page
    colours its own corners, adds up what it coloured and compares the total
    with the number here, and a disagreement is a bug on one side or the other.

    The corners are handed back the way step 2 wrote them - the same geometry,
    the same order, the same separators - because this build has no business
    rounding a coordinate or moving a property it did not add.
    '''
    drawn_names = set(names)
    written = [{'id': layer['id'], 'family': layer['family'],
                'kind': layer['kind'], 'band': layer['band'],
                'reached': layer['reached'], 'within': layer['within']}
               for layer in plan]
    columns = [readings[layer['id']] for layer in plan]

    for number, feature in enumerate(features):
        props = feature['properties']
        assert props['id'] == number, ('the corners are not in the order they '
                                       'are numbered in')
        place = borough[number]
        props['drawn'] = place in drawn_names
        props['borough'] = place
        props['walk'] = [[seconds[number], destination[number]]
                         for seconds, destination in columns]

    return {
        'type': 'FeatureCollection',
        'built_by': 'site/build_walk_times.py, step 3',
        'measured_from': 'data/intersections.geojson (step 2)',
        'walk_speed_mps': network.WALK_SPEED_MPS,
        'reach_m': ATTACH_METRES,
        'gateway_m': GATEWAY_METRES,
        'statements': {'outside': OUTSIDE, 'no_street': NO_STREET,
                       'unreachable': UNREACHABLE,
                       'no_destination': NO_DESTINATION},
        'coverage': dict(coverage, boroughs=[{'name': name,
                                              'corners': counts[name]}
                                             for name in names]),
        'tick_minutes': list(TICK_MINUTES),
        'bands': ([{'key': ANY_BAND[0], 'label': ANY_BAND[1]}] + [
            {'key': key, 'label': label} for key, label, _, _ in BANDS]),
        'kinds': ([{'key': ANY_KIND[0], 'label': ANY_KIND[1]}] + [
            {'key': key, 'label': label} for key, label in KINDS]),
        'metro_kinds': [{'key': kind, 'label': label}
                        for _, _, kind, label in METRO_LAYERS],
        'walk_layers': written,
        'features': features,
    }


def check_walk_file(built, read_back):
    '''The file on disk says what the build meant it to say.

    The whole collection is compared rather than a few fields: json reads back
    exactly what was written - the same numbers, the same order, the same
    empties - so the second copy is either the one that was built or it is not,
    and there is nothing in between to argue about.

    Each corner is then read the way the page reads it, one pair of numbers per
    layer in the order walk_layers lists them, and the last layer's counts are
    worked out again from those pairs alone. A column written a place out of
    step with walk_layers would land its counts somewhere else here, and it is
    that alignment - one question at one place on every corner - that every
    colour on the map depends on.
    '''
    assert read_back == built, 'the file on disk is not what was built'
    layers = read_back['walk_layers']
    for feature in read_back['features']:
        props = feature['properties']
        assert len(props['walk']) == len(layers), (props['id'],
                                                   len(props['walk']))

    last = len(layers) - 1
    reached = 0
    within = {str(minute): 0 for minute in TICK_MINUTES}
    for feature in read_back['features']:
        props = feature['properties']
        if not props['drawn']:
            continue
        seconds = props['walk'][last][0]
        if seconds < UNREACHABLE:
            reached += 1
        for minute in TICK_MINUTES:
            if seconds <= minute * 60:
                within[str(minute)] += 1
    assert reached == layers[last]['reached'], (reached, layers[last]['reached'])
    assert within == layers[last]['within'], (within, layers[last]['within'])

    print(f'the file reads back: {len(read_back["features"])} corners with '
          f'{len(layers)} walks each, and its last layer '
          f'({layers[last]["id"]}) counts the same corners again')


def write_walk_geojson(collection):
    '''Write the intersections file, and check it by reading it back.

    Compact, and in step 2's own separators, which is what makes the file the
    same size of thing it was: the corners are the ones step 2 wrote, so a run
    of this step shows up as the readings it added rather than as a file
    rewritten from scratch. It goes to a temporary name, is read back and
    checked, and only then replaces what is there, so a run that fails leaves
    the page with the file it already had.
    '''
    temp = DOTS_GEOJSON.with_name(DOTS_GEOJSON.name + '.new')
    text = json.dumps(collection, ensure_ascii=False, separators=(',', ':'))
    temp.write_text(text, encoding='utf-8')
    with open(temp, encoding='utf-8') as f:
        check_walk_file(collection, json.load(f))
    temp.replace(DOTS_GEOJSON)
    print(f'{DOTS_GEOJSON.name} written '
          f'({DOTS_GEOJSON.stat().st_size / 1048576.0:.1f} MB), '
          f'{len(collection["walk_layers"])} walks on every corner')


def summarise(layers, plan):
    '''One line per layer, so a run can be read at a glance.

    Dots within 5, 15 and 30 minutes of what the layer is looking for, over the
    corners of the boroughs the page draws, and the share of the
    corners the layer reaches at all. The counts are the same numbers the page
    is given on the layers rather than a second working of them, so the table
    cannot disagree with what the page checks itself against. A layer
    that looks wrong here - a size band with almost nothing in it, a metro
    layer reaching further than the parks do - is visible without the page
    having to be opened.
    '''
    print(f"{'layer':30s}{'reached':>9s}{'5 min':>9s}{'15 min':>9s}"
          f"{'30 min':>9s}{'30 min, of reached':>20s}")
    for layer in plan:
        counts = layer['within']
        share = ('     n/a' if not layer['reached'] else
                 '%7.0f%%' % (100.0 * counts['30'] / layer['reached']))
        print(f"{layer['id']:30s}{layer['reached']:9d}{counts['5']:9d}"
              f"{counts['15']:9d}{counts['30']:9d}{share:>20s}")


# --- Putting the whole step together --------------------------------------

def main():
    '''Read the five files, measure every layer, check them, write them out.'''
    started = time.perf_counter()

    dots, corner_features = read_dots()
    lines = read_streets()
    entrances = read_entrances()
    spaces = read_spaces()
    outlines = read_outlines()

    inside = [in_city(dot['point'], outlines) for dot in dots]
    drawn, _, borough, counts = mark_focus(dots, outlines)
    # The corners the page may colour: the ones in one of the outlines it draws.
    # A corner in one of the linked cities is measured like any other and is not
    # in this list - it is under the page's grey, and the page says so - and a
    # corner in none of the 34 is not in the city at all.
    names = [outline['name'] for outline in drawn]
    drawn_names = set(names)
    focus = [number for number in range(len(dots))
             if borough[number] in drawn_names]
    # Every dot the outlines hold is matched to one of them and every dot outside
    # them is matched to none: the two ways of asking - in_city()'s sweep, one
    # dot at a time, and mark_focus()'s rule for all of them at once - agree
    # about every corner of the map, so a rule that dropped a corner or counted
    # one twice stops the build rather than shrinking the map quietly.
    assert sum(counts.values()) == sum(inside), (sum(counts.values()),
                                                sum(inside))
    assert len(focus) == sum(counts[name] for name in drawn_names)
    masked_corners = len(dots) - len(focus)

    first, nodes, edges, lonely, _ = build_network(lines)
    nodes_of = corner_nodes(first, dots)
    corners_of = attach_destinations(nodes, edges, spaces, entrances, outlines)
    adjacency = network.build_adjacency(nodes, edges)

    plan = check_plan(plan_layers())
    by_id = {layer['id']: layer for layer in plan}
    layers = {}

    # The fourteen layers that need a search of their own: one Dijkstra per
    # layer, spreading out from every corner its destinations arrive at.
    for layer in plan:
        if layer['parents']:
            continue
        sources = layer_sources(layer, spaces, entrances, corners_of)
        metres, reached = network.walk_metres(adjacency, sources)
        layers[layer['id']] = measure_layer(dots, nodes_of, inside, lonely,
                                            metres, reached)
        destinations = {reached_at for reached_at, _ in sources.values()}
        print(f"  {layer['id']}: from {len(sources)} corners to "
              f'{len(destinations)} destinations')

    # The eight that are the minimum of the fourteen.
    for layer in plan:
        if not layer['parents']:
            continue
        layers[layer['id']] = derive_layer([layers[parent_id]
                                            for parent_id in layer['parents']])

    check_statements(dots, inside, nodes_of, lonely, layers)
    check_reach(layers)
    check_derived(layers, by_id, plan)
    check_crow_flies(dots, layers, plan,
                     {space['id']: space for space in spaces},
                     {entrance['id']: entrance for entrance in entrances})

    for layer in plan:
        seconds, destination = layers[layer['id']]
        layer['reached'] = reached_count(seconds, focus)
        layer['within'] = within_counts(seconds, focus)

    write_boroughs(outlines)
    write_walk_geojson(build_walk_file(
        corner_features, plan, layers, borough, names, counts,
        {'dots': len(dots), 'in_outlines': sum(inside),
         'outside': len(dots) - sum(inside), 'no_street': len(lonely),
         'focus': len(focus), 'masked': masked_corners}))

    summarise(layers, plan)
    print(f'{len(plan)} layers, {len(focus)} of the {len(dots)} corners drawn '
          'by the page')
    print(f'Done in {time.perf_counter() - started:.1f} s')


if __name__ == '__main__':
    main()
