"""
build.py

The whole build, in one file: the raw project data in data/ turned into the
four GeoJSON files site/index.html loads. It used to be three scripts -
network.py, build_geojson.py and build_walk_times.py - and the steps, and the
order they run in, are the same; what changed is that they are one script now,
in four parts, run by one command.

The steps, in the order they have to run:

  check     the projection this file measures in, MTM zone 8 (EPSG:32188),
            held against the city's own two copies of its 34 outlines, point
            for point, in both directions. Nothing in site/data is read or
            written: if this fails, every distance below would be the wrong
            distance.
  geojson   step 2. The metro stops out of the STM's GTFS zip, the city's
            green spaces, and the street corners the map is drawn from -
            three files: site/data/metro_stops.geojson, city_parks.geojson
            and intersections.geojson.
  walks     step 3. Step 2's corners, each one given the 22 walks the page's
            two questions can ask for, and the 34 outlines the page's mask is
            cut round: site/data/intersections.geojson (rewritten in place)
            and site/data/boroughs.geojson.

Run it from the project folder:

    python site/build.py            # all three, in the order above
    python site/build.py check      # only the projection self-check
    python site/build.py geojson    # only step 2
    python site/build.py walks      # only step 3

The steps always run in the order above, whatever order they are named in,
and naming one runs only that one. After changing a step 3 knob - a walking
speed, a park band, a size - `python site/build.py walks` is enough, because
step 2's files have not changed. After changing a step 2 knob, step 3 has to
run again: a corner step 2 has just rewritten carries no walks on it at all.

Every file is written to a temporary name, read back off the disk, checked as
it was written, and only then moved into place, so a run that fails leaves the
page with the files it already had. Nothing is fetched over the network and
nothing outside site/data is written.

What is where:

  Part 1  degrees, metres, streets, and walking
          the projection (MTM zone 8), the flat geometry the snapping and the
          checks use, the street graph built out of the city's troncons, the
          walk over it, and the self-check on the projection.
  Part 2  step 2, the city's files into GeoJSON
          the metro stops, the green spaces, the street corners, the checks
          on each of the three, and the writer both steps share.
  Part 3  step 3, the walk from every corner
          the readers, the 22 layers, the measurements and the checks on
          them, and the two files this step writes.
  Part 4  running the build
          the command above, and nothing else.

The reasoning - which layers exist and why, what a corner's numbers mean,
what the page does and does not know - is in the README, once, for the whole
project. What is here is the code and, beside it, the comment for each
decision that the code does not show by itself.
"""


import argparse
import collections
import csv
import heapq
import io
import json
import math
import re
import time
import zipfile
from array import array
from pathlib import Path


# --- Paths and files ------------------------------------------------------
#
# Paths are worked out from this file's own location, so it does not matter
# which folder the build is started from. The inputs sit in the project's data/
# folder and are only ever read; everything written lands in site/data/, beside
# the page that loads it.

SITE_DIR = Path(__file__).resolve().parent          # .../project/site
PROJECT_DIR = SITE_DIR.parent                       # .../project

# The three files step 2 reads.
METRO_ZIP = PROJECT_DIR / 'data' / 'metro_stops' / 'gtfs_stm.zip'
PARKS_JSON = PROJECT_DIR / 'data' / 'city_parks' / 'espace_vert.json'
STREETS_JSON = PROJECT_DIR / 'data' / 'street_network' / 'geobase.json'

# The four files the page loads. Step 2 writes the first three; step 3 reads
# them back and rewrites the intersections file with its walks on every corner.
METRO_GEOJSON = SITE_DIR / 'data' / 'metro_stops.geojson'
PARKS_GEOJSON = SITE_DIR / 'data' / 'city_parks.geojson'
INTERSECTIONS_GEOJSON = SITE_DIR / 'data' / 'intersections.geojson'
BOROUGHS_GEOJSON = SITE_DIR / 'data' / 'boroughs.geojson'

# The city's 34 outlines, published twice: in degrees, which is what step 3
# reads and the page's mask is cut round, and in MTM zone 8 metres, which is
# what the self-check in Part 1 holds this file's projection against.
OUTLINES_JSON = (PROJECT_DIR / 'data' / 'neighbourhoods'
                 / 'limites-administratives-agglomeration.geojson.json')
BOUNDARIES_MTM8 = (PROJECT_DIR / 'data' / 'neighbourhoods'
                   / 'limites-administratives-agglomeration-nad83.geojson')


# ======== Part 1: degrees, metres, streets, and walking ===================
#
# Everything the other parts measure with: the projection into metres, the
# flat geometry the snapping and the checks use, the street graph built out
# of the city's troncons, the walk over it, and the self-check that holds
# the projection against the city's own metres.


# --- The knobs: the projection, the streets, the walk ---------------------

# Two line ends closer together than this are the same corner.
SNAP_METRES = 0.1

