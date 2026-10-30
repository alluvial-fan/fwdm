// The page's behaviour, in one file: fetch the four data files, draw the map of
// the 19 boroughs over OpenStreetMap tiles, and answer the two filters out of
// the walk times already written beside each corner. Nothing is measured in the
// browser; the build did that, and this file only reads it.
//
// The data files are GeoJSON, a text format for map shapes that a browser can
// read as it stands, and the map is drawn with Leaflet, a small library that
// turns those files into tiles, shapes and dots.
//
// Run under node, the same file does no drawing at all: it checks the data
// instead, which is what makes `node site/app.js` a test.

let map = null;

// Make the map and put the OpenStreetMap tiles on it. With preferCanvas set,
// Leaflet draws all of the map's own shapes on a single canvas rather than as
// one element each. Zoom animation is turned off, because Leaflet scales that
// canvas while the tiles move, which lifts the grey mask off the map for the
// length of the zoom. The bounds set in start() are made solid under a drag:
// at a viscosity of 1 the drag stops at the edge instead of overshooting and
// springing back.
function buildMap() {
  map = L.map('map', {
    preferCanvas: true,
    zoomAnimation: false,
    maxBoundsViscosity: 1
  }).setView([45.5216, -73.6222], 12);

  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
}

// Fetch one of the data files and read it as JSON. A reply that is not ok stops
// here, naming the address that failed, rather than handing a page of HTML back
// as though it were shapes.
function loadGeojson(url) {
  return fetch(url)
    .then(function (response) {
      if (!response.ok) { throw new Error('HTTP ' + response.status + ' loading ' + url); }
      return response.json();
    });
}

// The park popup: the name, then type and size, a dash where the city gives no
// type, and italics for a name the city wrote itself.
function parkPopup(properties) {
  const title = escapeHtml(properties.popup_title);
  const shown = properties.popup_title_source === 'city' ? '<em>' + title + '</em>' : title;
  const kind = properties.popup_kind ? escapeHtml(properties.popup_kind) : '&mdash;';
  return '<strong>' + shown + '</strong><br>' +
    'Type: ' + kind + '<br>' +
    'Size: ' + escapeHtml(properties.popup_size);
}

// Make a value safe to put inside a popup: &, <, > and " each
// become their written-out code, so a park or street name can hold any
// character and still not break the markup round it. A ' needs nothing,
// because every attribute these values land in is written with double quotes.
function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Park polygons by the city's TYPO1 kind: four shades of green, darkest for the
// large parks, paler for pending and unclassified; each has a darker edge, and
// pending spaces are dashed.
const PARK_STYLE = {
  'grand_parc': { color: '#0B3D0F', weight: 1, fillColor: '#1B5E20', fillOpacity: 0.55 },
  'arrondissement': { color: '#1B5E20', weight: 1, fillColor: '#2E7D32', fillOpacity: 0.55 },
  'autre': { color: '#2E7D32', weight: 1, fillColor: '#66BB6A', fillOpacity: 0.55 },
  'jardin': { color: '#558B2F', weight: 1, fillColor: '#8BC34A', fillOpacity: 0.55 },
  'pending': { color: '#4CAF50', weight: 1, dashArray: '4 3', fillColor: '#A5D6A7', fillOpacity: 0.55 },
  'unknown': { color: '#81C784', weight: 1, fillColor: '#C8E6C9', fillOpacity: 0.55 }
};

// One marker per kind of metro point: stations are the big navy dots, entrances
// the small hollow grey ones, so size tells them apart as well as colour.
const METRO_STYLE = {
  '1': { radius: 9, color: '#ffffff', weight: 2, fillColor: '#1F2D5A', fillOpacity: 1 },      // Station
  '2': { radius: 4.5, color: '#4A4A4A', weight: 1.5, fillColor: '#ffffff', fillOpacity: 0.9 }  // Entrance
};

// The metro popup: the name and kind, then the accessibility line an entrance
// carries and the STM link a station point carries.
function metroPopup(properties) {
  const lines = ['<strong>' + escapeHtml(properties.popup_name) +
    ' (' + escapeHtml(properties.popup_type) + ')</strong>'];

  if (properties.popup_accessible) {
    lines.push('&#9855; ' + escapeHtml(properties.popup_accessible));
  }
  if (properties.popup_url && properties.popup_url.indexOf('http') === 0) {
    lines.push('<a href="' + escapeHtml(properties.popup_url) +
      '" target="_blank" rel="noopener">STM station page &#8599;</a>');
  }
  return lines.join('<br>');
}

// The mask: the world with the 19 drawn boroughs cut out as holes, filled in
// the grey of the map itself. It dims the linked cities' parks and metro
// points, and takes their clicks, so nothing outside the 19 opens.
const MASK_STYLE = { stroke: false, fillColor: '#DDDDDD', fillOpacity: 0.75 };

// The outlines the page draws: the 19 the build flagged from the city's TYPE
// column. The other 15 are the linked cities, which the mask covers.
function drawnOutlines(outlines) {
  return outlines.features.filter(function (feature) {
    return feature.properties.drawn === true;
  });
}

// A list of outlines as prose names them: "a, b and c".
function namesOf(features) {
  return features.map(function (feature) { return feature.properties.name; })
    .join(', ').replace(/, ([^,]*)$/, ' and $1');
}

// The world, as the mask's outer ring: one ring round the whole of the map
// Leaflet can draw, so the mask covers it at every zoom the page offers.
const WORLD_RING = [[85, -180], [85, 180], [-85, 180], [-85, -180]];

// An outline's rings as [north, east] pairs, which is the order Leaflet puts a
// point in. GeoJSON writes every point the other way round, as [east, north],
// so each pair is turned round here. Every ring is taken, so holes in a borough
// and separate pieces of one come with it.
function ringsOf(geometry) {
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates]
    : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
  return polygons.flat().map(function (ring) {
    return ring.map(function (point) { return [point[1], point[0]]; });
  });
}

// The mask from the outlines, or null and a message when none is flagged as
// drawn: a mask with no holes would cover the whole map.
function maskOf(windows) {
  if (!windows.length) {
    say('The outline file does not say which of its shapes this page draws, so ' +
      'nothing outside them is covered.');
    return null;
  }
  const holes = windows.map(function (feature) {
    return ringsOf(feature.geometry);
  }).flat();
  return L.polygon([WORLD_RING].concat(holes), MASK_STYLE);
}

// The box round the 19 drawn boroughs: the view the map opens on.
function focusBounds(windows) {
  const box = L.latLngBounds(windows.map(function (feature) {
    return ringsOf(feature.geometry);
  }).flat(2));
  return box.isValid() ? box : null;
}

// The page, once: make the map, load the four files, draw what no filter can
// change - the borough edges, the parks, the metro points and the grey mask -
// then read the walk times out of the intersections file that has just loaded
// and wire the controls. Each step hands its result to the next through page or
// walk, so nothing here holds a value for long.
function start() {
  buildMap();
  Promise.all([
    loadGeojson('data/city_parks.geojson'),
    loadGeojson('data/intersections.geojson'),
    loadGeojson('data/metro_stops.geojson'),
    loadGeojson('data/boroughs.geojson')
  ])
    .then(function ([parksData, intersectionsData, metroData, outlines]) {
      // The outlines come with the base map, not the walk times: the mask is
      // the map's own shape, so it is in place before any filter is asked.
      walk.outlines = outlines;

      // Parks first, then the metro points, so a click finds a metro point
      // where a park edge lies under one; the size order of the parks file
      // settles the rest.
      const parks = L.geoJSON(parksData, {
        style: function (feature) {
          return PARK_STYLE[feature.properties.popup_kind_key] || PARK_STYLE['unknown'];
        },
        onEachFeature: function (feature, layer) {
          // Bound as a function, so the markup is built on opening, not for all
          // 1,981 parks up front.
          layer.bindPopup(function () { return parkPopup(feature.properties); });
        }
      }).addTo(map);

      // The intersections file's corners are counted, never drawn: it makes
      // no paths and no popups.

      // The STM lists each station point and then its entrances, so an entrance
      // is drawn over its station dot and takes the click where they overlap.
      const metros = L.geoJSON(metroData, {
        pointToLayer: function (feature, latlng) {
          const style = METRO_STYLE[feature.properties.location_type] || METRO_STYLE['2'];
          return L.circleMarker(latlng, style);
        },
        onEachFeature: function (feature, layer) {
          // Built when it is opened, as the park popups above are.
          layer.bindPopup(function () { return metroPopup(feature.properties); });
        }
      }).addTo(map);

      // The mask, added after the parks and metro points, takes the clicks they
      // miss; redraw() brings it to the front again.
      const windows = drawnOutlines(walk.outlines);
      // The 19 boroughs' rings, kept for the lookup's click. A click can land
      // under the grey mask, and Leaflet passes a click on a filled shape on to
      // the map as well, so the page has to know where its own windows are
      // before it answers one.
      page.rings = windows.map(function (feature) {
        return ringsOf(feature.geometry);
      }).flat();
      page.mask = maskOf(windows);
      if (page.mask) { page.mask.addTo(map); }

      // The 19 boroughs, edged and named on hover, and the opening view: the
      // whole of what the page draws.
      drawBoroughEdges(windows);
      const drawn = focusBounds(windows);
      const frame = drawn || parks.getBounds().extend(metros.getBounds());
      map.fitBounds(frame);

      // The hard edge on a drag: the map may be panned anywhere inside the
      // frame plus a small margin, and no further - not to the US, not to
      // another city, not to the ocean past the island. The margin is 5% of
      // the frame, a few hundred metres either way: enough that a borough on
      // the frame's edge keeps its rim beside the grey, not enough to reach
      // anything the page does not measure.
      map.setMaxBounds(frame.pad(0.05));

      // No zooming out past the fitted view: past it the mask fills the map
      // with grey. Zooming in stays free, and every drag stays inside the
      // frame set above.
      map.setMinZoom(map.getZoom());

      // The status line: metro points by kind and corners by how many street
      // ends meet at them.
      const counts = { '1': 0, '2': 0 };
      metroData.features.forEach(function (feature) {
        const kind = feature.properties.location_type;
        counts[kind] = (counts[kind] || 0) + 1;
      });
      const degrees = {};
      intersectionsData.features.forEach(function (feature) {
        const degree = feature.properties.degree;
        degrees[degree] = (degrees[degree] || 0) + 1;
      });
      const byDegree = Object.keys(degrees)
        .sort(function (a, b) { return a - b; })
        .map(function (degree) { return degrees[degree] + ' of ' + degree; })
        .join(', ');

      const status = document.getElementById('status');
      status.textContent = 'All data loaded.';
      status.title =
        intersectionsData.features.length + ' street intersections (' + byDegree +
        ' street ends), ' + metroData.features.length + ' metro points (' +
        counts['1'] + ' stations and ' + counts['2'] +
        ' entrances) and ' + parksData.features.length + ' green spaces loaded';

      // The walk times are already in the intersections file just loaded, so a
      // question needs no fetch. Hand over the two drawn files and open the
      // group the dots go into; redraw() settles what is drawn over what.
      readWalkData(intersectionsData);
      page.metro = metroData;
      page.parks = parksData;
      page.parkLayer = parks;
      page.metros = metros;
      describeWalk();
      page.walkGroup = L.layerGroup().addTo(map);
      // The lookup's own lines, in a group of their own so that a filter
      // change redraws the dots without touching an address's five walks.
      page.lookupGroup = L.layerGroup().addTo(map);
      wireFilters();
      wireLookup();
    })
    .catch(function (err) {
      document.getElementById('status').textContent =
        'Could not load the GeoJSON files. Open the console (F12) for details.';
      console.error(err);
    });
}

// --- The walk times: the two filters, and the corners they colour ----------
//
// The intersections file already carries each corner's walk for all 22
// questions, so nothing is fetched: this section reads those answers and
// colours the corners that pass the filters.

// Five shades per question, dark for the nearest and pale for the farthest,
// splitting the time asked for rather than an hour.
const WALK_RAMPS = {
  metro: ['#08324F', '#0E5B85', '#2188BC', '#6FB6DD', '#BFE1F2'],
  park: ['#6B3300', '#94500B', '#BE7420', '#DFA254', '#F4D2A0']
};
const WALK_SHADES = 5;
const WALK_RING = '#141414';           // the dark edge every coloured dot wears
const WALK_RADII = { 3: 2.5, 4: 3.5, 5: 4.5, 6: 5.5, 7: 7, 'other': 4 };
const EDGE_STYLE = { color: '#1F2D5A', weight: 1.5, opacity: 0.8,
                     dashArray: '6 4', fill: false };

// What the page is drawn from, once the four files have loaded: the
// intersections features, the two files a destination is named from, the park
// and metro layers the filters show and hide, the dots' group, and the mask.
const page = { dots: null, metro: null, parks: null, parkLayer: null,
               metros: null, walkGroup: null, mask: null, graph: null,
               lookupGroup: null, rings: null };

