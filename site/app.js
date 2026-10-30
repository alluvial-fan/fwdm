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
      wireFilters();
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
               metros: null, walkGroup: null, mask: null };

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