# The city's CLASSE column, filtered to the classes a pedestrian may walk:
# class 8 is the motorways, the expressway in its trench (the file calls it
# Route 136), the bridges a motorway crosses on, and the unnamed service ways
# beside them. Every other class - rues, avenues, boulevards, the bridges that
# carry people - is kept as it stands. The corner file and the walks are both
# filtered on this one tuple, so they agree about what a street is.
UNWALKABLE_CLASSES = ('8',)

# A line end this close to the side of another line is taken to join it: the
# corner is moved onto that line and the line is split there.
CROSSING_METRES = 0.3

# Walking speed on the flat, in metres per second: 1.35 m/s is 4.9 km/h, the
# usual planning figure for an average pedestrian. Every time on the page is
# this figure applied to a distance measured here, and the page repeats the
# number so that minutes can be read back as metres.
WALK_SPEED_MPS = 1.35

# Side of the little squares the lookups use to skip most of the work. It is a
# lookup size, not a measurement: nothing measured is ever rounded to it.
CELL_METRES = 25.0


# --- Degrees to metres: MTM zone 8 (EPSG:32188) ---------------------------

# Every distance measured here is metres on the ground. The city's files are in
# longitudes and latitudes instead, where a centimetre is "0.0000002 degrees"
# and a degree is a different length east-west than it is north-south, so the
# first thing anything flat needs is this conversion.
#
# Montreal sits in Quebec's MTM zone 8, a Transverse Mercator projection, and
# the parameters below are copied from the city's own file:
# data/street_network/geobase.zip holds geobase_mtl.prj, which reads
#
#     PROJCS["NAD_1983_MTM_8", ... SPHEROID["GRS_1980",6378137.0,298.257222101]
#     ... PARAMETER["False_Easting",304800.0] PARAMETER["False_Northing",0.0]
#     PARAMETER["Central_Meridian",-73.5] PARAMETER["Scale_Factor",0.9999]
#     PARAMETER["Latitude_Of_Origin",0.0] ... UNIT["Meter",1.0]]
#
# That is EPSG code 32188. Python's own library is all that is used: no pyproj,
# no geopandas, nothing to install. The formulas are the standard Transverse
# Mercator series (Snyder, "Map Projections - A Working Manual", USGS 1987,
# pages 61-64), which are accurate to well under a millimetre this close to the
# central meridian, and Montreal is at most 1.75 degrees from it.
#
# to_wgs84 is the way back, and it is here for one reason: it lets this
# projection be held against the city's own metres, which the bottom of this
# file does. Nothing in the build walks on it.

# GRS 1980: the shape of the earth the city's file uses, as its two numbers.
ELLIPSOID_A = 6378137.0                 # semi-major axis, in metres
ELLIPSOID_INV_F = 298.257222101         # inverse flattening

# The rest of the Transverse Mercator recipe, from the same .prj file. Zone 8's
# central meridian is 73.5 degrees west; the false easting puts that meridian
# at 304800 metres, so no coordinate in the province comes out negative.
CENTRAL_MERIDIAN = math.radians(-73.5)
SCALE_FACTOR = 0.9999
FALSE_EASTING = 304800.0
FALSE_NORTHING = 0.0

# Derived once from the two ellipsoid numbers: the first and second
# eccentricity squared, which every formula below needs.
_FLATTENING = 1.0 / ELLIPSOID_INV_F
ECCENTRICITY2 = _FLATTENING * (2.0 - _FLATTENING)
_ECCENTRICITY_PRIME2 = ECCENTRICITY2 / (1.0 - ECCENTRICITY2)


def _meridian_arc(phi):
    '''How far along the meridian, in metres, a latitude sits from the equator.

    This is the M of the series: an elliptic integral the series expands into
    sines of 2, 4 and 6 times the latitude.
    '''
    e2 = ECCENTRICITY2
    e4 = e2 * e2
    e6 = e4 * e2
    return ELLIPSOID_A * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
        - (35 * e6 / 3072) * math.sin(6 * phi)
    )


def to_metres(lon, lat):
    '''Longitude and latitude in degrees -> easting and northing in metres.'''
    phi = math.radians(lat)
    lam = math.radians(lon)

    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    tan_phi = math.tan(phi)

    n = ELLIPSOID_A / math.sqrt(1 - ECCENTRICITY2 * sin_phi * sin_phi)
    t = tan_phi * tan_phi
    c = _ECCENTRICITY_PRIME2 * cos_phi * cos_phi
    a = (lam - CENTRAL_MERIDIAN) * cos_phi
    m = _meridian_arc(phi)

    a2 = a * a
    a3 = a2 * a
    a4 = a2 * a2
    a5 = a4 * a
    a6 = a3 * a3

    easting = FALSE_EASTING + SCALE_FACTOR * n * (
        a
        + (1 - t + c) * a3 / 6
        + (5 - 18 * t + t * t + 72 * c - 58 * _ECCENTRICITY_PRIME2) * a5 / 120
    )
    northing = FALSE_NORTHING + SCALE_FACTOR * (
        m + n * tan_phi * (
            a2 / 2
            + (5 - t + 9 * c + 4 * c * c) * a4 / 24
            + (61 - 58 * t + t * t + 600 * c - 330 * _ECCENTRICITY_PRIME2) * a6 / 720
        )
    )
    return easting, northing