// The build's own answers, read from the intersections file: the layers it
// measured, each layer's walk in a corner's list, its counts per time, the
// statements for corners with no walk, and the corners the page may colour.
const walk = {
  outlines: null, layers: null, places: null, byChoice: null, board: null,
  corners: null, edges: null, asked: {}
};

function valueOf(id) { return document.getElementById(id).value; }

function minutesOf(id) {
  const value = valueOf(id);
  return value === 'none' ? null : Number(value);
}

// The questions the menus hold, read from the menus themselves: one entry per
// menu not left on n/a, with the build's layer for the choices and the minutes
// asked. The menus are the only record of what is asked; nothing mirrors them.
function askFilters() {
  const asked = {};
  const park = minutesOf('park-minutes');
  if (park !== null) {
    asked.park = { family: 'park', minutes: park,
      layer: layerFor('park', valueOf('park-kind'), valueOf('park-band')) };
  }
  const metro = minutesOf('metro-minutes');
  if (metro !== null) {
    asked.metro = { family: 'metro', minutes: metro,
      layer: layerFor('metro', valueOf('metro-kind'), 'metro') };
  }
  return asked;
}

// Which of the five shades a walk of so many seconds falls in. A walk at
// exactly the time asked for is in the last band, not one beyond it.
function shadeOf(seconds, minutes) {
  const band = minutes * 60 / WALK_SHADES;
  return Math.min(WALK_SHADES - 1, Math.floor(seconds / band));
}

function shadeColour(family, shade) { return WALK_RAMPS[family][shade]; }

// The five bands a filter's minutes are split into, said the way a walk is
// said: "under 3 min", then "3 to 6 min", up to the time that was asked for.
function shadeLabels(minutes) {
  const band = minutes / WALK_SHADES;
  const labels = [];
  for (let at = 0; at < WALK_SHADES; at += 1) {
    labels.push(at === 0
      ? 'under ' + whole(band) + ' min'
      : whole(at * band) + ' to ' + whole((at + 1) * band) + ' min');
  }
  return labels;
}

function whole(number) {
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

// A corner that passes both filters wears the two shades mixed channel by
// channel, a third palette rather than one dot in two colours.
function mixColour(one, other) {
  const channels = [1, 3, 5].map(function (at) {
    const first = parseInt(one.substr(at, 2), 16);
    const second = parseInt(other.substr(at, 2), 16);
    return ('0' + Math.round((first + second) / 2).toString(16)).slice(-2);
  });
  return '#' + channels.join('');
}

function count(value) {
  return value === null || value === undefined ? '&hellip;' : String(value);
}

// --- Reading the walk times off the map's own file ---------------------

// Everything the filters are answered from, read off the intersections file:
// the layers, each layer's place, the statements for corners with no walk, and
// which corners the page may colour.
function readWalkData(dots) {
  page.dots = dots;
  walk.layers = dots.walk_layers || [];
  walk.places = {};
  walk.byChoice = {};
  walk.layers.forEach(function (layer, at) {
    walk.places[layer.id] = at;
    walk.byChoice[[layer.family, layer.kind, layer.band].join(' ')] = layer;
  });
  // The file's statements, gathered here under the names this file uses: the
  // file says "no_street", the page reads walk.board.noStreet. A key that did
  // not arrive would leave every walk compared against nothing, which reads as
  // NaN - a value that is not a number, and is false against everything.
  walk.board = { outside: dots.statements.outside,
                 noStreet: dots.statements.no_street,
                 unreachable: dots.statements.unreachable };
  walk.corners = dots.features.filter(function (feature) {
    return feature.properties.drawn === true;
  }).map(function (feature) { return feature.properties.id; });
  if (!walk.layers.length) {
    say('The intersections file carries no walk times, so no question about a ' +
      'walk can be answered.');
  }
}

// One corner's walk to one question: the pair the build wrote beside the corner
// itself - the walk in seconds and the number of the destination it reached -
// read at the place that layer has in the build's own list of them.
function walkedOf(number, family) {
  const pair = page.dots.features[number]
    .properties.walk[walk.places[walk.asked[family].layer.id]];
  return { seconds: pair[0], destination: pair[1] };
}

// The layer a set of choices is. The build made exactly one layer per
// combination of family, kind and size band, so a combination with none of
// them is a bug the page can name rather than a blank map.
function layerFor(family, kind, band) {
  const layer = walk.byChoice[[family, kind, band].join(' ')];
  if (!layer) {
    throw new Error('the build has 0 layers for ' + family + ' ' + kind + ' ' + band);
  }
  return layer;
}

// The page's own wording in the filter choices, named from the file that
// answers them.
function describeWalk() {
  const data = page.dots;
  const missing = [];
  // The outlines and the build's measured boroughs must name the same 19, or a
  // borough is coloured without a walk, or masked with one.
  const measured = data.coverage.boroughs.map(function (entry) {
    return entry.name;
  });
  const flagged = drawnOutlines(walk.outlines);
  if (flagged.map(function (feature) { return feature.properties.name; })
      .join() !== measured.join()) {
    say('The build measured the corners of ' + measured.join(', ') +
      ' and this page draws ' + namesOf(flagged) + ': one of the two is wrong.');
  }
  fillMenu('park-kind', data.kinds, missing);
  fillMenu('park-band', data.bands, missing);
  fillMenu('metro-kind', data.metro_kinds, missing);
  // A time menu is the build's ticks beside a value that means n/a.
  const minutes = [{ key: 'none', label: 'n/a' }].concat(data.tick_minutes.map(
    function (minute) {
      return { key: String(minute), label: 'within ' + minute + ' min' };
    }));
  fillMenu('park-minutes', minutes, missing);
  fillMenu('metro-minutes', minutes, missing);
  if (missing.length) {
    say('The page offers a choice the build does not have: ' +
      missing.join(', '));
  }
}

// The wording of a menu, from the build rather than from this file, so a
// question cannot be described in two places at once. A menu whose options are
// not the build's own choices is named, not relabelled: it offers a choice the
// build never counted, or cannot ask for one it did.
function fillMenu(id, entries, missing) {
  const select = document.getElementById(id);
  const words = {};
  entries.forEach(function (entry) { words[entry.key] = entry.label; });
  Array.prototype.forEach.call(select.options, function (option) {
    if (words[option.value] === undefined) {
      missing.push(id + ' "' + option.value + '"');
    } else {
      option.textContent = words[option.value];
    }
  });
  if (select.options.length !== entries.length) {
    missing.push(id + ' offers ' + select.options.length + ' choices');
  }
}

// The 19 boroughs, drawn round as a dashed edge: with the mask covering
// everything else, they are the windows the map is read through. The edges go
// to the back, or they would take clicks meant for parks inside them.
function drawBoroughEdges(windows) {
  if (!walk.outlines) {
    say('The borough outlines did not come with the base map, so the boroughs ' +
      'the page draws are not drawn round.');
    return;
  }
  if (!windows.length) {
    say('The outline file does not say which of its shapes this page draws, so ' +
      'none of them is drawn round.');
    return;
  }
  walk.edges = L.geoJSON({ type: 'FeatureCollection', features: windows }, {
    style: EDGE_STYLE,
    onEachFeature: function (feature, layer) {
      layer.bindTooltip(feature.properties.name);
    }
  }).addTo(map);
}

function say(message) {
  document.getElementById('status').textContent = message;
}

// Seconds as a reader would say them: minutes under an hour, and hours and
// minutes beyond it, since a walk of seventy minutes is not helped by being
// called 70.
function formatWalk(seconds) {
  if (seconds < 60) { return 'under a minute'; }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) { return minutes + ' min'; }
  const hours = Math.floor(minutes / 60);
  const rest = minutes - hours * 60;
  return hours + ' h ' + (rest < 10 ? '0' + rest : rest) + ' min';
}

// --- The question, asked of one corner ------------------------------------
//
// One corner at a time: does it pass every filter that was asked, and if it
// does, which colour does it wear? The answer is one small record, and it is
// what both the dot and its popup are drawn from.

// What one corner is, with the filters as they stand: each walk, its shade,
// and the colour to draw. 'drawn', 'beyond' and 'nothing' separate a corner
// inside the time, one outside it, and one the build could not measure.
function lookOf(number, wanted) {
  const look = { kind: 'drawn', walks: {}, colour: null, gap: null };
  for (let at = 0; at < wanted.length; at += 1) {
    const family = wanted[at];
    const asking = walk.asked[family];
    const walked = walkedOf(number, family);
    if (walked.seconds >= walk.board.unreachable) {
      look.kind = 'nothing';
      look.gap = family;
      return look;
    }
    if (walked.seconds > asking.minutes * 60) {
      look.kind = 'beyond';
      return look;
    }
    look.walks[family] = {
      seconds: walked.seconds, shade: shadeOf(walked.seconds, asking.minutes),
      destination: walked.destination
    };
  }
  look.colour = wanted.length === 2
    ? mixColour(shadeColour('metro', look.walks.metro.shade),
                shadeColour('park', look.walks.park.shade))
    : shadeColour(wanted[0], look.walks[wanted[0]].shade);
  return look;
}

// --- Drawing the corners the filters pass ---------------------------------
//
// The corners that pass the filters, as dots on the map, with a popup each and
// the tally the legend is built from. What comes in is the choices, the minutes
// asked for and the walks already written on each corner; what goes out is a
// dot for every corner the page may colour, and the counts a legend can read.

function newCounts() {
  const zeros = function () { return [0, 0, 0, 0, 0]; };
  return {
    metro: zeros(), park: zeros(),
    cells: [zeros(), zeros(), zeros(), zeros(), zeros()],
    gaps: {}, drawn: 0, beyond: 0, total: walk.corners.length
  };
}

// The corners the filters pass, drawn at their own positions with their own
// popups, plus the tally the legend is built from. Only corners the build
// measured, inside the 19, are drawn at all.
function drawDots(wanted, counts) {
  const features = page.dots.features;
  const ids = walk.corners;
  for (let at = 0; at < ids.length; at += 1) {
    const number = ids[at];
    const look = lookOf(number, wanted);
    if (look.kind === 'nothing') {
      counts.gaps[look.gap] = (counts.gaps[look.gap] || 0) + 1;
      continue;
    }
    if (look.kind === 'beyond') { counts.beyond += 1; continue; }

    const feature = features[number];
    const marker = L.circleMarker(
      [feature.geometry.coordinates[1], feature.geometry.coordinates[0]], {
        radius: WALK_RADII[feature.properties.degree] || WALK_RADII.other,
        color: WALK_RING, weight: 1, fillColor: look.colour, fillOpacity: 0.95
      });
    // Bound as a function, so the markup is built only when a popup opens.
    marker.bindPopup(function () { return cornerPopup(number, look); });
    page.walkGroup.addLayer(marker);

    counts.drawn += 1;
    if (wanted.length === 2) {
      counts.cells[look.walks.park.shade][look.walks.metro.shade] += 1;
    } else {
      counts[wanted[0]][look.walks[wanted[0]].shade] += 1;
    }
  }
}

// --- Which features are drawn ---------------------------------------------
//
// Each family answers to its own filter: a menu left on n/a is the reader
// saying they do not care about that family, so none of its features are
// drawn. The layers are made once and taken on and off the map here, which
// keeps a restored choice drawing the features it asks about.
function showFeature(layer, asked) {
  if (!layer || !map) { return; }
  if (asked && !map.hasLayer(layer)) { layer.addTo(map); }
  else if (!asked && map.hasLayer(layer)) { map.removeLayer(layer); }
}

// Everything a filter changes goes through here: the dots, the path order and
// the legend, from one tally. Order: borough edges at the back, then parks,
// dots, the mask, and the metro points on top.
function redraw() {
  const wanted = Object.keys(walk.asked);
  const counts = newCounts();
  showFeature(page.parkLayer, walk.asked.park);
  showFeature(page.metros, walk.asked.metro);
  if (page.walkGroup) { page.walkGroup.clearLayers(); }
  if (wanted.length) { drawDots(wanted, counts); }
  if (page.metros && map.hasLayer(page.metros)) { page.metros.bringToFront(); }
  if (page.mask) { page.mask.bringToFront(); }
  // The lookup's lines go over the mask as well: they are drawn from an address
  // the reader chose, and a walk that ends at a dot must not pass under a dot.
  if (page.lookupGroup) {
    page.lookupGroup.eachLayer(function (layer) {
      if (layer.bringToFront) { layer.bringToFront(); }
    });
  }
  if (walk.edges) { walk.edges.bringToBack(); }
  drawLegend(counts, wanted);
  checkAgainstBuild(counts, wanted);
}

// What a click on a coloured dot says: the corner's name, its borough, then a
// line per filter asked, with the walk and what it reached.
function cornerPopup(number, look) {
  const properties = page.dots.features[number].properties;
  const lines = ['<strong>' + escapeHtml(properties.popup_title) + '</strong>'];
  if (properties.drawn === true) {
    lines.push('<span class="dim">' + escapeHtml(properties.borough) + '</span>');
  }
  ['park', 'metro'].forEach(function (family) {
    const walked = look.walks[family];
    if (!walked) { return; }
    lines.push(escapeHtml(family === 'metro' ? 'Metro' : 'Green space') + ': <b>' +
      escapeHtml(formatWalk(walked.seconds)) + '</b> to ' +
      escapeHtml(destinationName(family, walked.destination)));
  });
  return lines.join('<br>');
}

