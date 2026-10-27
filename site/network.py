'''
network.py

The street network, the projection that puts the city in metres, the flat
geometry around it, and walking over it.

Step 3 measures the whole map in 50 m squares, and every one of those
measurements is a walk: out of the square, onto the nearest street, and along
the streets to whatever that square's filter is looking for. So before anything
can be measured, the city's line work has to become a graph a person can walk,
and the graph has to be able to say how far every corner of it is from a set of
destinations.

Three things about the file make that more than reading it in.

The lines break at intersections, but the file draws the same corner once per
street that touches it, a centimetre or two apart, so the ends that are the
same corner have to be snapped together first. Two ends within SNAP_METRES are
one corner.

Some streets end on the side of another street rather than at its end, which
would leave the network with a gap where the file means a junction, and a walk
would have to go round what a person steps across. split_crossings moves those
ends onto the line they meet and cuts the line there.

And two streets sometimes cross in the middle with no corner on either, either
because the file left that junction unsplit or because it holds one street
twice, slightly out of line, so the two copies cross. join_crossing_links cuts
both lines at the point they cross and joins them there. That is the one place
in this network where a corner is made that no street end asked for, and it is
where a real viaduct, one street above another, is joined as though the two met.
No walk gets longer for it - cutting a line at a point leaves two halves that
add up to the line - but a walk through such a spot comes out a few metres
shorter than the ground a person would really cover, because the join offers a
way the map does not have. That is rare, the build counts how rare, and the
crow-flies check in the build is what catches a walk shortened by more than its
own whole seconds and the file's rounding allow.

Nothing here guesses a distance. There is no straight line anywhere inside a
walk: the only straight lines are the short ones between a point and the street
it starts from, which a person walks anyway. Straight-line distances are worked
out in the build, and for one purpose only - as a lower bound the walks have to
beat, which is a check on the walking rather than part of it.

The one search is walk_metres: a Dijkstra per set of destinations, giving every
corner its distance to the nearest of them and the name of the one reached.
Every number on the step 3 map comes out of that.

The city's files are in degrees and every distance here is metres on the ground,
so the file also holds the projection between the two, MTM zone 8 (EPSG:32188),
worked out from the city's own .prj file rather than from a library. Running
this file checks that projection against the city's own two copies of its 34
outlines, in both directions: see the bottom of the file.
'''

import heapq
import json
import math
from pathlib import Path

# --- The knobs ------------------------------------------------------------

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

# Running this file checks the projection above against the city's metres, the
# same way the walk times are checked elsewhere: both sides come from the city.
# It publishes the 34 borough and linked-city outlines twice, once in degrees
# and once in MTM zone 8 metres, and each outline is projected and compared
# with its twin, point for point, in both directions. Neither file is ours, so
# agreement to centimetres means the metres measured everywhere else are real.
#
# Run it with:  python site/network.py

PROJECT_DIR = Path(__file__).resolve().parent.parent

BOUNDARIES_WGS84 = (PROJECT_DIR / 'data' / 'neighbourhoods'
                    / 'limites-administratives-agglomeration.geojson.json')
BOUNDARIES_MTM8 = (PROJECT_DIR / 'data' / 'neighbourhoods'
                   / 'limites-administratives-agglomeration-nad83.geojson')


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
    with open(BOUNDARIES_WGS84, encoding='utf-8') as f:
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


if __name__ == '__main__':
    check_against_city_files()