def to_wgs84(easting, northing):
    '''Easting and northing in metres -> longitude and latitude in degrees.

    The way back needs the footpoint latitude first: the latitude whose
    meridian arc is the northing we were given.
    '''
    m = (northing - FALSE_NORTHING) / SCALE_FACTOR
    mu = m / (ELLIPSOID_A * (1 - ECCENTRICITY2 / 4
                             - 3 * ECCENTRICITY2 ** 2 / 64
                             - 5 * ECCENTRICITY2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - ECCENTRICITY2)) / (1 + math.sqrt(1 - ECCENTRICITY2))

    phi1 = (mu
            + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))

    sin_phi1 = math.sin(phi1)
    cos_phi1 = math.cos(phi1)
    tan_phi1 = math.tan(phi1)

    c1 = _ECCENTRICITY_PRIME2 * cos_phi1 * cos_phi1
    t1 = tan_phi1 * tan_phi1
    n1 = ELLIPSOID_A / math.sqrt(1 - ECCENTRICITY2 * sin_phi1 * sin_phi1)
    r1 = (ELLIPSOID_A * (1 - ECCENTRICITY2)
          / (1 - ECCENTRICITY2 * sin_phi1 * sin_phi1) ** 1.5)
    d = (easting - FALSE_EASTING) / (n1 * SCALE_FACTOR)

    d2 = d * d
    d3 = d2 * d
    d4 = d2 * d2
    d5 = d4 * d
    d6 = d3 * d3

    phi = phi1 - (n1 * tan_phi1 / r1) * (
        d2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * _ECCENTRICITY_PRIME2) * d4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * _ECCENTRICITY_PRIME2
           - 3 * c1 * c1) * d6 / 720
    )
    lam = CENTRAL_MERIDIAN + (
        d
        - (1 + 2 * t1 + c1) * d3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * _ECCENTRICITY_PRIME2
           + 24 * t1 * t1) * d5 / 120
    ) / cos_phi1

    return math.degrees(lam), math.degrees(phi)


# --- Flat geometry --------------------------------------------------------

def point_segment_distance(point, start, end):
    '''How far a point is from a segment, and where along it it lands.

    The answer is (distance in metres, position 0..1 along start->end), the
    position being a clamped foot of the perpendicular.
    '''
    px, py = point
    ax, ay = start
    bx, by = end
    dx = bx - ax
    dy = by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0.0:
        return math.hypot(px - ax, py - ay), 0.0

    t = ((px - ax) * dx + (py - ay) * dy) / length2
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t


def point_in_ring(point, ring):
    '''True when the point is inside the ring, by counting side crossings.

    A ray is sent to the right and the sides it crosses are counted; an odd
    count means inside. Only sides that straddle the ray's height count, and a
    side that ends exactly at that height counts once rather than twice, which
    is what the half-open comparison below does.
    '''
    px, py = point
    inside = False
    x0, y0 = ring[-1]
    for x1, y1 in ring:
        if (y1 > py) != (y0 > py):
            cross_x = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
            if px < cross_x:
                inside = not inside
        x0, y0 = x1, y1
    return inside


def point_in_rings(point, rings):
    '''True when the point is inside an odd number of the given rings.

    A shape is one or more polygons, and those can have holes in them. This
    treats every ring the same way: an outer ring and a hole inside it cancel
    out, which is what a point must do to be inside the shape.
    '''
    return sum(1 for ring in rings if point_in_ring(point, ring)) % 2 == 1


def ring_bounds(ring):
    '''The smallest box around a ring: (min x, min y, max x, max y).'''
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def bounds_of_rings(rings):
    '''The box that holds every ring in a list, as (west, south, east, north).'''
    boxes = [ring_bounds(ring) for ring in rings]
    return (min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes))


def point_box_distance(point, bounds):
    '''How far a point is from a box, or zero when the point is inside it.'''
    px, py = point
    west, south, east, north = bounds
    dx = max(west - px, px - east, 0.0)
    dy = max(south - py, py - north, 0.0)
    return math.hypot(dx, dy)


def point_ring_distance(point, ring):
    '''How far a point is on the map from the edge of a ring of points.

    Zero when the point is inside the ring. Otherwise every side of the ring is
    walked and the nearest one wins, so a park drawn with thousands of points
    along a river costs that many steps. This is only ever used on the rings of
    the one space a check is about, never once per square of the map.
    '''
    if point_in_ring(point, ring):
        return 0.0
    best = float('inf')
    for index, start in enumerate(ring):
        distance, _ = point_segment_distance(point, start,
                                             ring[(index + 1) % len(ring)])
        if distance < best:
            best = distance
    return best


# --- Building the graph out of the troncons -------------------------------