// A destination number is a feature's own number in the parks or metro file,
// so the name is read there rather than copied here.
function destinationName(family, number) {
  const opened = (family === 'metro' ? page.metro : page.parks).features[number];
  if (!opened) { return 'a place the file no longer holds'; }
  return family === 'metro' ? opened.properties.popup_name
                            : opened.properties.popup_title;
}

// --- The legend -----------------------------------------------------------
//
// The key first, then the question as the filters stand. The key is always the
// same five bands by five bands, so the table keeps its size and its place in
// its box under the filters, and only the words below it change.

function drawLegend(counts, wanted) {
  document.getElementById('legend-body').innerHTML = [
    legendTable(counts, wanted),
    '<p class="question">' + escapeHtml(questionText(wanted)) + '</p>',
    axisNote(wanted)
  ].join('');
}

// The question the map is answering, in the build's own words for the choices:
// a corner is coloured when every walk that was asked about is inside its own
// time, and the two times are never added together.
function questionText(wanted) {
  if (!wanted.length) {
    return 'Nothing is asked yet, so the map draws neither parks nor metro stations. ' +
      'Choose a walking time for green spaces, for metro stations, or for both: ' +
      'the features you ask about appear, and every corner within their reach is coloured.';
  }
  return 'Walk time to ' + askingText(wanted[0]) + (wanted.length === 1 ? '.' :
    ' and to ' + askingText(wanted[1]) + ': an intersection "dot" appears only when ' +
    'both requested walks are inside their own time, and its colour is the pair of ' +
    'bands it falls in.');
}

function askingText(family) {
  const asked = walk.asked[family];
  if (family === 'metro') {
    return labelOf('metro_kinds', asked.layer.kind).toLowerCase() +
      ' within ' + asked.minutes + ' min';
  }
  return 'a green space (' + labelOf('kinds', asked.layer.kind).toLowerCase() +
    ', ' + labelOf('bands', asked.layer.band).toLowerCase() + ') within ' +
    asked.minutes + ' min';
}

function labelOf(list, key) {
  const found = page.dots[list].filter(function (entry) {
    return entry.key === key;
  });
  return found.length ? found[0].label : key;
}

// A band's name for a table header: the unit is in the note under the key, and a
// dash and a "less than" keep "24 to 30" and "under 6" inside the fifth of a
// 300 px panel, where the long spelling filled the whole cell and ran into its
// neighbours.
function shortLabel(label) {
  const shorter = label.replace(' min', '').replace('under ', '<');
  return escapeHtml(shorter).replace(' to ', '&ndash;');
}

// The key: five bands by five bands, whatever is asked. Metro bands run across
// the top and green-space bands down the left, and each cell is the colour of
// the pair it stands for with the number of dots drawn that way. A family
// nobody asked about adds no names and no counts, so its cells stay blank and
// the grid keeps its shape instead of shrinking to a bar.
function legendTable(counts, wanted) {
  const both = wanted.length === 2;
  const only = wanted.length === 1 ? wanted[0] : null;
  const across = both ? shadeLabels(walk.asked.metro.minutes)
    : only ? shadeLabels(walk.asked[only].minutes) : null;
  const down = both ? shadeLabels(walk.asked.park.minutes) : null;

  const head = ['<tr><th></th>'];
  for (let col = 0; col < WALK_SHADES; col += 1) {
    head.push(across ? '<th>' + shortLabel(across[col]) + '</th>' : '<th></th>');
  }
  head.push('</tr>');

  const rows = [];
  for (let at = 0; at < WALK_SHADES; at += 1) {
    const row = [down ? '<th>' + shortLabel(down[at]) + '</th>' : '<th></th>'];
    for (let col = 0; col < WALK_SHADES; col += 1) {
      row.push(keyCell(counts, wanted, at, col));
    }
    rows.push('<tr>' + row.join('') + '</tr>');
  }
  return '<table class="blend">' + head.join('') + rows.join('') + '</table>';
}

// One cell of the key: the colour of the pair the row and the column stand for,
// and how many dots are drawn that way. One question asked puts its five counts
// along the top row, nearest first; a cell no question reaches is left blank.
function keyCell(counts, wanted, at, col) {
  if (wanted.length === 2) {
    const here = counts.cells[at][col];
    const colour = here ? ' style="background:' +
      mixColour(shadeColour('metro', col), shadeColour('park', at)) + '"' : '';
    return '<td class="cell' + (here ? '' : ' blank') + '"' + colour + '>' +
      (here ? count(here) : '&middot;') + '</td>';
  }
  if (wanted.length === 1 && at === 0) {
    return '<td class="cell" style="background:' + shadeColour(wanted[0], col) + '">' +
      count(counts[wanted[0]][col]) + '</td>';
  }
  return '<td class="cell blank"></td>';
}

// What the key holds, said under it: the grid for two answers at once, the one
// row of counts for one, and, before anything is asked, the note that says the
// key fills in - so the paragraph is always there and the column never jumps.
function axisNote(wanted) {
  if (wanted.length === 2) {
    return '<p class="axis">Metro bands horizontal (x axis), green spaces vertical (y axis), ' +
      'both in minutes. Each cell is the colour of an intersection passing both, and ' +
      'displays how many dots are coloured that way.</p>';
  }
  if (wanted.length === 1) {
    return '<p class="axis">' + (wanted[0] === 'metro' ? 'Metro' : 'Green spaces') +
      ' bands (in minutes), closest first, with each cell coloured by intersections within ' +
      'that band, and how many dots are coloured that way.</p>';
  }
  return '<p class="axis">The key fills in once a walking time is chosen: five bands ' +
    '(in minutes), closest first, from the dark end of the scale to the pale one.</p>';
}

// --- Reading the header, and answering it ---------------------------------
//
// The controls are the only record of what is asked, so a change reads them
// again rather than remembering what the last change was; a reload that
// restores a menu's last choice therefore answers that choice straight away.
// What comes out is walk.asked - one entry per menu not left on n/a - and a
// redraw from it.

// Wire the controls once the base map has loaded, then answer whatever they
// hold: a reload restores a menu's last choice without firing a change, so the
// page reads the menus rather than assuming they are blank.
function wireFilters() {
  ['park-minutes', 'park-kind', 'park-band', 'metro-minutes', 'metro-kind']
    .forEach(function (id) {
      document.getElementById(id).addEventListener('change', afterChange);
    });
  if (document.readyState === 'complete') {
    afterChange();
  } else {
    window.addEventListener('load', afterChange, { once: true });
  }
}

// A control was touched: read the header, then redraw. A question the file
// cannot answer clears the map and names the failure.
function afterChange() {
  try {
    walk.asked = askFilters();
    redraw();
  } catch (error) {
    walk.asked = {};
    redraw();
    say('The walk times could not be read: ' + error.message);
    console.error(error);
  }
}

// The build's own count for the time asked, compared with the five shades'
// sum: a mismatch is a bug in this file or in the build. With both filters
// asked there is no such number, so only one filter is checked.
function checkAgainstBuild(counts, wanted) {
  if (wanted.length !== 1) { return; }
  const family = wanted[0];
  const asked = walk.asked[family];
  const within = asked.layer.within[String(asked.minutes)];
  const here = counts[family].reduce(function (sum, value) {
    return sum + value;
  }, 0);
  if (within !== undefined && within !== here) {
    say('The build counted ' + within + ' corners within ' + asked.minutes +
      ' minutes and the page drew ' + here + ': one of the two is wrong.');
  }
}

// --- The address: a graph, a street name, and a walk made here -------------
//
// The two filters are answered by the build: every corner carries its own walk
// to every kind of destination, so a question about them needs no search. An
// address is the other case. Nobody knows which place a reader will type until
// they type it, so this section does what the build could not do in advance -
// turn the text into a place, find the street under that place, and search out
// from it - over the network the build wrote for the purpose, in
// data/walk_graph.json, and in the browser.
//
// The search is Dijkstra's, the same one the build runs over the same network:
// always deal with the nearest corner not yet dealt with, so no corner is given
// a distance that a longer way round could better. What comes back is a
// distance for every corner, and the corner each one was reached from - which is
// enough to draw the route, not only to name the walk.

const ADDRESS_GRAPH = 'data/walk_graph.json';
const SNAP_METRES = 100;       // how far a point may be from the street it is on
const STREET_TYPO = 0.9;       // how alike two street names must be to be one name
const NEAR_BLOCK = 100;        // how far outside a range a number may be and fit
const DESTINATION_PARKS = 3;
const DESTINATION_STATIONS = 2;
const SNAP_CELL = 200;         // metres across one square of the snapping grid

// Degrees to metres, for the two places this file has to measure in metres
// alone: how far a click is from a street, and where along a link it landed.
// The scale is the island's middle latitude, so the error over the whole island
// is a part in five hundred - a metre or two in a kilometre - which the snap
// does not care about, because it only has to pick the right street out of the
// two or three within a hundred metres. Every distance a walk is made of is
// measured by the build and read out of the file, never recomputed this way.
const ISLAND_LAT = 45.52;
const LON_METRES = 111320 * Math.cos(ISLAND_LAT * Math.PI / 180);
const LAT_METRES = 110540;

// The long form of each short way a reader might write a street's type. The
// city writes the long form into every name it publishes, so folding both sides
// this way is what lets "boul. Curé-Labelle" find "Boulevard Curé-Labelle"
// without the file holding two of every name.
const STREET_TYPES = {
  boul: 'boulevard', bd: 'boulevard',
  av: 'avenue', ave: 'avenue',
  ch: 'chemin', chem: 'chemin',
  pl: 'place', plc: 'place',
  crt: 'croissant', crois: 'croissant',
  mont: 'montee', rte: 'route',
  st: 'saint', ste: 'sainte',
  cds: 'cote'
};

// The types themselves, so the second pass can set the type aside and compare
// what is left: a reader who types "Sherbrooke Est" has named the street the
// city calls "Rue Sherbrooke Est", and nothing about the word "rue" says which
// street is meant.
const TYPE_WORDS = ['rue', 'avenue', 'boulevard', 'chemin', 'place',
  'croissant', 'montee', 'route', 'cote', 'rang', 'autoroute', 'impasse',
  'allee', 'sentier', 'pont', 'voie', 'square', 'quai', 'terrasse', 'ruelle'];

// A street name as this file compares them: lower case, no accents, one space
// between words, no punctuation, and the type spelled out. Two names that tidy
// to the same text are the same street to every pass below, which is exactly
// the point: the city writes "Rue de la Gauchetière" and "Rue De La
// Gauchetière" on different troncons of the one street.
function tidy(text) {
  return String(text)
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/\u0153/g, 'oe').replace(/\u00e6/g, 'ae')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .split(' ')
    .map(function (word) { return STREET_TYPES[word] || word; })
    .join(' ');
}

// The same name with its type word taken off the front, for the second pass.
function withoutType(key) {
  const words = key.split(' ');
  return words.length > 1 && TYPE_WORDS.indexOf(words[0]) >= 0
    ? words.slice(1).join(' ') : key;
}

// How alike two names are, from 0 to 1: one less the share of the longer name
// that has to be changed to turn it into the other, a letter changed or added or
// dropped counting one each. I first tried counting shared letter pairs, and it
// refused "Sherbrook" against "Sherbrooke" at 0.85: dropping a letter there
// breaks two pairs, the ones on either side of it, so the commonest slip of all
// cost twice what it should. Counting the letters that have to change costs it
// exactly one letter in eighteen.
//
// The same measure keeps "Rue Sherbrooke Est" away from "Rue Sherbrooke Ouest":
// they differ by two letters, which is one name in nine, and the word they
// differ in is the one word that says which street is meant.
function similarity(one, other) {
  if (one === other) { return 1; }
  const longest = Math.max(one.length, other.length);
  if (!longest) { return 0; }
  // Two names whose lengths are far apart cannot come within reach however
  // their letters fall, since every missing letter has to be made up - so they
  // are dropped without being counted.
  if (Math.abs(one.length - other.length) > longest * (1 - STREET_TYPO)) {
    return 0;
  }
  return 1 - distance(one, other) / longest;
}

// How many single letters have to be changed to turn one name into the other:
// one row of numbers at a time, each cell the cheapest of the three ways there -
// keep the letter, change it, or leave one out.
function distance(one, other) {
  let before = [];
  for (let at = 0; at <= other.length; at += 1) { before.push(at); }
  for (let down = 1; down <= one.length; down += 1) {
    const here = [down];
    for (let across = 1; across <= other.length; across += 1) {
      here.push(Math.min(
        before[across] + 1,
        here[across - 1] + 1,
        before[across - 1] +
          (one.charAt(down - 1) === other.charAt(across - 1) ? 0 : 1)));
    }
    before = here;
  }
  return before[other.length];
}


