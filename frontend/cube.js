/* Cube drawing from the 54-character facelet string.
 * The facelet order and the coordinates are the same as in app/cube/engine.py.
 * Two views: a 3D (isometric) view that shows U, F and R, and an unfolded net with all six faces.
 */
(function () {
  'use strict';

  var FACES = 'URFDLB';
  var COLORS = { U: '#F6F6F2', R: '#C8282D', F: '#2F9E50', D: '#F6C600', L: '#F07F00', B: '#1E5FB5' };
  var NAMES = { U: 'white', R: 'red', F: 'green', D: 'yellow', L: 'orange', B: 'blue' };

  // (cubie position, sticker direction) for every facelet index
  var COORDS = (function () {
    var out = [];
    for (var f = 0; f < 6; f++) {
      for (var i = 0; i < 9; i++) {
        var r = Math.floor(i / 3), c = i % 3, p, n;
        switch (f) {
          case 0: p = [c - 1, 1, r - 1]; n = [0, 1, 0]; break;      // U
          case 1: p = [1, 1 - r, 1 - c]; n = [1, 0, 0]; break;      // R
          case 2: p = [c - 1, 1 - r, 1]; n = [0, 0, 1]; break;      // F
          case 3: p = [c - 1, -1, 1 - r]; n = [0, -1, 0]; break;    // D
          case 4: p = [-1, 1 - r, c - 1]; n = [-1, 0, 0]; break;    // L
          default: p = [1 - c, 1 - r, -1]; n = [0, 0, -1];          // B
        }
        out.push({ p: p, n: n });
      }
    }
    return out;
  })();

  var S = 34; // scale of the 3D view
  function proj(v) {
    return [(v[0] - v[2]) * 0.8660254 * S, ((v[0] + v[2]) * 0.5 - v[1]) * S];
  }
  function fmt(pts) {
    return pts.map(function (q) { return q[0].toFixed(1) + ',' + q[1].toFixed(1); }).join(' ');
  }
  function quad(p, n) {
    var c = [p[0] + n[0] * 0.5, p[1] + n[1] * 0.5, p[2] + n[2] * 0.5];
    var axes = [0, 1, 2].filter(function (a) { return n[a] === 0; });
    var h = 0.44;
    return [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(function (ab) {
      var v = c.slice();
      v[axes[0]] += ab[0] * h;
      v[axes[1]] += ab[1] * h;
      return proj(v);
    });
  }

  var HEX = fmt([[-1.5, 1.5, -1.5], [1.5, 1.5, -1.5], [1.5, -1.5, -1.5], [1.5, -1.5, 1.5], [-1.5, -1.5, 1.5], [-1.5, 1.5, 1.5]].map(proj));
  var QUADS = COORDS.map(function (c) { return fmt(quad(c.p, c.n)); });

  function iso(facelets) {
    var s = '<svg class="cube-svg" viewBox="-96 -108 192 216" role="img" aria-label="Cube, 3D view">';
    s += '<polygon points="' + HEX + '" fill="#161b20"/>';
    for (var i = 0; i < 27; i++) { // faces U, R, F are the ones we can see
      s += '<polygon points="' + QUADS[i] + '" fill="' + COLORS[facelets[i]] + '"/>';
    }
    return s + '</svg>';
  }

  var NET_OFFSETS = { U: [3, 0], L: [0, 3], F: [3, 3], R: [6, 3], B: [9, 3], D: [3, 6] };
  function net(facelets) {
    var cell = 16, gap = 1.2;
    var s = '<svg class="cube-svg net" viewBox="0 0 ' + (12 * cell) + ' ' + (9 * cell) + '" role="img" aria-label="Cube, unfolded net">';
    for (var f = 0; f < 6; f++) {
      var face = FACES[f], off = NET_OFFSETS[face];
      s += '<rect x="' + (off[0] * cell - 0.6) + '" y="' + (off[1] * cell - 0.6) + '" width="' + (3 * cell + 1.2) + '" height="' + (3 * cell + 1.2) + '" fill="#161b20" rx="2"/>';
      for (var i = 0; i < 9; i++) {
        var r = Math.floor(i / 3), c = i % 3;
        s += '<rect x="' + ((off[0] + c) * cell + gap) + '" y="' + ((off[1] + r) * cell + gap) + '" width="' + (cell - 2 * gap) + '" height="' + (cell - 2 * gap) + '" rx="1.5" fill="' + COLORS[facelets[f * 9 + i]] + '"/>';
      }
    }
    return s + '</svg>';
  }

  window.CubeView = { iso: iso, net: net, COLORS: COLORS, NAMES: NAMES };
})();