def merge_endpoints(polylines, snap=SNAP_METRES):
    '''Give every shared corner one number.

    Returns (nodes, edges): nodes is a list of (x, y) metre points, and edges
    is a list of (start node, end node, polyline number, points the same way
    round). The polylines handed in are lists of points, one per troncon, in
    metres; the last flag records whether a link runs the same way round as the
    troncon it came from, which the build uses to read a troncon's columns back
    off the links that replaced it.

    The lookup is by square, so a corner is only compared with the handful of
    corners in its own square and the eight around it rather than with all of
    them.
    '''
    nodes = []
    squares = {}
    edges = []

    def corner(point):
        square = (math.floor(point[0] / CELL_METRES),
                  math.floor(point[1] / CELL_METRES))
        for cx in (square[0] - 1, square[0], square[0] + 1):
            for cy in (square[1] - 1, square[1], square[1] + 1):
                for index in squares.get((cx, cy), ()):
                    x, y = nodes[index]
                    if abs(x - point[0]) <= snap and abs(y - point[1]) <= snap:
                        return index
        index = len(nodes)
        nodes.append(point)
        squares.setdefault(square, []).append(index)
        return index

    for number, line in enumerate(polylines):
        previous = None
        for point in line:
            node = corner(point)
            if previous is not None and node != previous:
                edges.append((previous, node, number, True))
            previous = node

    return nodes, edges


def _edge_squares(nodes, edges):
    '''Which square each link is in, for the lookups that follow.

    A link is filed under every square its box touches, so a point on the edge
    of one square still finds the link that ends just across the line.
    '''
    squares = {}
    for index, (u, v, _, _) in enumerate(edges):
        ux, uy = nodes[u]
        vx, vy = nodes[v]
        for cx in range(math.floor(min(ux, vx) / CELL_METRES),
                        math.floor(max(ux, vx) / CELL_METRES) + 1):
            for cy in range(math.floor(min(uy, vy) / CELL_METRES),
                            math.floor(max(uy, vy) / CELL_METRES) + 1):
                squares.setdefault((cx, cy), set()).add(index)
    return squares


def _squares_around(point, rings=1):
    '''The square a point is in, and the rings of squares around it.'''
    cx = math.floor(point[0] / CELL_METRES)
    cy = math.floor(point[1] / CELL_METRES)
    return [(cx + dx, cy + dy)
            for dx in range(-rings, rings + 1)
            for dy in range(-rings, rings + 1)]


def segment_crossing(a, b, c, d):
    '''Where two segments cross, as (point, along a-b, along c-d), or None.

    Neither end of either segment counts: a crossing is strictly inside both.
    '''
    ax, ay = a
    bx, by = b
    rx = bx - ax
    ry = by - ay
    sx = d[0] - c[0]
    sy = d[1] - c[1]
    denominator = rx * sy - ry * sx
    if denominator == 0.0:
        return None
    t = ((c[0] - ax) * sy - (c[1] - ay) * sx) / denominator
    u = ((c[0] - ax) * ry - (c[1] - ay) * rx) / denominator
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (ax + t * rx, ay + t * ry), t, u
    return None


def _split_links(nodes, edges, wanted):
    '''Rebuild the links that have picked up new corners.

    wanted maps a link number to the corners now on it, as (position, corner),
    and each link is replaced by the chain of links between those corners. The
    source troncon and its direction are carried over, so a link can still be
    read back to the troncon it came from afterwards.
    '''
    rebuilt = 0
    kept = []
    for index, (u, v, number, forward) in enumerate(edges):
        cuts = wanted.get(index)
        if not cuts:
            kept.append((u, v, number, forward))
            continue
        chain = [u] + [node for _, node in sorted(cuts)] + [v]
        for i in range(len(chain) - 1):
            if chain[i] != chain[i + 1]:
                kept.append((chain[i], chain[i + 1], number, forward))
        rebuilt += 1
    edges[:] = kept
    return rebuilt


def split_crossings(nodes, edges, snap=CROSSING_METRES):
    '''Join the troncon ends that land on the side of another troncon.

    A street that ends on another street should meet it, but the file can
    leave the end a few centimetres short of, or past, the line it meets, and
    without this the two streets never join and a walk has to go the long way
    round. The corner is moved onto the line and the line is split at it. Only
    corners that land on the inside of a link count, and a link is not split
    within `snap` of its own ends, so a link is never cut into two pieces a
    centimetre long.

    Returns (links that were split, links rebuilt).
    '''
    squares = _edge_squares(nodes, edges)
    wanted = {}
    moved = {}

    for node, point in enumerate(nodes):
        found = None
        for square in _squares_around(point):
            for index in squares.get(square, ()):
                u, v, _, _ = edges[index]
                if node == u or node == v:
                    continue
                start = nodes[u]
                end = nodes[v]
                distance, t = point_segment_distance(point, start, end)
                if distance > snap:
                    continue
                length = math.hypot(end[0] - start[0], end[1] - start[1])
                if t * length <= snap or (1.0 - t) * length <= snap:
                    continue
                wanted.setdefault(index, []).append((t, node))
                foot = (start[0] + t * (end[0] - start[0]),
                        start[1] + t * (end[1] - start[1]))
                if found is None or distance < found[0]:
                    found = (distance, foot, node)
        if found is not None:
            moved[found[2]] = found[1]

    if not wanted:
        return 0, 0

    for node, point in moved.items():
        nodes[node] = point

    rebuilt = _split_links(nodes, edges, wanted)
    return len(wanted), rebuilt