// --- Reading the graph once into the shape the lookups want ----------------

// The graph as the lookups use it: the file's own lists, plus three things
// built from them once and used by every lookup after - the streets gathered
// under their tidied names, the links filed by square for the snapping, and the
// corners' links laid end to end for the search.
function prepareGraph(document) {
  const graph = document;
  graph.groups = [];                  // one per distinct street name
  graph.groupAt = {};                 // that name -> its place in the list
  graph.bareAt = {};                  // the same, with the type word taken off
  // Two spellings of one name are one street here, so their troncons are
  // gathered together: a reader who types the name one way still finds the
  // block the city wrote the other.
  document.names.forEach(function (name, index) {
    const key = tidy(name);
    if (graph.groupAt[key] === undefined) {
      graph.groupAt[key] = graph.groups.length;
      graph.groups.push({ key: key, bare: withoutType(key),
                          names: [index], rows: [] });
    } else {
      graph.groups[graph.groupAt[key]].names.push(index);
    }
  });
  graph.groups.forEach(function (group, index) {
    if (graph.bareAt[group.bare] === undefined) { graph.bareAt[group.bare] = index; }
  });
  const groupOfName = document.names.map(function (name) {
    return graph.groupAt[tidy(name)];
  });
  document.roads.forEach(function (row) {
    graph.groups[groupOfName[row[0]]].rows.push(row);
  });
  graph.grid = linkGrid(document);
  return graph;
}

// The street a piece of text names, as a place in graph.groups, or a record of
// the names that came closest.
//
// Three passes, in the order a reader's guess should be trusted. The name as
// written, which is the one answer that cannot be wrong. Then the name with its
// type word set aside, which is how "Sherbrooke Est" finds "Rue Sherbrooke
// Est". Then the closest name in the file, taken only when it is close enough
// that a mistyped letter is the likelier story - which is what keeps
// "Curé-Labelle", a street of Laval that this file has no troncon for, from
// being answered with some Montreal street that merely looks like it.
//
// Every answer says how it was reached, so the page can print the street it
// settled on: a guess a reader can see is a guess they can correct.
function findStreet(graph, text) {
  const wanted = tidy(text);
  const bare = withoutType(wanted);
  const nearest = [];
  let best = null;
  graph.groups.forEach(function (group, index) {
    const score = Math.max(similarity(wanted, group.key),
                           similarity(bare, group.bare));
    if (!best || score > best.score) { best = { index: index, score: score }; }
    // Kept whether or not it is close enough, so that a refusal can name the
    // three streets that came nearest: a reader who has typed "Sherbrook" can
    // see the spelling the city uses and try again.
    nearest.push({ index: index, score: score, name: graph.names[group.names[0]] });
  });
  nearest.sort(function (one, other) { return other.score - one.score; });
  const closest = nearest.slice(0, 3);

  if (graph.groupAt[wanted] !== undefined) {
    return { found: true, index: graph.groupAt[wanted], how: 'exact',
             score: 1, nearest: closest };
  }
  if (graph.bareAt[bare] !== undefined) {
    const index = graph.bareAt[bare];
    return { found: true, index: index, how: 'type-free',
             score: similarity(bare, graph.groups[index].bare),
             nearest: closest };
  }
  if (best && best.score >= STREET_TYPO) {
    return { found: true, index: best.index, how: 'fuzzy',
             score: best.score, nearest: closest };
  }
  return { found: false, nearest: closest };
}

// The number and the street in a line of text: "4250 Rue Sherbrooke Est". A
// line with no number in front of it names no place on a street that has
// hundreds of numbers along it, so it is refused here rather than matched to
// whichever end of the street comes first.
function splitAddress(text) {
  const asked = String(text).trim().match(/^(\d+)\s*(.*)$/);
  if (!asked || !asked[2]) { return null; }
  return { number: Number(asked[1]), street: asked[2] };
}

// --- A number on a street, and the point it stands at ----------------------

// Where along its street a number lies: one of the troncon's two sides and the
// share of the range from its first end. A troncon that holds the number is
// taken whole, and the number is placed its share of the way from DEB to FIN,
// the two ends in the order the city drew them. Reading those ends backwards
// would swap the sides of the street and put the number at the far end of its
// block - which is the one way this placement can be out by hundreds of metres,
// and the reason the file keeps the troncon's own direction.
//
// When no troncon of the street holds the number, the nearest block end within
// NEAR_BLOCK is used and the distance reported, because a street whose numbers
// the city stopped publishing at some block still has a house at the end of it.
// Beyond that distance nothing is placed: an address invented at the end of the
// street is worse than a refusal, since every walk drawn from it would be drawn
// from somewhere the reader did not ask about.
function findNumber(rows, number) {
  let held = null;
  let near = null;
  rows.forEach(function (row) {
    [[row[3], row[4], 'left'], [row[5], row[6], 'right']]
      .forEach(function (side) {
        const deb = side[0];
        const fin = side[1];
        if (!deb || !fin || deb === fin) { return; }
        const share = (number - deb) / (fin - deb);
        if (share >= 0 && share <= 1) {
          if (!held || Math.abs(share - 0.5) < Math.abs(held.share - 0.5)) {
            held = { row: row, side: side[2], share: share, gap: 0 };
          }
          return;
        }
        const low = Math.min(deb, fin);
        const high = Math.max(deb, fin);
        const gap = number < low ? low - number : number - high;
        if (gap > 0 && gap <= NEAR_BLOCK && (!near || gap < near.gap)) {
          near = { row: row, side: side[2], gap: gap,
                   share: Math.max(0, Math.min(1, share)) };
        }
      });
  });
  return held || near;
}

// The point a number stands at, in the degrees the map is drawn in: its share
// of the way along the troncon its side belongs to. The troncon's two ends are
// corners of the network, so the point is on the network by construction, and
// the snap below finds the street it is already standing on.
function pointOn(graph, found) {
  const start = graph.nodes[found.row[1]];
  const end = graph.nodes[found.row[2]];
  return [start[0] + (end[0] - start[0]) * found.share,
          start[1] + (end[1] - start[1]) * found.share];
}

// The whole of what a line of text resolves to: the number, the street it named
// and how it named it, the troncon and side the number was placed on, and the
// point. Each refusal comes back as its own kind, because each has its own
// sentence to write: a name this file does not have and a number this street
// does not have are different mistakes, and a reader can only put right the one
// they made.
function addressPoint(graph, text) {
  const asked = splitAddress(text);
  if (!asked) { return { kind: 'no-number' }; }
  const street = findStreet(graph, asked.street);
  if (!street.found) {
    return { kind: 'no-street', asked: asked, nearest: street.nearest };
  }
  const group = graph.groups[street.index];
  const found = findNumber(group.rows, asked.number);
  const name = graph.names[group.names[0]];
  if (!found) {
    return { kind: 'no-block', asked: asked, how: street.how, name: name };
  }
  return { kind: 'found', asked: asked, how: street.how, name: name,
           gap: found.gap, point: pointOn(graph, found) };
}

// --- The network under a point --------------------------------------------

// The links filed by 200 m square, so the snap compares a point with the
// handful of links around it rather than with all 274,085 of them. A link goes
// under every square its box touches: a link can run across several, and the
// point may be over any part of it.
function linkGrid(graph) {
  const cells = {};
  graph.links.forEach(function (link, index) {
    const one = graph.nodes[link[0]];
    const other = graph.nodes[link[1]];
    const west = Math.floor(Math.min(one[0], other[0]) * LON_METRES / SNAP_CELL);
    const east = Math.floor(Math.max(one[0], other[0]) * LON_METRES / SNAP_CELL);
    const south = Math.floor(Math.min(one[1], other[1]) * LAT_METRES / SNAP_CELL);
    const north = Math.floor(Math.max(one[1], other[1]) * LAT_METRES / SNAP_CELL);
    for (let cx = west; cx <= east; cx += 1) {
      for (let cy = south; cy <= north; cy += 1) {
        const key = cx + ':' + cy;
        if (cells[key]) { cells[key].push(index); } else { cells[key] = [index]; }
      }
    }
  });
  return cells;
}

// How far a point is from one link and where along it it lands: the distance in
// metres, the share of the link from its first corner, and the link's length.
function nearestOnLink(graph, index, point) {
  const link = graph.links[index];
  const start = graph.nodes[link[0]];
  const end = graph.nodes[link[1]];
  const across = (point[0] - start[0]) * LON_METRES;
  const up = (point[1] - start[1]) * LAT_METRES;
  const along = (end[0] - start[0]) * LON_METRES;
  const rise = (end[1] - start[1]) * LAT_METRES;
  const square = along * along + rise * rise;
  const share = square === 0 ? 0
    : Math.max(0, Math.min(1, (across * along + up * rise) / square));
  return { link: index, share: share, length: Math.sqrt(square),
           metres: Math.hypot(across - share * along, up - share * rise) };
}

// The nearest link to a point, within reach of it in metres, or null when the
// point is not near a street at all. A point is compared with the links in its
// own square and the eight around it: a square is 200 m across and the reach is
// 100 m, so no link within reach of the point can be in a square further off.
function snapToNetwork(graph, point, reach) {
  const cx = Math.floor(point[0] * LON_METRES / SNAP_CELL);
  const cy = Math.floor(point[1] * LAT_METRES / SNAP_CELL);
  let best = null;
  for (let west = -1; west <= 1; west += 1) {
    for (let south = -1; south <= 1; south += 1) {
      const listed = graph.grid[(cx + west) + ':' + (cy + south)];
      if (!listed) { continue; }
      listed.forEach(function (index) {
        const hit = nearestOnLink(graph, index, point);
        if (!best || hit.metres < best.metres) { best = hit; }
      });
    }
  }
  return best && best.metres <= reach ? best : null;
}

// --- The search ------------------------------------------------------------

// How many of the file's length units there are in a metre. The build writes
// every length in centimetres, so the divisions here are the only place this
// file knows that.
const GRAPH_PARTS = 100;

// The corners' links, laid out in one run: each corner's links follow one
// another in a single list, so the search steps through an array to visit a
// corner's neighbours instead of looking anything up. It is built on the first
// search, so a page nobody gives an address to never pays for it.
function prepareSearch(graph) {
  if (graph.start) { return graph; }
  const corners = graph.nodes.length;
  const start = new Int32Array(corners + 1);
  graph.links.forEach(function (link) {
    start[link[0] + 1] += 1;
    start[link[1] + 1] += 1;
  });
  for (let at = 0; at < corners; at += 1) { start[at + 1] += start[at]; }
  const other = new Int32Array(start[corners]);
  const length = new Int32Array(start[corners]);
  const next = start.slice(0, corners);
  graph.links.forEach(function (link) {
    other[next[link[0]]] = link[1];
    length[next[link[0]]] = link[2];
    next[link[0]] += 1;
    other[next[link[1]]] = link[0];
    length[next[link[1]]] = link[2];
    next[link[1]] += 1;
  });
  graph.start = start;
  graph.other = other;
  graph.length = length;
  return graph;
}

// Dijkstra from a point on the network, as the build runs it: always deal with
// the nearest corner not yet dealt with, so a corner's distance is settled
// before it is used to settle another's, and every distance that comes out is
// the shortest way there is.
//
// The reader's point is a spot part-way along a link, not a corner, so the
// search begins at both ends of that link with the distance from the point to
// each already covered. That is what lets a walk start at an address without a
// corner being made for it: the two ends carry the address's distance, and
// every other corner is reached from one of them the usual way.
//
// Returns the distance to every corner, and the corner each was reached from.
// The corner the walk started at has none behind it, which is where the route
// below stops reading backwards.
function walkNetwork(graph, seeds) {
  prepareSearch(graph);
  const corners = graph.nodes.length;
  const metres = new Float64Array(corners);
  const came = new Int32Array(corners);
  metres.fill(Infinity);
  came.fill(-1);
  const heap = [];
  const key = [];

  function swap(one, other) {
    const corner = heap[one];
    heap[one] = heap[other];
    heap[other] = corner;
    const far = key[one];
    key[one] = key[other];
    key[other] = far;
  }

  // The nearest corner is always at the top of the heap, so the search never
  // has to look for it: a new corner climbs until its own parent is nearer.
  function push(corner, distance) {
    heap.push(corner);
    key.push(distance);
    let at = heap.length - 1;
    while (at > 0) {
      const up = (at - 1) >> 1;
      if (key[up] <= key[at]) { break; }
      swap(up, at);
      at = up;
    }
  }

  function pop() {
    const corner = heap[0];
    const distance = key[0];
    const last = heap.length - 1;
    heap[0] = heap[last];
    key[0] = key[last];
    heap.pop();
    key.pop();
    let at = 0;
    for (;;) {
      const left = at * 2 + 1;
      const right = left + 1;
      let small = at;
      if (left < heap.length && key[left] < key[small]) { small = left; }
      if (right < heap.length && key[right] < key[small]) { small = right; }
      if (small === at) { break; }
      swap(at, small);
      at = small;
    }
    return { corner: corner, metres: distance };
  }

  seeds.forEach(function (seed) {
    if (seed.metres < metres[seed.corner]) {
      metres[seed.corner] = seed.metres;
      push(seed.corner, seed.metres);
    }
  });

  while (heap.length) {
    const here = pop();
    // A corner can be pushed more than once, each time a shorter way to it is
    // found. Only the shortest of those is worth spreading out from, and the
    // others are thrown away here rather than being walked out of twice.
    if (here.metres > metres[here.corner]) { continue; }
    const last = graph.start[here.corner + 1];
    for (let at = graph.start[here.corner]; at < last; at += 1) {
      const other = graph.other[at];
      const step = here.metres + graph.length[at] / GRAPH_PARTS;
      if (step < metres[other]) {
        metres[other] = step;
        came[other] = here.corner;
        push(other, step);
      }
    }
  }
  return { metres: metres, came: came };
}