def join_crossing_links(nodes, edges, snap=0.01):
    '''Join two troncons that cross in mid air with no corner on either.

    The file leaves the odd junction unsplit, and it also holds a few streets
    twice, slightly out of line with each other, so the two copies cross. In
    both cases two lines cross where there is no corner, and a walk cannot be
    turned there: it has to go round a corner that is not on its way. So both
    lines are cut at the crossing point and a corner is put there where the two
    now meet.

    The price is that a real viaduct, one street passing over another, is
    joined as though the two met, and the join offers a pedestrian a way the map
    does not have: a route through such a spot comes out a few metres shorter
    than the ground really covered, and never longer. That is rare (the build
    reports how rare), and the crow-flies check in the build is what catches a
    walk shortened by more than the seconds it is written in and the file's
    rounding allow.

    Returns the number of crossings joined.
    '''
    squares = _edge_squares(nodes, edges)
    wanted = {}
    corners = {}
    pairs = set()
    joined = 0

    for listed in squares.values():
        if len(listed) < 2:
            continue
        ordered = sorted(listed)
        for i in range(len(ordered) - 1):
            for j in range(i + 1, len(ordered)):
                a = ordered[i]
                b = ordered[j]
                if (a, b) in pairs:
                    continue
                pairs.add((a, b))
                au, av, _, _ = edges[a]
                bu, bv, _, _ = edges[b]
                if au == bu or au == bv or av == bu or av == bv:
                    continue
                met = segment_crossing(nodes[au], nodes[av],
                                       nodes[bu], nodes[bv])
                if met is None:
                    continue
                point, along_a, along_b = met
                key = (round(point[0] / snap), round(point[1] / snap))
                corner = corners.get(key)
                if corner is None:
                    corner = len(nodes)
                    nodes.append((key[0] * snap, key[1] * snap))
                    corners[key] = corner
                wanted.setdefault(a, []).append((along_a, corner))
                wanted.setdefault(b, []).append((along_b, corner))
                joined += 1

    if not wanted:
        return 0
    _split_links(nodes, edges, wanted)
    return joined


def drop_duplicate_edges(edges):
    '''Keep one copy of each link, and drop links from a corner to itself.

    The city's file holds a good number of troncons twice - once per side of
    the street, or once per direction. For walking they are the same pavement,
    and a link counted twice would count its length twice as well, so the
    second copy goes. Returns (links kept, copies dropped, stubs dropped).
    '''
    seen = set()
    kept = []
    copies = 0
    stubs = 0
    for u, v, number, forward in edges:
        if u == v:
            stubs += 1
            continue
        key = (u, v) if u < v else (v, u)
        if key in seen:
            copies += 1
            continue
        seen.add(key)
        kept.append((u, v, number, forward))
    return kept, copies, stubs


def edge_length(nodes, edge):
    '''How long a link is on the map, in metres.'''
    ux, uy = nodes[edge[0]]
    vx, vy = nodes[edge[1]]
    return math.hypot(vx - ux, vy - uy)


def count_components(nodes, edges):
    '''How many separate pieces of network there are, and which corner is in which.

    A piece of network is a set of corners joined by links, so a walk can only
    ever reach what its own piece reaches. Two corners in the same borough can
    sit in different pieces - the island and a piece the file draws with no
    junction to it - and then one of them is a short walk from a metro entrance
    and the other is not on the network at all.

    Returns (pieces, piece of each corner, corners with no link at all).
    '''
    neighbours = [[] for _ in nodes]
    for u, v, _, _ in edges:
        neighbours[u].append(v)
        neighbours[v].append(u)

    piece = [-1] * len(nodes)
    lonely = [node for node, near in enumerate(neighbours) if not near]
    pieces = 0
    for start in range(len(nodes)):
        if piece[start] != -1 or not neighbours[start]:
            continue
        piece[start] = pieces
        stack = [start]
        while stack:
            node = stack.pop()
            for near in neighbours[node]:
                if piece[near] == -1:
                    piece[near] = pieces
                    stack.append(near)
        pieces += 1
    return pieces, piece, lonely


def build_adjacency(nodes, edges):
    '''For each corner, the links that leave it: (other end, link, length).'''
    adjacency = [[] for _ in nodes]
    for index, (u, v, _, _) in enumerate(edges):
        length = edge_length(nodes, edges[index])
        adjacency[u].append((v, index, length))
        adjacency[v].append((u, index, length))
    return adjacency


# --- Putting the destinations onto the network ----------------------------