// The nearest destinations of one family, from the distances the search found:
// for each destination the shortest of its gateways, then the nearest few of
// those. A destination whose every gateway went unreached is left out, because
// a park on a piece of network the rest of the island does not join is a park
// no walk here can reach, and listing it with no walk would be the worse answer.
//
// The gateway's own stub is added to the walk: the search stops at the corner
// on the pavement, and the last few metres are the ones from the pavement to
// the door or to the park's edge.
function nearestDestinations(graph, metres, destinations, count) {
  const found = [];
  destinations.forEach(function (destination) {
    let best = null;
    destination[1].forEach(function (gateway) {
      const away = metres[gateway[0]];
      if (!isFinite(away)) { return; }
      const total = away + gateway[1] / GRAPH_PARTS;
      if (!best || total < best.metres) {
        best = { metres: total, corner: gateway[0] };
      }
    });
    if (best) {
      best.destination = destination[0];
      found.push(best);
    }
  });
  found.sort(function (one, other) { return one.metres - other.metres; });
  return found.slice(0, count);
}

// The corners a walk went through, from where it started to the gateway it
// ended at: each corner's own corner read backwards, to the one with none
// behind it. The reader's own point is not a corner and is not in here: it lies
// on the link the search started from, and the caller draws the line from it to
// the first corner, which is the stretch of that link already walked.
function routeOf(graph, came, target) {
  const corners = [];
  let corner = target;
  while (corner !== -1 && corners.length <= graph.nodes.length) {
    corners.push(corner);
    corner = came[corner];
  }
  corners.reverse();
  return corners.map(function (index) { return graph.nodes[index]; });
}

// One lookup, from the point the reader gave: the walk to the nearest three
// green spaces and the nearest two stations, each with the metres walked, the
// seconds that makes at the build's walking speed, and the line to draw.
//
// Both families come out of one search, since one search already has the
// distance to every corner: asking twice would walk the whole network twice to
// answer two halves of the same question.
function lookupAt(graph, point, reach) {
  const snap = snapToNetwork(graph, point, reach || SNAP_METRES);
  if (!snap) { return { kind: 'no-street', point: point }; }
  const link = graph.links[snap.link];
  const length = link[2] / GRAPH_PARTS;
  const seeds = [
    { corner: link[0], metres: snap.share * length },
    { corner: link[1], metres: (1 - snap.share) * length }
  ];
  const searched = walkNetwork(graph, seeds);
  const walks = [];
  [['park', graph.parks, DESTINATION_PARKS],
   ['metro', graph.stations, DESTINATION_STATIONS]]
    .forEach(function (family) {
      nearestDestinations(graph, searched.metres, family[1], family[2])
        .forEach(function (found) {
          walks.push({
            family: family[0], destination: found.destination,
            metres: found.metres, corner: found.corner,
            seconds: found.metres / graph.meta.walk_speed_mps,
            line: routeOf(graph, searched.came, found.corner)
          });
        });
    });
  walks.sort(function (one, other) { return one.metres - other.metres; });
  return { kind: 'found', point: point, snap: snap, walks: walks };
}

// --- The lookup on the page -----------------------------------------------
//
// The engine above answers a question about a place; this is the page's part of
// it - the box to type in, the click to answer, the five lines and the list. The
// graph is fetched on the first question rather than with the rest of the page,
// because it is the fifth file and the other four are what the map opens with: a
// reader who only ever uses the filters never downloads it.

// The two families' lines, in the dark end of the palette the filters already
// use, so a lookup's lines read as the same two things the dots stand for and
// not as a seventh kind of mark to interpret.
const LOOKUP_STYLE = {
  park: { color: WALK_RAMPS.park[0], weight: 4, opacity: 0.95 },
  metro: { color: WALK_RAMPS.metro[0], weight: 4, opacity: 0.95 }
};
const LOOKUP_START = { radius: 6, color: '#ffffff', weight: 2,
                       fillColor: '#DCBD23', fillOpacity: 1 };
// A park the answer names is drawn as the city drew it, in a green dark enough to
// read over the grey mask: the parks layer answers to a menu that may well be left
// on n/a, and a walk that ends "in Parc La Fontaine" is only useful if the shape
// called Parc La Fontaine can be found.
const LOOKUP_PARK = { color: '#1B5E20', weight: 2, opacity: 0.9,
                      fillColor: '#7CC47F', fillOpacity: 0.35 };
const LOOKUP_ZOOM = 16;    // the closest the map is taken to an answer
const LOOKUP_PAD = 40;     // pixels of margin round the six points fitted

// The one question this page asks anything but its own data, and the one thing
// it is allowed to be told: how the city spells a street it has never heard of.
// Photon is a free geocoder that needs no key, which is why it is the only one
// that can be used from a static page at all - every service that places an
// address properly wants a key, and a key on a public page is a key given away.
//
// Its answers are read for their names and nothing else: the coordinates go in
// the bin, because a service that puts 4250 Rue Sherbrooke Est seven kilometres
// from where it is cannot be trusted to put it anywhere, while the city's own
// file of street numbers can. The spelling is the whole of what is kept.
const PHOTON_ASSIST = true;
const PHOTON_URL = 'https://photon.komoot.io/api/';
const PHOTON_LIMIT = 5;
const PHOTON_WAIT = 3000;              // milliseconds before the question is dropped
const PHOTON_AT = { lat: ISLAND_LAT, lon: -73.58 };

// One answer per spelled street, for the length of the visit: a reader who asks
// twice about the same misspelling asks Photon once, which is what its usage
// policy asks of a page that cannot cache anything but its own memory.
const photon = { spelt: {} };

// The street names in Photon's answer, in the order it ranked them. Only a
// street inside Montreal counts: the point of the question is to re-spell a name
// the city's own file may hold, and a result from Laval or Longueuil is another
// city's street, on which this map measures nothing.
function namesOfPhoton(answer) {
  const features = answer && answer.features ? answer.features : [];
  const names = [];
  features.forEach(function (feature) {
    const props = feature.properties || {};
    if (!props.name || props.osm_key !== 'highway') { return; }
    if (tidy(props.city || '') !== 'montreal') { return; }
    if (names.indexOf(props.name) < 0) { names.push(props.name); }
  });
  return names;
}

// Photon, asked for the spelling of one street: one attempt, a few seconds, and
// an empty answer rather than an error, because a refusal is what happens when
// a stranger cannot help.
function askPhoton(text) {
  return new Promise(function (resolve) {
    let settled = false;
    const timer = setTimeout(function () { finish([]); }, PHOTON_WAIT);

    function finish(names) {
      if (settled) { return; }
      settled = true;
      clearTimeout(timer);
      resolve(names);
    }

    const url = PHOTON_URL + '?q=' + encodeURIComponent(text) +
      '&limit=' + PHOTON_LIMIT + '&lat=' + PHOTON_AT.lat +
      '&lon=' + PHOTON_AT.lon;
    fetch(url).then(function (response) {
      if (!response.ok) {
        throw new Error('HTTP ' + response.status + ' from Photon');
      }
      return response.json();
    }).then(function (answer) {
      finish(namesOfPhoton(answer));
    }).catch(function (error) {
      console.error(error);
      finish([]);
    });
  });
}

// The address asked again with each spelling Photon offers for its street, until
// one is a name the city's file holds. What comes back is the address placed on
// that spelling, or null when none of them is a street this map measures - which
// is what happens for a street of Laval, however well it is spelled.
function reSpell(graph, split) {
  const key = tidy(split.street);
  if (!photon.spelt[key]) {
    photon.spelt[key] = askPhoton(split.street).then(function (names) {
      for (let at = 0; at < names.length; at += 1) {
        const again = addressPoint(graph, split.number + ' ' + names[at]);
        if (again.kind === 'found') {
          again.how = 're-spelt';
          return again;
        }
      }
      return null;
    });
  }
  return photon.spelt[key];
}


// The graph, once a visit. What is kept is the promise rather than the graph, so
// two clicks in the same second share one download instead of starting two, and
// a download that fails is forgotten rather than remembered as the answer.
function loadGraph() {
  if (!page.graph) {
    page.graph = loadGeojson(ADDRESS_GRAPH).then(function (document) {
      return prepareGraph(document);
    });
    page.graph.catch(function () { page.graph = null; });
  }
  return page.graph;
}

// The box and the map, wired once the base map is up. The form is what catches
// the button and the Enter key alike; the map's own click is the other way to
// ask, and both end in the same answer.
function wireLookup() {
  document.getElementById('lookup-form').addEventListener('submit',
    function (event) {
      event.preventDefault();
      lookupTyped(document.getElementById('lookup-text').value);
    });
  map.on('click', function (event) {
    // A click that has already landed on something the page draws is a click on
    // that thing: its own popup is the answer, and no lookup starts from it. The
    // grey mask is the exception, because a click there is a question about a
    // place this map does not measure, and that deserves a sentence.
    if (event.layer && event.layer !== page.mask) { return; }
    lookupPoint(event.latlng.lat, event.latlng.lng);
  });
}

// Whether a place is inside one of the 19 boroughs the page draws: the same
// crossing count the build's own point_in_ring() makes, over the rings kept when
// the map loaded. A click under the mask is outside them all, and a walk from
// there would start on a street the page has greyed out.
function inDrawn(lat, lng) {
  const rings = page.rings || [];
  if (!rings.length) { return true; }
  for (let at = 0; at < rings.length; at += 1) {
    if (inRing(lat, lng, rings[at])) { return true; }
  }
  return false;
}

function inRing(lat, lng, ring) {
  let inside = false;
  for (let at = 0, before = ring.length - 1; at < ring.length;
       before = at, at += 1) {
    const one = ring[at];
    const other = ring[before];
    if ((one[0] > lat) !== (other[0] > lat)) {
      const crossing = other[1] +
        (lat - other[0]) * (one[1] - other[1]) / (one[0] - other[0]);
      if (lng < crossing) { inside = !inside; }
    }
  }
  return inside;
}

// How the street was arrived at, in words a reader can act on: the three passes
// and the stranger's spelling are the file's own shorthand, and these are what
// they mean to someone looking at the popup.
const LOOKUP_NOTE = {
  'type-free': 'the type word was set aside to find this street',
  'fuzzy': 'the name was corrected to the city spelling',
  're-spelt': 'the name was re-spelt by Photon'
};

// A line of text from the box. The street's name is looked up in the file; if
// the file has no street by that name, Photon is asked how Montreal spells it
// and the address is tried again in that spelling. Every refusal says what to
// type next rather than only that something was wrong, because a reader who has
// typed the wrong thing needs the right thing.
function lookupTyped(text) {
  const typed = String(text === undefined || text === null ? '' : text).trim();
  if (!typed) {
    return sayLookup('Type a street number and a street, as in 4250 Rue ' +
      'Sherbrooke Est, or click the map.');
  }
  waitForGraph();
  loadGraph().then(function (graph) {
    const placed = addressPoint(graph, typed);
    if (placed.kind !== 'no-street' || !PHOTON_ASSIST) {
      return showPlaced(graph, placed, typed, false);
    }
    const split = splitAddress(typed);
    if (!split) { return showPlaced(graph, placed, typed, false); }
    // Said before the wait rather than after it: a reader who is about to be
    // refused deserves to know that something is being tried first.
    sayLookup('No street by that name in the street file. Asking Photon how ' +
      'Montreal spells it, rather than refusing straight away.');
    return reSpell(graph, split).then(function (again) {
      showPlaced(graph, again || placed, typed, true);
    });
  }).catch(reportFailure);
}