def attach_points(nodes, edges, points, reach=40.0):
    '''Put a corner of the network at each of a set of points.

    Metro entrances and green-space gateways have to be somewhere on the
    network before a walk can end at them, so each point is pulled onto the
    nearest link within `reach` metres and that link is split at it. The point
    itself moves onto the link, which is right for both uses: a station
    entrance is reached from the pavement in front of it, and a green space is
    entered from the street drawn along its edge.

    points is a list of (label, point), and several points may share a label:
    a destination with more than one way in - a green space ringed by streets,
    a station with four entrances - arrives as several points under the one
    label and ends up with more than one corner a walk can finish at.

    Returns (a corner for each point in order, how far that point is from its
    corner, and the labels of the points no link was found for). The distance
    matters as much as the corner: it is the last little stretch of the walk -
    from the pavement to the door, or from the street to the park's edge - and
    the build hands it to the search as the distance already covered, so a walk
    is never shorter than the straight line it is a chain of.

    A point that no link comes within `reach` of gets None in place of a
    corner, so the corners always line up with the points handed in and a
    caller can read back which corner belongs to which point. A point lands on
    a corner of its link instead when it falls within half a metre of one, so
    the network does not pick up specks of link.
    '''
    squares = _edge_squares(nodes, edges)
    rings = int(math.ceil(reach / CELL_METRES))
    wanted = {}
    made = {}
    corners = []
    approaches = []
    missed = []

    for label, point in points:
        best = None
        for square in _squares_around(point, rings):
            for index in squares.get(square, ()):
                u, v, _, _ = edges[index]
                start = nodes[u]
                end = nodes[v]
                distance, t = point_segment_distance(point, start, end)
                if best is None or distance < best[0]:
                    best = (distance, index, t, start, end)
        if best is None or best[0] > reach:
            missed.append(label)
            corners.append(None)
            approaches.append(None)
            continue

        _, index, t, start, end = best
        u, v, _, _ = edges[index]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        along = t * length
        if along <= 0.5:
            corner = u
        elif length - along <= 0.5:
            corner = v
        else:
            spot = (index, round(along * 100.0))
            corner = made.get(spot)
            if corner is None:
                corner = len(nodes)
                nodes.append((start[0] + t * (end[0] - start[0]),
                              start[1] + t * (end[1] - start[1])))
                made[spot] = corner
                wanted.setdefault(index, []).append((t, corner))

        corners.append(corner)
        approaches.append(math.hypot(point[0] - nodes[corner][0],
                                     point[1] - nodes[corner][1]))

    _split_links(nodes, edges, wanted)
    return corners, approaches, missed


def walk_metres(adjacency, sources):
    '''How far every corner is, on foot, from the nearest of a set of corners.

    sources maps a corner to the name of what is reached from it and the metres
    already walked to get there: nought where a corner is the destination
    itself, and otherwise the short line from the corner to the destination's
    own door or edge. One Dijkstra search spreads out from all of them at once,
    so each corner ends up with the distance to the nearest destination and the
    name of that destination.

    A corner is one key, so it can only hold one name: where two destinations
    both arrive at the same corner, the caller decides which one that corner is
    listed under. The build lists the spaces in the order the city's file puts
    them, biggest first, so a corner shared by two of them is named after the
    bigger one - which is the one a person walking there was most likely
    heading for.

    Returns (metres to each corner, name reached at each corner). Corners in a
    separate piece of network are simply not in the answers.
    '''
    metres = {}
    reached = {}
    queue = []
    for corner, (name, already) in sources.items():
        metres[corner] = already
        reached[corner] = name
        queue.append((already, corner))
    heapq.heapify(queue)

    while queue:
        distance, corner = heapq.heappop(queue)
        if distance > metres[corner]:
            continue
        for other, _, length in adjacency[corner]:
            step = distance + length
            if step < metres.get(other, float('inf')):
                metres[other] = step
                reached[other] = reached[corner]
                heapq.heappush(queue, (step, other))
    return metres, reached


def sample_ring(ring, step):
    '''Points along a ring, no two of them closer than step metres.

    The ring is walked segment by segment with a point laid down every step
    metres measured along the ring, not from each corner, so the points do not
    bunch up wherever the edge is irregular. A closed ring, whose last point
    simply repeats its first, comes out with nothing on its last segment.
    '''
    points = []
    carried = 0.0
    for number, start in enumerate(ring):
        end = ring[(number + 1) % len(ring)]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length == 0.0:
            continue
        along = step - carried
        while along < length:
            share = along / length
            points.append((start[0] + share * (end[0] - start[0]),
                           start[1] + share * (end[1] - start[1])))
            along += step
        carried = (carried + length) % step
    return points


# --- The self-test: this file's projection against the city's own ----------
#
# A metre is only worth something if it matches the city's. The city publishes
# the same 34 outlines twice, once in degrees and once in MTM zone 8 metres, so
# each outline can be projected here and compared with its own twin, point for
# point, in both directions. Neither copy is this file's, so agreement to a few
# centimetres is the projection being right, not the two agreeing by
# construction. It is also the one check that reads no built file and writes
# nothing, which is why it can run before anything else does.


def _rings(geometry):
    '''Every ring of a polygon or multipolygon, as lists of points.'''
    if geometry['type'] == 'Polygon':
        return geometry['coordinates']
    return [ring for polygon in geometry['coordinates'] for ring in polygon]