// What one placed address means for the page: the five walks, or the sentence
// that says why there are none. typed is the text the reader wrote, which the
// numberless refusal needs in order to tell a number with no street from a
// street with no number; asked says whether Photon was consulted, so that a
// refusal can admit it rather than claiming the last word.
function showPlaced(graph, placed, typed, asked) {
  if (placed.kind === 'no-number') {
    return sayLookup(/^\s*\d/.test(typed)
      ? 'That is a number on no street. Add the street after it, as in ' +
        '4250 Rue Sherbrooke Est.'
      : 'Type the street number first, then the street, as in 4250 Rue ' +
        'Sherbrooke Est.');
  }
  if (placed.kind === 'no-street') {
    const near = (placed.nearest || []).map(function (each) {
      return each.name;
    });
    return sayLookup('No street by that name in the street file' +
      (asked ? ', and Photon spells nothing in Montreal that fits it' : '') +
      '.' + (near.length ? ' The nearest names are ' + listOf(near) + '.' : ''));
  }
  if (placed.kind === 'no-block') {
    return sayLookup(placed.name + ' has no number ' + placed.asked.number +
      ' in the street file, so there is nothing to stand on.');
  }
  drawLookup(placed.point, lookupAt(graph, placed.point),
             placed.asked.number + ' ' + placed.name, placed.how);
}

// A click on the map: the same answer, asked from a place instead of a name.
// Somewhere outside the 19 boroughs is refused by name, since the grey mask is a
// shape rather than a lock and Leaflet passes its clicks on to the map.
function lookupPoint(lat, lng) {
  if (!inDrawn(lat, lng)) {
    return sayLookup('That spot is outside the 19 boroughs this map covers, so ' +
      'the page measures nothing there.');
  }
  waitForGraph();
  loadGraph().then(function (graph) {
    const found = lookupAt(graph, [lng, lat]);
    if (found.kind === 'no-street') {
      return sayLookup('No street within ' + SNAP_METRES + ' m of that spot, so ' +
        'there is nowhere to start walking from.');
    }
    drawLookup([lng, lat], found, 'the point you clicked', null);
  }).catch(reportFailure);
}

// The first question waits for the graph, which is the one thing on this page
// that is fetched when it is needed: saying so is better than a box that looks
// broken for the length of a download.
function waitForGraph() {
  if (!page.graph) {
    sayLookup('Loading the street network, the one file this page fetches when ' +
      'it is needed. It is about 13 MB, and a second question uses it without ' +
      'downloading it again.');
  }
}

// What the box under the field says. A new question clears the last answer off
// the map rather than leaving five lines pointing at an address that is no
// longer the one being asked about.
function sayLookup(message) {
  if (page.lookupGroup) { page.lookupGroup.clearLayers(); }
  document.getElementById('lookup-body').innerHTML =
    '<p class="said">' + escapeHtml(message) + '</p>';
}

function reportFailure(error) {
  sayLookup('The street network could not be loaded, so no address can be ' +
    'walked from. Open the console (F12) for details.');
  console.error(error);
}

// A list as prose names it: "a, b and c".
function listOf(words) {
  return words.join(', ').replace(/, ([^,]*)$/, ' and $1');
}

// The answer, drawn and listed: the reader's own point, a line from it to each
// of the three parks and two stations, and the same five named under the field
// with their walks, shortest first.
//
// A line is the corners the search went through, with the reader's own point in
// front of them - that point lies on the link the search started from, so the
// first segment is the stretch of that link already walked - and the last few
// metres from the pavement to the park's edge or the station, which is the stub
// the build measured and the search counted as covered.
function drawLookup(point, found, label, how) {
  page.lookupGroup.clearLayers();
  const at = [point[1], point[0]];

  const start = L.circleMarker(at, LOOKUP_START);
  const note = LOOKUP_NOTE[how];
  start.bindPopup('<strong>' + escapeHtml(label) + '</strong>' +
    (note ? '<br><span class="dim">' + escapeHtml(note) + '</span>' : ''));
  page.lookupGroup.addLayer(start);

  const walks = found.walks.slice().sort(function (one, other) {
    return one.seconds - other.seconds;
  });

  walks.forEach(function (walk) {
    const line = [at].concat(walk.line.map(function (corner) {
      return [corner[1], corner[0]];
    }));
    const end = destinationPoint(walk);
    if (end) { line.push(end); }
    page.lookupGroup.addLayer(L.polyline(line, LOOKUP_STYLE[walk.family]));

    const name = destinationName(walk.family, walk.destination);
    if (end) {
      const mark = L.circleMarker(end, walk.family === 'metro' ? METRO_STYLE['1']
        : { radius: 5, color: '#1B5E20', weight: 2, fillColor: '#66BB6A',
            fillOpacity: 1 });
      mark.bindPopup('<strong>' + escapeHtml(name) + '</strong><br>' +
        escapeHtml(formatWalk(walk.seconds)) + ' on foot, ' +
        Math.round(walk.metres) + ' m');
      // The name goes on the map as well as in the list under the box: a reader
      // who asked what is near them should not have to open five popups to learn
      // which dot is which park. It is the arrival dot that wears it, because that
      // is the point on the park's edge the walk really reaches.
      mark.bindTooltip(name, { permanent: true, direction: 'right',
                               className: 'walk-tag' });
      page.lookupGroup.addLayer(mark);
    }
    if (walk.family === 'park') {
      addParkOutline(walk.destination);
    }
  });

  // The map is taken to the answer: the address and the five places it leads to,
  // fitted with a margin. A lookup typed while the map was somewhere else, and a
  // click at the rim of the view, both end with the whole of what was asked
  // visible at once. The zoom stops at LOOKUP_ZOOM, so an answer whose five walks
  // all end within a hundred metres is the neighbourhood, not the rooftop.
  const ends = walks.map(function (walk) {
    const end = walk.line[walk.line.length - 1] || point;
    return [end[1], end[0]];
  }).concat([at]);
  const box = L.latLngBounds(ends);
  if (box.isValid()) {
    map.fitBounds(box, { padding: [LOOKUP_PAD, LOOKUP_PAD],
                         maxZoom: LOOKUP_ZOOM });
  }

  document.getElementById('lookup-body').innerHTML = [
    '<p class="asked">' + escapeHtml(label) + '</p>',
    walkList(walks),
    shortNote(walks)
  ].join('');
}

// The park a walk ends in, drawn as the city drew it and lifted out of the
// filters: the parks layer answers to `park-minutes`, so a menu left on n/a takes
// the whole of it off the map and a line ending "in Parc La Fontaine" would end on
// a green dot with nothing green under it. The outline keeps the park's own popup,
// so clicking the shape opens the card the park would have shown. A park the city
// drew as several polygons is one feature and one layer: L.geoJSON draws every
// piece of it, and every piece answers to the same card.
function addParkOutline(number) {
  const feature = page.parks && page.parks.features[number];
  if (!feature) { return; }
  const outline = L.geoJSON(feature, { style: LOOKUP_PARK });
  outline.eachLayer(function (piece) {
    piece.bindPopup(function () { return parkPopup(feature.properties); });
  });
  page.lookupGroup.addLayer(outline);
}

// Where a walk ends on the map: the station's own point for a station, and for a
// park the point of its outline nearest the gateway the walk arrived at, which
// is the park's edge as the city drew it. The line runs from the pavement to
// that point, so the last few metres of the walk are drawn rather than assumed.
function destinationPoint(walk) {
  const last = walk.line[walk.line.length - 1];
  if (!last) { return null; }
  const family = walk.family === 'metro' ? page.metro : page.parks;
  const feature = family && family.features[walk.destination];
  if (!feature) { return null; }
  if (walk.family === 'metro') {
    return [feature.geometry.coordinates[1], feature.geometry.coordinates[0]];
  }
  return nearestOnOutline(feature.geometry, last);
}

// The nearest point of a park's outline to a corner of the network, in the order
// the map draws them. Every ring of every piece is searched: a park the city drew
// as several polygons is still one park, and its nearest edge may be on any of
// them. A park with no outline at all leaves the line ending at the corner.
function nearestOnOutline(geometry, corner) {
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates]
    : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
  let best = null;
  polygons.forEach(function (rings) {
    rings.forEach(function (ring) {
      ring.forEach(function (point) {
        const gap = Math.hypot((point[0] - corner[0]) * LON_METRES,
                               (point[1] - corner[1]) * LAT_METRES);
        if (!best || gap < best.gap) {
          best = { gap: gap, point: [point[1], point[0]] };
        }
      });
    });
  });
  return best ? best.point : [corner[1], corner[0]];
}

// The five walks as a list, shortest first, each with the time the map's lines
// cannot show and the metres the time is made of.
function walkList(walks) {
  const items = walks.map(function (walk) {
    return '<li>' + (walk.family === 'metro' ? 'Metro' : 'Park') + ': ' +
      '<b>' + escapeHtml(formatWalk(walk.seconds)) + '</b> &mdash; ' +
      escapeHtml(destinationName(walk.family, walk.destination)) +
      ' <span class="far">' + Math.round(walk.metres) + ' m</span></li>';
  });
  return '<ol>' + items.join('') + '</ol>';
}

// The one thing a list of fewer than five cannot say by itself: which of the two
// kinds came up short. A park no street comes near, or a station whose every
// entrance is off the island's network, is a destination no walk can reach, and
// saying so is better than a list that quietly holds two parks.
function shortNote(walks) {
  const held = { park: 0, metro: 0 };
  walks.forEach(function (walk) { held[walk.family] += 1; });
  const missing = [];
  if (held.park < DESTINATION_PARKS) {
    missing.push(missingWord(DESTINATION_PARKS - held.park, 'green space'));
  }
  if (held.metro < DESTINATION_STATIONS) {
    missing.push(missingWord(DESTINATION_STATIONS - held.metro, 'station'));
  }
  return missing.length
    ? '<p class="short">No walk could reach ' + escapeHtml(listOf(missing)) +
      '.</p>' : '';
}

function missingWord(count, thing) {
  return count + ' more ' + thing + (count === 1 ? '' : 's');
}

// --- Running this file without a browser ----------------------------------
//
// `node site/app.js` reads the intersections file off the disk and checks it
// without a map: the numbering, one walk per layer per corner, the build's own
// counts, and lookOf() on all 22 layers. Every check prints a line, and a
// failure sets a non-zero exit code, so a script can run this file as a test.