def check_against_city_files():
    '''Project the city's outlines and compare them with the city's own metres.

    The two files hold the same 34 outlines, point for point, one in degrees
    and one in metres. Matching them by their CODEID and walking both at once
    is the strongest available test: two independent files, one centre, and
    neither is ours.
    '''
    with open(OUTLINES_JSON, encoding='utf-8') as f:
        degrees = {feat['properties']['CODEID']: feat for feat in json.load(f)['features']}
    with open(BOUNDARIES_MTM8, encoding='utf-8') as f:
        metres = {feat['properties']['CODEID']: feat for feat in json.load(f)['features']}

    assert set(degrees) == set(metres), 'the two files cover different areas'

    forward_worst = 0.0
    inverse_worst = 0.0
    vertex_count = 0
    for code, feat in degrees.items():
        got_rings = _rings(feat['geometry'])
        want_rings = _rings(metres[code]['geometry'])
        assert len(got_rings) == len(want_rings), 'a different number of rings'

        for got, want in zip(got_rings, want_rings):
            assert len(got) == len(want), 'a different number of points'
            for (lon, lat), (x, y) in zip(got, want):
                ex, ey = to_metres(lon, lat)
                forward_worst = max(forward_worst, math.hypot(ex - x, ey - y))

                back_lon, back_lat = to_wgs84(x, y)
                inverse_worst = max(inverse_worst,
                                    math.hypot(back_lon - lon, back_lat - lat))

                vertex_count += 1

    # Degrees are unreadable as an error, so the worst one is also given in
    # metres, at the latitude of the city.
    metres_per_degree = 111320.0 * math.cos(math.radians(45.52))

    print(f'{vertex_count} outline points compared, in both directions')
    print(f'  degrees -> metres, worst point : {forward_worst:.4f} m')
    print(f'  metres -> degrees, worst point : {inverse_worst * metres_per_degree:.4f} m'
          f' ({inverse_worst:.3e} degrees)')

    # A projection out by half a metre would still look right on a map of the
    # city but would quietly move every distance, so the bar is centimetres.
    assert forward_worst < 0.05, 'the forward projection disagrees with the city'
    assert inverse_worst * metres_per_degree < 0.05, 'the inverse projection disagrees'
    print('check passed: this file and the city agree to a few centimetres')


# ======== Part 2: step 2, the city's files into GeoJSON ===================
#
# The metro stops, the green spaces, and the street corners the map is
# drawn from. Each file is written to a temporary name, read back, checked,
# and only then moved into place.


# --- The metro stops: the words the page shows ----------------------------

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
# step 3 uses (Part 1's UNWALKABLE_CLASSES). The motorways and the service
# ways beside them go first, before anything else is done, because nobody walks
# there. So no point in this file is an intersection a person cannot stand on,
# and the points and the walks measured from them agree about what a street is.

# The file draws one corner once for every street that touches it, a
# centimetre or two apart. Two ends this close together are the same corner,
# and the first one read decides where that corner sits, right down to the
# degrees the file wrote it in. SNAP_METRES is Part 1's.

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

# The city's own placeholder for a way it has not named, which is 1079 of the
# troncons a pedestrian can walk. It is not a name, so it is kept out of a
# corner's title: "Rue Sherbrooke Est / Voie Non-nommee" names one street and
# then says the other has no name, which the one name already says. A corner
# with no named street at all falls back to UNNAMED_WAY_TITLE, worded here the
# way the green spaces word a space the city has not named.
UNNAMED_WAY = 'Voie Non-nomm\u00e9e'
UNNAMED_WAY_TITLE = 'Unnamed way'


# --- Degrees to metres -----------------------------------------------------
#
# The distances here are metres on the ground and the file's are in degrees, so
# the two are bridged by to_metres in Part 1: MTM zone 8, EPSG code 32188,
# worked out from the city's own .prj file. The one copy of that maths in the
# repo is there, and Part 1's self-check is what holds it against the city's
# own metres.


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

def check_intersections(corners, graph, rows, out_path):
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
    assert graph == {'corners': 216183, 'links': 232080}, graph

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


def build_geojson():
    '''Read the city's three files in, write the three the page loads.'''
    # Metro stops: the points are the stations and their entrances; the
    # platform rows are read for each station's name and STM page and dropped.
    rows, platforms = read_metro_rows()
    metro_features, skipped = build_metro_features(rows)
    add_metro_popup_fields(metro_features, platforms)
    write_checked(metro_features, METRO_GEOJSON, indent=2,
                  check=lambda path: check_metro(rows, skipped, path))

    # Green spaces.
    source = read_parks()
    park_features = build_park_features(source)
    add_park_popup_fields(park_features)
    sort_park_features(park_features)
    write_checked(park_features, PARKS_GEOJSON, indent=None,
                  check=lambda path: check_parks(source, path))

    # Street intersections: one point per place three or more street ends meet.
    lines = read_streets()
    corners, graph = build_corners(lines)
    sort_corners(corners)
    features = build_intersection_features(corners, lines)
    add_intersection_popup_fields(features, corners, lines)
    write_checked(features, INTERSECTIONS_GEOJSON, indent=None,
                  check=lambda path: check_intersections(corners, graph, rows, path))


# ======== Part 3: step 3, the walk from every corner ======================
#
# Step 2's corners, each one given the 22 walks the page can ask for, and
# the 34 outlines the page's mask is cut round.


# --- The knobs: what step 3 asks about ------------------------------------

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
# keys are the ones step 2 wrote into the parks file from TYPO1: a
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
    with open(INTERSECTIONS_GEOJSON, encoding='utf-8') as f:
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
            'id': len(dots), 'point': to_metres(lon, lat),
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
                'point': to_metres(lon, lat),
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
    first - step 2 sorts it that way - and that order is kept, because
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
    return [[to_metres(lon, lat) for lon, lat in polygon[0]]
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
        polygons = [[[to_metres(lon, lat) for lon, lat in ring]
                     for ring in polygon] for polygon in degrees]
        rings = [ring for polygon in polygons for ring in polygon]
        outlines.append({
            'name': feature['properties']['NOM'],
            'type': feature['properties']['TYPE'],
            'rings': rings,
            'bounds': bounds_of_rings(rings),
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
        if point_in_rings(point, outline['rings']):
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
            if point_in_rings(dot['point'], outline['rings']):
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

    The four steps are the ones Part 1 sets out: snap the troncons that
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
    nodes, edges = merge_endpoints(lines)
    first = list(nodes)
    report = {'corners': len(nodes), 'links': len(edges)}
    print(f"{report['corners']} corners and {report['links']} links after "
          'snapping the troncons that share a corner onto it')

    report['split'], report['rebuilt'] = split_crossings(nodes, edges)
    report['crossings'] = join_crossing_links(nodes, edges)
    edges, report['copies'], report['stubs'] = drop_duplicate_edges(edges)
    report['links'] = len(edges)

    pieces, _, lonely = count_components(nodes, edges)
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
    return (math.floor(point[0] / CELL_METRES),
            math.floor(point[1] / CELL_METRES))


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
            for point in sample_ring(ring, GATEWAY_METRES):
                points.append((space['label'], point))

    corners, approaches, missed = attach_points(nodes, edges, points,
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
    speed = WALK_SPEED_MPS
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
        if point_box_distance(point, ring_bounds(ring)) >= best:
            continue
        best = min(best, point_ring_distance(point, ring))
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
    speed = WALK_SPEED_MPS
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

    write_checked does the work: it writes to a temporary name,
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

    write_checked(borough_features(outlines), BOROUGHS_GEOJSON,
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
        'walk_speed_mps': WALK_SPEED_MPS,
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
    temp = INTERSECTIONS_GEOJSON.with_name(INTERSECTIONS_GEOJSON.name + '.new')
    text = json.dumps(collection, ensure_ascii=False, separators=(',', ':'))
    temp.write_text(text, encoding='utf-8')
    with open(temp, encoding='utf-8') as f:
        check_walk_file(collection, json.load(f))
    temp.replace(INTERSECTIONS_GEOJSON)
    print(f'{INTERSECTIONS_GEOJSON.name} written '
          f'({INTERSECTIONS_GEOJSON.stat().st_size / 1048576.0:.1f} MB), '
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


# --- Putting step 3 together ----------------------------------------------

def build_walk_times():
    '''Read the five files, measure every layer, check them, write them out.'''
    started = time.perf_counter()

    dots, corner_features = read_dots()
    # Only the metres are wanted here, but they come through step 2's reader,
    # so that this step walks the same troncons, in the same order, with the
    # same road classes dropped as the corners were numbered from. One reader,
    # so the two steps cannot disagree about what a street is.
    lines = [line['metres'] for line in read_streets()]
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
    adjacency = build_adjacency(nodes, edges)

    plan = check_plan(plan_layers())
    by_id = {layer['id']: layer for layer in plan}
    layers = {}

    # The fourteen layers that need a search of their own: one Dijkstra per
    # layer, spreading out from every corner its destinations arrive at.
    for layer in plan:
        if layer['parents']:
            continue
        sources = layer_sources(layer, spaces, entrances, corners_of)
        metres, reached = walk_metres(adjacency, sources)
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


# ======== Part 4: running the build =======================================
#
# The one command, and the order it runs the steps in. Each step is a function
# above that does its own work and its own checking; nothing is shared between
# them here except the order.

STEPS = ('check', 'geojson', 'walks')


def main():
    """Run the steps asked for, in the order they have to run in."""
    parser = argparse.ArgumentParser(
        prog='build.py',
        description='Build the four GeoJSON files the page loads.')
    parser.add_argument(
        'steps', nargs='*', choices=STEPS,
        help='check: the projection self-check. geojson: step 2. '
             'walks: step 3. Default: all three, in the order above.')
    # No step named means all of them; naming one runs only that one.
    chosen = parser.parse_args().steps or list(STEPS)
    started = time.perf_counter()

    if 'check' in chosen:
        check_against_city_files()
    if 'geojson' in chosen:
        build_geojson()
    if 'walks' in chosen:
        build_walk_times()

    print(f'Build done in {time.perf_counter() - started:.1f} s')


if __name__ == '__main__':
    main()