async function checkWithoutBrowser() {
  const fs = require('fs');
  const path = require('path');
  let checked = 0;
  let failures = 0;
  function check(what, ok, detail) {
    checked += 1;
    if (!ok) { failures += 1; }
    console.log((ok ? '  ok   ' : '  FAIL ') + what
      + (ok || !detail ? '' : '   (' + detail + ')'));
  }

  // Read the file the way the page reads it, through readWalkData(), so the
  // checks below test the page's own reading rather than a second one.
  const dots = JSON.parse(fs.readFileSync(
    path.join(__dirname, 'data', 'intersections.geojson'), 'utf8'));
  readWalkData(dots);
  const layers = walk.layers;
  const features = dots.features;
  const total = features.length;
  const ids = walk.corners;

  // A corner's walk sits at the place its question has in walk_layers, so the
  // whole column has to stay in step with that list.
  check('every corner is numbered by its place in the file',
    features.length > 0 && features.every(function (feature, at) {
      return feature.properties.id === at;
    }), total + ' corners');
  check('every corner carries a walk for each of the build\'s own layers',
    layers.length > 0 && features.every(function (feature) {
      return feature.properties.walk.length === layers.length;
    }), total + ' corners with ' + layers.length + ' walks each');
  const named = layers.map(function (layer) { return layer.id; });
  check('the build has one layer per question, and no two share a name',
    new Set(named).size === named.length && layers.every(function (layer) {
      return (layer.family === 'park' || layer.family === 'metro')
        && typeof layer.kind === 'string' && typeof layer.band === 'string'
        && typeof layer.reached === 'number' && layer.within;
    }), named.length + ' layers, from ' + named[0] + ' to ' +
    named[named.length - 1]);
  check('the file offers the times, the kinds and the size bands the filters ask in',
    dots.tick_minutes.length > 1 && dots.kinds.length > 1
    && dots.bands.length > 1 && dots.metro_kinds.length > 1
    && Number.isInteger(dots.walk_speed_mps * 100),
    dots.tick_minutes.length + ' times, ' + dots.kinds.length + ' kinds, ' +
    dots.bands.length + ' bands, ' + dots.metro_kinds.length + ' metro kinds');

  // A destination past the end of the parks or metro file would name nothing,
  // so every walk's destination is checked against the file it names, and every
  // pair must be two whole numbers: the four statements are the largest the
  // file holds, and a destination must be a feature's own number.
  const opened = { park: 'city_parks.geojson', metro: 'metro_stops.geojson' };
  const lengths = {};
  Object.keys(opened).forEach(function (family) {
    lengths[family] = JSON.parse(fs.readFileSync(
      path.join(__dirname, 'data', opened[family]), 'utf8')).features.length;
  });
  const bad = [];
  const stray = [];
  features.forEach(function (feature) {
    feature.properties.walk.forEach(function (pair, at) {
      const layer = layers[at];
      const whole = Array.isArray(pair) && pair.length === 2
        && pair.every(function (value) {
          return Number.isInteger(value) && value >= 0 && value <= 0xffff;
        });
      if (!whole) { bad.push(feature.properties.id + '/' + layer.id); }
      else if (pair[0] < walk.board.unreachable
          && pair[1] >= lengths[layer.family]) {
        stray.push(layer.id + ' on ' + feature.properties.id);
      }
    });
  });
  check('every walk is two whole numbers a corner could be drawn from',
    bad.length === 0, bad.length ? bad.slice(0, 3).join(', ')
      : total + ' corners of ' + layers.length + ' walks');
  check('every destination a walk reached is a feature of the file it names',
    stray.length === 0, stray.length ? stray.slice(0, 3).join(', ')
      : lengths.park + ' green spaces and ' + lengths.metro + ' metro points');

  // The corners the page may colour run south to north and name a borough the
  // page draws, not one of the linked cities.
  check('the corners the page may colour are numbered in order',
    ids.length > 0 && ids.every(function (number, at) {
      return number > (at ? ids[at - 1] : -1);
    }) && ids[ids.length - 1] < total,
    ids.length + ' of the ' + total + ' corners of the map');
  const boroughs = dots.coverage.boroughs;
  const names = boroughs.map(function (entry) { return entry.name; });
  check('the flag, the number and the borough name agree, corner by corner',
    features.every(function (feature) {
      return feature.properties.drawn ===
        (names.indexOf(feature.properties.borough) >= 0);
    }) && dots.coverage.focus === ids.length
    && ids.every(function (number) {
      return features[number].properties.drawn === true;
    }),
    dots.coverage.focus + ' corners flagged, ' + ids.length + ' numbered');

  // The borough names and the file's counts must agree corner by corner, or a
  // popup names a borough the corner is not in.
  const held = {};
  ids.forEach(function (number) {
    const where = features[number].properties.borough;
    held[where] = (held[where] || 0) + 1;
  });
  check('the borough counts and the corners that name them tell the same story',
    boroughs.reduce(function (sum, entry) { return sum + entry.corners; }, 0)
      === ids.length
    && boroughs.every(function (entry) {
      return entry.corners > 0 && held[entry.name] === entry.corners;
    }),
    names.length + ' boroughs drawn, ' + Object.keys(held).length +
    ' names worn: ' + boroughs.map(function (entry) {
      return entry.name + ' ' + (held[entry.name] || 0) + '/' + entry.corners;
    }).slice(0, 3).join(', '));

  // The drawn names and the linked cities are two lists with nothing in common,
  // and their corners are the whole map, so the arithmetic must close.
  const linked = new Set(features.filter(function (feature) {
    return !feature.properties.drawn;
  }).map(function (feature) {
    return feature.properties.borough;
  }));
  check('the corners the grey covers are the ones the page does not draw',
    dots.coverage.masked > 0 && dots.coverage.masked === total - ids.length
    && dots.coverage.focus === ids.length
    && dots.coverage.in_outlines + dots.coverage.outside === total
    && Array.from(linked).every(function (name) {
      return names.indexOf(name) < 0;
    }),
    dots.coverage.masked + ' of the ' + total + ' corners are in the ' +
    linked.size + ' linked cities, ' + dots.coverage.outside +
    ' are in none of the 34 outlines');

  // A sample of corners, at every time: a corner is drawn exactly when its walk
  // is inside the time asked. The fixed stride makes a failure repeatable.
  const sample = ids.filter(function (number, at) { return at % 97 === 0; });

  // Each layer's counts, worked out again through walkedOf() and compared with
  // the counts the build wrote, then read back through lookOf() on the sample:
  // a statement where a walk belongs, or a corner drawn when it should not be,
  // shows up here.
  for (let at = 0; at < layers.length; at += 1) {
    const layer = layers[at];
    // The question set up as afterChange() sets it up, so walkedOf() is the
    // page's own look-up here, not a second way of reading the file.
    const ask = { family: layer.family, layer: layer,
                  minutes: dots.tick_minutes[0] };
    walk.asked = {};
    walk.asked[layer.family] = ask;
    let outside = 0;
    let noStreet = 0;
    let reached = 0;
    const within = {};
    dots.tick_minutes.forEach(function (minute) { within[minute] = 0; });
    ids.forEach(function (number) {
      const seconds = walkedOf(number, layer.family).seconds;
      if (seconds === walk.board.outside) { outside += 1; }
      else if (seconds === walk.board.noStreet) { noStreet += 1; }
      else if (seconds < walk.board.unreachable) { reached += 1; }
      dots.tick_minutes.forEach(function (minute) {
        if (seconds <= minute * 60) { within[minute] += 1; }
      });
    });
    check(layer.id + ': the corners the page draws that it reaches',
      reached === layer.reached, reached + ' instead of ' + layer.reached);
    check(layer.id + ': the corners outside the boroughs say so',
      outside === dots.coverage.outside,
      outside + ' instead of ' + dots.coverage.outside);
    check(layer.id + ': the corners with no street say so',
      noStreet === dots.coverage.no_street,
      noStreet + ' instead of ' + dots.coverage.no_street);
    dots.tick_minutes.forEach(function (minute) {
      check(layer.id + ': the corners within ' + minute + ' minutes',
        within[minute] === layer.within[String(minute)],
        within[minute] + ' instead of ' + layer.within[String(minute)]);
      ask.minutes = minute;
      const wrong = sample.filter(function (number) {
        const seconds = walkedOf(number, layer.family).seconds;
        const drawn = lookOf(number, [layer.family]).kind === 'drawn';
        const inside = seconds <= minute * 60
          && seconds < walk.board.unreachable;
        return drawn !== inside;
      });
      check(layer.id + ': the page reads it the same way at ' + minute +
        ' minutes', wrong.length === 0,
        wrong.length + ' of ' + sample.length + ' corners differ');
    });
  }

  // A step-free entrance is an entrance, so no corner can be quicker to one
  // than to any entrance.
  const stepFree = walk.places.metro_stepfree;
  const anyEntrance = walk.places.metro_any;
  let disagreeing = 0;
  for (let number = 0; number < total; number += 1) {
    const pairs = features[number].properties.walk;
    if (pairs[stepFree][0] < pairs[anyEntrance][0]) { disagreeing += 1; }
  }
  check('no corner is quicker to a step-free entrance than to any entrance',
    disagreeing === 0, disagreeing + ' corners are');

  // Five shades per question make 25 mixed ones; each of the 35 must be a
  // colour of its own, or two answers would look the same.
  const mixes = [];
  for (let across = 0; across < WALK_SHADES; across += 1) {
    for (let down = 0; down < WALK_SHADES; down += 1) {
      mixes.push(mixColour(shadeColour('metro', across),
                           shadeColour('park', down)));
    }
  }
  const plain = WALK_RAMPS.metro.concat(WALK_RAMPS.park);
  check('the 25 mixed colours are 25 colours of their own',
    new Set(mixes).size === mixes.length,
    new Set(mixes).size + ' different colours for 25 pairs of shades');
  const used = Object.keys(PARK_STYLE).map(function (kind) {
    return PARK_STYLE[kind].fillColor;
  }).concat(Object.keys(METRO_STYLE).map(function (kind) {
    return METRO_STYLE[kind].fillColor;
  }));
  check('no walk colour is one the parks or the metro points already wear',
    plain.concat(mixes).every(function (colour) {
      return used.indexOf(colour) < 0;
    }), plain.concat(mixes).join(' ') + ' against ' + used.join(' '));

  // The bands a filter's minutes are split into cover every walk inside the
  // time that was asked for and nothing beyond it, and there are five of them,
  // which is what the legend's arithmetic rests on.
  const bands = dots.tick_minutes.map(function (minute) {
    const labels = shadeLabels(minute);
    const good = labels.length === WALK_SHADES
      && shadeOf(0, minute) === 0
      && shadeOf(minute * 60 - 1, minute) === WALK_SHADES - 1
      && shadeOf(minute * 30, minute) === 2;
    return minute + ' min ' + (good ? 'ok' : 'BAD');
  });
  check('each time asked for is five bands, and a walk inside it is in one',
    bands.join(', ').indexOf('BAD') < 0, bands.join(', ') + ': ' +
    shadeLabels(dots.tick_minutes[0]).join(', '));

  // --- The address: the graph, the street names, and the walks --------------
  //
  // These come last because they are the slow ones: the graph is 13 MB and one
  // walk visits a quarter of a million corners. The graph is read the way the
  // page reads it, through prepareGraph() and addressPoint(), so what is
  // checked is this file's own lookup rather than a second one written to
  // agree with it.

  const graph = prepareGraph(JSON.parse(fs.readFileSync(
    path.join(__dirname, 'data', 'walk_graph.json'), 'utf8')));
  const places = {
    park: JSON.parse(fs.readFileSync(
      path.join(__dirname, 'data', 'city_parks.geojson'), 'utf8')).features,
    metro: JSON.parse(fs.readFileSync(
      path.join(__dirname, 'data', 'metro_stops.geojson'), 'utf8')).features
  };

  // A destination's name, read from the file the page has in front of it rather
  // than copied out of the graph.
  function nameOf(family, number) {
    const feature = places[family][number];
    if (!feature) { return 'a place the file no longer holds'; }
    return family === 'metro' ? feature.properties.popup_name
                              : feature.properties.popup_title;
  }

  // The graph the page walks has to be the one the build measured, and the one
  // whose walking speed the page's own filter arithmetic already uses: a second
  // speed here would make one address's walk a different number of minutes from
  // the same walk read off a dot.
  check('the graph the page walks is the one the build wrote, whole',
    graph.meta.corners === graph.nodes.length
    && graph.meta.links === graph.links.length
    && graph.nodes.length > 0 && graph.links.length > 0
    && graph.links.every(function (link) {
      return link[2] > 0 && link[0] >= 0 && link[1] >= 0;
    })
    && graph.meta.walk_speed_mps === dots.walk_speed_mps,
    graph.nodes.length + ' corners, ' + graph.links.length + ' links, ' +
    graph.names.length + ' street names at ' + graph.meta.walk_speed_mps +
    ' m/s');

  // A street the file names has to be findable by the name it gives it. The
  // stride keeps the check quick and repeatable: a name that cannot be looked
  // up is a street no reader can reach at all.
  const unfound = [];
  graph.groups.forEach(function (group, at) {
    if (at % 149 !== 0) { return; }
    const name = graph.names[group.names[0]];
    const street = findStreet(graph, name);
    if (!street.found || street.index !== at) { unfound.push(name); }
  });
  check('every street the file names is found again by its own name',
    unfound.length === 0, unfound.slice(0, 3).join(', '));

  // Twenty addresses, spread over the island, each with the answer it should
  // get. Four of them are refusals a reader really produces: a street of
  // another city, a number this street does not have, and a line that names a
  // street but no number on it.
  const probes = [
    { text: '4250 Rue Sherbrooke Est', kind: 'found' },
    { text: '4252 Rue Sherbrooke', kind: 'found' },
    { text: '4250 rue Sherbrook est', kind: 'found' },
    { text: '1435 Rue Saint-Denis', kind: 'found' },
    { text: '1000 Rue de la Gauchetière Ouest', kind: 'found' },
    { text: '1 Place Ville-Marie', kind: 'found' },
    { text: '4400 Boulevard Pie-IX', kind: 'found' },
    { text: '200 Rue Sainte-Catherine Ouest', kind: 'found' },
    { text: '5000 Rue Sherbrooke Ouest', kind: 'found' },
    { text: '4250 Rue Sherbrooke Ouest', kind: 'found' },
    { text: '1500 Avenue du Mont-Royal Est', kind: 'found' },
    { text: '2000 Boulevard Saint-Laurent', kind: 'found' },
    { text: '900 Boulevard de Maisonneuve Est', kind: 'found' },
    { text: '3000 Rue Sherbrooke Est', kind: 'found' },
    { text: '1250 Rue Sainte-Catherine Est', kind: 'found' },
    { text: '6000 Boulevard Décarie', kind: 'found' },
    { text: '5200 Chemin de la Côte-des-Neiges', kind: 'found' },
    { text: '1000 Boulevard Curé-Labelle', kind: 'no-street' },
    { text: '845 Avenue McGill College', kind: 'no-block' },
    { text: 'Rue Sherbrooke Est', kind: 'no-number' }
  ];

  const wrongKind = [];
  const broken = [];
  probes.forEach(function (probe) {
    const placed = addressPoint(graph, probe.text);
    probe.placed = placed;
    if (placed.kind !== probe.kind) {
      wrongKind.push(probe.text + ' gave ' + placed.kind);
      return;
    }
    if (placed.kind !== 'found') { return; }
    const found = lookupAt(graph, placed.point);
    probe.found = found;
    if (found.kind !== 'found') {
      broken.push(probe.text + ' could not be walked from at all');
      return;
    }
    // The number is placed on a troncon whose two ends are corners of the
    // network, so the point stands on the network already and the snap has
    // nothing to do. A snap of any size means the placement and the network
    // have drifted apart, and every walk from it would start somewhere else.
    // The number is placed on the straight line between the troncon's two
    // ends, while the street itself may bend between them, so the point can
    // stand a few metres off the pavement - four metres on the worst address
    // tried, which is a bend, not a mistake. The snap is what takes the point
    // back onto the road, and anything that needed more than fifteen metres of
    // it would be a placement on the wrong block rather than a bend.
    if (!(found.snap.metres <= 15)) {
      broken.push(probe.text + ' stands ' + found.snap.metres.toFixed(1) +
                  ' m off the street it was placed on');
    }
    const families = { park: [], metro: [] };
    found.walks.forEach(function (walk) {
      families[walk.family].push(walk.metres);
      if (!(walk.metres > 0) || !isFinite(walk.seconds)) {
        broken.push(probe.text + ' has a walk of ' + formatWalk(walk.seconds));
      }
      if (!walk.line.length) {
        broken.push(probe.text + ' has no line to ' +
                    nameOf(walk.family, walk.destination));
      }
    });
    // Nearest first, family by family: the list is read as "the closest
    // three", so a shorter walk listed after a longer one would be a search
    // that stopped early rather than one that finished.
    ['park', 'metro'].forEach(function (family) {
      for (let at = 1; at < families[family].length; at += 1) {
        if (families[family][at] < families[family][at - 1]) {
          broken.push(probe.text + ' lists its ' + family + ' walks out of order');
        }
      }
    });
    if (families.park.length !== DESTINATION_PARKS
        || families.metro.length !== DESTINATION_STATIONS) {
      broken.push(probe.text + ' found ' + families.park.length +
                  ' green spaces and ' + families.metro.length + ' stations');
    }
  });

  check('every address is answered as it should be, or refused for its own reason',
    wrongKind.length === 0,
    wrongKind.length ? wrongKind.slice(0, 4).join('; ')
      : probes.length + ' addresses, ' + probes.filter(function (probe) {
        return probe.kind === 'found';
      }).length + ' of them walked from');
  check('every address that resolves walks to three green spaces and two stations',
    broken.length === 0, broken.slice(0, 3).join('; '));

  // A name with a letter missing finds the street it meant, and says that it
  // guessed: the reader can see the spelling the city uses.
  const typed = addressPoint(graph, '4250 Rue Sherbrooke Est');
  const slipped = addressPoint(graph, '4250 rue Sherbrook est');
  check('a name with a letter missing finds the street it meant, and says so',
    typed.kind === 'found' && slipped.kind === 'found'
    && slipped.name === typed.name && slipped.how === 'fuzzy',
    slipped.kind === 'found' ? slipped.name + ', by ' + slipped.how
                             : slipped.kind);

  // A click is answered from wherever it lands on the network, and a point
  // nowhere near a street is refused rather than snapped to the nearest one
  // however far off that is.
  const walked = probes.filter(function (probe) { return probe.found; });
  check('a point forty metres beside an address still stands on a street',
    walked.length > 0 && lookupAt(graph, [walked[0].placed.point[0] +
      40 / LON_METRES, walked[0].placed.point[1]]).kind === 'found',
    walked.length ? 'from ' + walked[0].text : 'nothing was found to stand beside');

  let west = graph.nodes[0][0];
  let south = graph.nodes[0][1];
  graph.nodes.forEach(function (point) {
    if (point[0] < west) { west = point[0]; }
    if (point[1] < south) { south = point[1]; }
  });
  const nowhere = lookupAt(graph, [west - 0.02, south - 0.02]);
  check('a point two kilometres from every street is refused, not snapped',
    nowhere.kind === 'no-street', nowhere.kind);

  // Photon's answer, as the page reads it: a name counts only when it is a
  // street inside Montreal, and a name the answer repeats counts once. The
  // coordinates in the same answer are never read at all - which is the whole of
  // what the page is allowed to take from a service that cannot place an address
  // as well as the city's own file can.
  const reSpelling = namesOfPhoton({ features: [
    { properties: { name: 'Rue Saint-Denis', osm_key: 'highway',
                    city: 'Montréal', housenumber: '1435' } },
    { properties: { name: 'Rue Saint-Denis', osm_key: 'highway',
                    city: 'Montréal' } },
    { properties: { name: 'Boulevard Curé-Labelle', osm_key: 'highway',
                    city: 'Laval' } },
    { properties: { name: 'Montréal', osm_key: 'place', city: 'Montréal' } },
    { properties: { name: 'Rue Sherbrooke', osm_key: 'highway',
                    city: 'Montréal' } }
  ] });
  check('a stranger is believed about Montreal street names, and only those',
    reSpelling.length === 2 && reSpelling[0] === 'Rue Saint-Denis'
    && reSpelling[1] === 'Rue Sherbrooke',
    reSpelling.length + ' names kept: ' + reSpelling.join(', '));

  // What the five walks look like, for two addresses a reader would know: the
  // street the lookup settled on, how it settled on it, and each destination
  // with its walk. Eyeballing these against a map is the check no assertion
  // here can make.
  function pad(text, width) {
    let out = String(text);
    while (out.length < width) { out += ' '; }
    return out;
  }

  function show(probe) {
    if (!probe || !probe.found) {
      console.log('  ' + (probe ? probe.text + ' -> ' + probe.placed.kind
                                : 'nothing to show'));
      return;
    }
    console.log('  ' + probe.text + '   [' + probe.placed.name + ', ' +
      probe.placed.how + ']');
    probe.found.walks.forEach(function (walk) {
      console.log('     ' + pad(walk.family === 'metro' ? 'metro' : 'park', 6) +
        pad(formatWalk(walk.seconds), 15) +
        pad(Math.round(walk.metres) + ' m', 9) +
        nameOf(walk.family, walk.destination));
    });
  }

  ['4250 Rue Sherbrooke Est', '1435 Rue Saint-Denis'].forEach(function (text) {
    show(probes.filter(function (probe) { return probe.text === text; })[0]);
  });

  // The page's markup has to hold the boxes the lookup types into and writes
  // into: a rename in index.html would otherwise leave a lookup that does
  // nothing at all, and nothing here would say so.
  const markup = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
  const wanted = ['lookup-form', 'lookup-text', 'lookup-body'];
  const absent = wanted.filter(function (id) {
    return markup.indexOf("id='" + id + "'") < 0
      && markup.indexOf('id="' + id + '"') < 0;
  });
  check('the page holds the box the lookup is typed into, and the one it writes in',
    absent.length === 0, absent.join(', '));

  // --- The answer as drawn --------------------------------------------------
  //
  // drawLookup() is the one part of the lookup that otherwise needs a browser to
  // see, and the one place a pair of numbers in the wrong order would draw five
  // lines into the Atlantic without failing anything else. So Leaflet and the
  // document are stood in for here: the shapes the function asks for are
  // recorded, and what it asked for is checked - five lines, the address at the
  // head of each, a dot at the end of each to click, and five walks listed under
  // the box.
  //
  // The stand-ins go in last, so nothing after them sees a map that is not
  // Leaflet's, and the page's own two destination files are handed to it so that
  // a park's name and outline are the real ones.
  (function () {
    const drawn = [];
    const box = { innerHTML: '' };
    const realDocument = global.document;
    global.document = {
      getElementById: function (id) { return id === 'lookup-body' ? box : null; }
    };
    global.L = {
      circleMarker: function (at, style) {
        const dot = { kind: 'dot', at: at, style: style, popup: '', tooltip: '',
                      bindPopup: function (html) { dot.popup = html; },
                      bindTooltip: function (text) { dot.tooltip = text; } };
        return dot;
      },
      polyline: function (line, style) {
        return { kind: 'line', line: line, style: style };
      },
      // A park's outline: the pieces Leaflet would make from the feature's
      // polygons, each of which the page dresses with the park's card. One piece
      // here, which is what most parks are.
      geoJSON: function (feature, options) {
        const piece = {
          kind: 'piece', popup: '',
          bindPopup: function (html) { piece.popup = html; }
        };
        return { kind: 'outline', feature: feature, style: options.style,
                 pieces: [piece],
                 eachLayer: function (visit) { [piece].forEach(visit); } };
      },
      latLngBounds: function (points) {
        return { points: points,
                 isValid: function () { return points.length > 0; } };
      }
    };
    const realMap = map;
    const fits = [];
    map = { fitBounds: function (bounds, options) {
      fits.push({ points: bounds.points, options: options });
    } };
    const realMetro = page.metro;
    const realParks = page.parks;
    const realGroup = page.lookupGroup;
    page.metro = { features: places.metro };
    page.parks = { features: places.park };
    page.lookupGroup = {
      clearLayers: function () { drawn.length = 0; },
      addLayer: function (layer) { drawn.push(layer); }
    };

    const probe = probes.filter(function (each) {
      return each.text === '1435 Rue Saint-Denis';
    })[0];
    const origin = [probe.placed.point[1], probe.placed.point[0]];
    drawLookup(probe.placed.point, probe.found, '1435 Rue Saint-Denis', 'exact');

    const lines = drawn.filter(function (layer) { return layer.kind === 'line'; });
    const dots = drawn.filter(function (layer) { return layer.kind === 'dot'; });
    check('the answer is drawn as one line per destination, from the address',
      lines.length === DESTINATION_PARKS + DESTINATION_STATIONS,
      lines.length + ' lines');
    check('every line starts at the address it was asked about',
      lines.length > 0 && lines.every(function (layer) {
        return layer.line[0][0] === origin[0] && layer.line[0][1] === origin[1];
      }), JSON.stringify(lines.length ? lines[0].line[0] : null) + ' against ' +
      JSON.stringify(origin));
    check('every line ends on a dot the reader can click',
      lines.length > 0
      && dots.length === 1 + DESTINATION_PARKS + DESTINATION_STATIONS
      && lines.every(function (layer) {
        const end = layer.line[layer.line.length - 1];
        return dots.some(function (dot) {
          return dot.at[0] === end[0] && dot.at[1] === end[1];
        });
      }), lines.length + ' lines, ' + dots.length + ' dots');
    check('the box under the field lists the same five walks',
      (box.innerHTML.match(/<li>/g) || []).length ===
        DESTINATION_PARKS + DESTINATION_STATIONS
      && box.innerHTML.indexOf('Rue Saint-Denis') >= 0,
      (box.innerHTML.match(/<li>/g) || []).length + ' walks listed');

    // The five places the list names have to be findable on the map itself: each
    // park as the shape the city drew (with its own card, so a click on it still
    // answers) and each destination wearing its name.
    const outlines = drawn.filter(function (layer) { return layer.kind === 'outline'; });
    check('every park the answer names is drawn as its own shape, with its card',
      outlines.length === DESTINATION_PARKS && outlines.every(function (layer) {
        return layer.feature && typeof layer.pieces[0].popup === 'function'
          && String(layer.pieces[0].popup()).indexOf('Size:') >= 0;
      }), outlines.length + ' outlines drawn, ' + drawn.length + ' layers');

    const tags = drawn.reduce(function (all, layer) {
      const each = layer.kind === 'outline' ? layer.pieces : [layer];
      return all.concat(each.filter(function (one) {
        return one.tooltip && one.tooltip.length > 0;
      }).map(function (one) { return one.tooltip; }));
    }, []);
    check('the five destinations wear their names on the map, and no name twice',
      tags.length === DESTINATION_PARKS + DESTINATION_STATIONS
      && new Set(tags).size === tags.length, tags.join(' / '));

    check('the map is fitted to the address and its five walks, no closer than ' +
      'LOOKUP_ZOOM',
      fits.length === 1 && fits[0].options.maxZoom === LOOKUP_ZOOM
      && fits[0].options.padding[0] === LOOKUP_PAD
      && fits[0].points.length === 1 + DESTINATION_PARKS + DESTINATION_STATIONS
      && fits[0].points.some(function (at) {
        return at[0] === origin[0] && at[1] === origin[1];
      }), JSON.stringify(fits.length ? fits[0].options : null) + ' on ' +
      (fits.length ? fits[0].points.length : 0) + ' points');

    // A refusal has to take the last answer off the map as well as say why: a
    // reader who then asks about a street that does not exist must not be left
    // with five lines to parks they asked about a moment before.
    const before = drawn.length;
    sayLookup('No street by that name in the street file.');
    check('a refusal clears the last answer off the map, and says why',
      before > 0 && drawn.length === 0
      && box.innerHTML.indexOf('No street by that name') >= 0,
      before + ' layers drawn, ' + drawn.length + ' left, and the box says ' +
      (box.innerHTML.indexOf('No street by that name') >= 0 ? 'why' : 'nothing'));

    global.document = realDocument;
    global.L = undefined;
    map = realMap;
    page.metro = realMetro;
    page.parks = realParks;
    page.lookupGroup = realGroup;
  })();

  console.log(failures
    ? failures + ' of ' + checked + ' checks failed'
    : 'all ' + checked + ' checks passed');
  process.exitCode = failures ? 1 : 0;
}

// The page is built when there is a document to build it into; run this file
// with node and there is not, so it checks itself instead.
if (typeof document === 'undefined') {
  checkWithoutBrowser();
} else {
  start();
}
