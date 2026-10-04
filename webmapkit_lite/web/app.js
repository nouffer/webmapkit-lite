(function() {
  "use strict";
  var BASEMAPS = {
    positron: "https://tiles.openfreemap.org/styles/positron",
    liberty: "https://tiles.openfreemap.org/styles/liberty",
    bright: "https://tiles.openfreemap.org/styles/bright"
  };
  var FALLBACK_GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
  var LABEL_FONT = [ "Noto Sans Regular" ];
  var PALETTE = [ "#2f6fb0", "#d1495b", "#2a9d8f", "#e9a23b", "#7b5ea7", "#3f8f4f", "#c2571a", "#5c6b7a" ];
  var NONE = [ "==", [ "get", "__wm_never__" ], "__wm_never__" ];
  var ICONS = window.WEBMAP_ICONS || {
    dot: [ {
      d: "M7 12a5 5 0 1 0 10 0a5 5 0 1 0 -10 0z",
      fill: true
    } ]
  };
  var $ = function(id) {
    return document.getElementById(id);
  };
  var el = function(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  var isSmall = function() {
    return window.matchMedia("(max-width: 640px)").matches;
  };
  function notice(msg, ms) {
    var n = $("notice");
    n.textContent = msg;
    n.hidden = false;
    clearTimeout(notice.t);
    if (ms) notice.t = setTimeout(function() {
      n.hidden = true;
    }, ms);
  }
  function getJSON(url, timeoutMs) {
    var ctrl = new AbortController;
    var t = setTimeout(function() {
      ctrl.abort();
    }, timeoutMs || 1e4);
    return fetch(url, {
      signal: ctrl.signal
    }).then(function(r) {
      clearTimeout(t);
      if (!r.ok) throw new Error(url + " returned " + r.status);
      return r.json();
    });
  }
  function isPMTiles(url) {
    return /\.pmtiles(\?|$)/i.test(url);
  }
  function absolute(url) {
    return new URL(url, location.href).href;
  }
  function hexToRgb(hex) {
    var h = String(hex || "").replace("#", "");
    if (h.length === 3) h = h.split("").map(function(c) {
      return c + c;
    }).join("");
    var n = parseInt(h, 16);
    return isNaN(n) ? [ 100, 100, 100 ] : [ n >> 16 & 255, n >> 8 & 255, n & 255 ];
  }
  function luminance(hex) {
    var c = hexToRgb(hex).map(function(v) {
      v /= 255;
      return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4);
    });
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2];
  }
  function shade(hex, amt) {
    var c = hexToRgb(hex).map(function(v) {
      return Math.round(amt < 0 ? v * (1 + amt) : v + (255 - v) * amt);
    });
    return "#" + c.map(function(v) {
      return ("0" + Math.max(0, Math.min(255, v)).toString(16)).slice(-2);
    }).join("");
  }
  function iconInk(color) {
    return luminance(color) > .55 ? "#1d2733" : "#ffffff";
  }
  function layerDefaults(layer, index) {
    var s = layer.style = layer.style || {};
    if (!s.kind) s.kind = s.categories ? "categorized" : s.ranges ? "graduated" : "single";
    if (s.kind === "single" && !s.color) s.color = PALETTE[index % PALETTE.length];
    (s.categories || []).forEach(function(c, i) {
      if (!c.color) c.color = PALETTE[i % PALETTE.length];
    });
    if (layer.type === "point" && s.radiusBy) s.marker = "circle";
    if (layer.type === "point") s.marker = s.marker || (s.icon || (s.categories || []).some(function(c) {
      return c.icon;
    }) ? "badge" : "circle");
    if (s.kind === "graduated" && s.field && (s.ranges || []).length) {
      var lo = Math.min.apply(null, s.ranges.map(function(r) {
        return Number(r.min);
      }));
      var hi = Math.max.apply(null, s.ranges.map(function(r) {
        return Number(r.max);
      }));
      var v = [ "to-number", [ "get", s.field ], NaN ];
      var f = [ "all", [ "has", s.field ], [ "!=", [ "get", s.field ], "" ], [ ">=", v, lo ], [ "<=", v, hi ] ];
      layer._styleFilter = f;
    }
    if (s.kind === "categorized" && s.field && (s.categories || []).length) {
      var key = [ "to-string", [ "get", s.field ] ], cf = null;
      if (!s.defaultColor) cf = [ "in", key, [ "literal", s.categories.map(function(c) {
        return String(c.value);
      }) ] ];
      if ((s.hideValues || []).length) {
        var hv = [ "!", [ "in", key, [ "literal", s.hideValues.map(String) ] ] ];
        cf = cf ? [ "all", cf, hv ] : hv;
      }
      if (cf) layer._styleFilter = cf;
    }
    layer._drawFilter = layer.filter && layer._styleFilter ? [ "all", layer.filter, layer._styleFilter ] : layer.filter || layer._styleFilter || null;
    return layer;
  }
  function colorExpr(s) {
    var fallback = s.defaultColor || s.color || "#8792a2";
    if (s.kind === "categorized" && s.field && (s.categories || []).length) {
      var m = [ "match", [ "to-string", [ "get", s.field ] ] ], seen = {};
      s.categories.forEach(function(c) {
        var v = String(c.value);
        if (seen[v]) return;
        seen[v] = true;
        m.push(v, c.color);
      });
      m.push(fallback);
      return m;
    }
    if (s.kind === "graduated" && s.field && (s.ranges || []).length) {
      var r = s.ranges.slice().sort(function(a, b) {
        return a.min - b.min;
      });
      var e = [ "step", [ "to-number", [ "get", s.field ], -1e308 ], r[0].color ];
      for (var i = 1; i < r.length; i++) e.push(Number(r[i].min), r[i].color);
      return e;
    }
    return fallback;
  }
  function featureLook(layer, props) {
    var s = layer.style, v = s.field != null ? props[s.field] : undefined;
    var look = {
      color: s.defaultColor || s.color || "#8792a2",
      icon: s.icon || "dot",
      label: null
    };
    if (layer.type === "polygon" && s.kind === "single" && s.opacity === 0 && s.outline) look.color = s.outline;
    if (s.kind === "categorized") {
      var c = (s.categories || []).filter(function(c) {
        return String(c.value) === String(v);
      })[0];
      if (c) {
        look.color = c.color;
        look.icon = c.icon || look.icon;
        look.label = c.label != null ? c.label : c.value;
      }
    } else if (s.kind === "graduated") {
      var r = (s.ranges || []).slice().sort(function(a, b) {
        return a.min - b.min;
      });
      for (var i = 0; i < r.length; i++) if (Number(v) >= r[i].min) {
        look.color = r[i].color;
        look.label = r[i].label || null;
      }
    }
    return look;
  }
  function zoomScaled(v) {
    return [ "interpolate", [ "linear" ], [ "zoom" ], 8, v * .55, 13, v, 18, v * 1.6 ];
  }
  function sizeExpr(s, key, fallback, add) {
    add = add || 0;
    if (key === "radius" && s.radiusBy && s.radiusBy.field) {
      var rb = s.radiusBy, span = rb.max - rb.min || 1;
      var t = [ "/", [ "-", [ "min", [ "max", [ "to-number", [ "get", rb.field ], rb.min ], rb.min ], rb.max ], rb.min ], span ];
      var minR = Math.max(rb.minRadius, 2.5);
      var r = [ "+", minR + add, [ "*", Math.max(rb.maxRadius - minR, 1), [ "^", t, rb.exponent || .5 ] ] ];
      return [ "interpolate", [ "linear" ], [ "zoom" ], 2, [ "*", r, .6 ], 5, r, 8, [ "*", r, 1.6 ], 11, [ "*", r, 2.4 ], 15, [ "*", r, 3.2 ] ];
    }
    var base = s[key] != null ? s[key] : fallback;
    if (s.kind === "graduated" && s.field && (s.ranges || []).some(function(r) {
      return r[key] != null;
    })) {
      var rs = s.ranges.slice().sort(function(x, y) {
        return x.min - y.min;
      });
      var st = [ "step", [ "to-number", [ "get", s.field ], -1e308 ], (rs[0][key] != null ? rs[0][key] : base) + add ];
      for (var j = 1; j < rs.length; j++) st.push(Number(rs[j].min), (rs[j][key] != null ? rs[j][key] : base) + add);
      return [ "interpolate", [ "linear" ], [ "zoom" ], 8, [ "*", st, .55 ], 13, st, 18, [ "*", st, 1.6 ] ];
    }
    var cats = s.kind === "categorized" && s.field ? (s.categories || []).filter(function(c) {
      return c[key] != null;
    }) : [];
    if (!cats.length) return zoomScaled(base + add);
    var m = [ "match", [ "to-string", [ "get", s.field ] ] ], seen = {};
    cats.forEach(function(c) {
      var v = String(c.value);
      if (seen[v]) return;
      seen[v] = true;
      m.push(v, c[key] + add);
    });
    m.push(base + add);
    return [ "interpolate", [ "linear" ], [ "zoom" ], 8, [ "*", m, .55 ], 13, m, 18, [ "*", m, 1.6 ] ];
  }
  function drawIcon(ctx, name, x, y, size, ink) {
    var parts = ICONS[name] || ICONS.dot, k = size / 24;
    ctx.save();
    ctx.translate(x - size / 2, y - size / 2);
    ctx.scale(k, k);
    parts.forEach(function(p) {
      var path = new Path2D(p.d);
      if (p.fill) {
        ctx.fillStyle = ink;
        ctx.fill(path, p.evenodd ? "evenodd" : "nonzero");
      }
      if (p.stroke) {
        ctx.strokeStyle = ink;
        ctx.lineWidth = p.stroke;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.stroke(path);
      }
    });
    ctx.restore();
  }
  var MARKERS = {
    badge: {
      w: 30,
      h: 30,
      cx: 15,
      cy: 14,
      icon: 14,
      anchor: "center",
      shape: function() {
        var p = new Path2D;
        p.arc(15, 14, 11, 0, Math.PI * 2);
        return p;
      }
    },
    pin: {
      w: 32,
      h: 42,
      cx: 16,
      cy: 15,
      icon: 15,
      anchor: "bottom",
      shape: function() {
        return new Path2D("M16 40C16 40 4 26.5 4 15A12 12 0 1 1 28 15C28 26.5 16 40 16 40Z");
      }
    }
  };
  function markerImageName(kind, color, icon) {
    return "wm-" + kind + "-" + icon + "-" + color.replace("#", "");
  }
  function addMarkerImage(map, kind, color, icon) {
    var name = markerImageName(kind, color, icon);
    if (map.hasImage(name)) return name;
    var m = MARKERS[kind], ratio = 2;
    var canvas = document.createElement("canvas");
    canvas.width = m.w * ratio;
    canvas.height = m.h * ratio;
    var ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    var shape = m.shape();
    ctx.save();
    ctx.shadowColor = "rgba(15, 23, 42, .35)";
    ctx.shadowBlur = 3;
    ctx.shadowOffsetY = 1;
    ctx.fillStyle = color;
    ctx.fill(shape);
    ctx.restore();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke(shape);
    drawIcon(ctx, icon, m.cx, m.cy, m.icon, iconInk(color));
    map.addImage(name, ctx.getImageData(0, 0, canvas.width, canvas.height), {
      pixelRatio: ratio
    });
    return name;
  }
  function markerSVG(kind, color, icon, size) {
    var ink = iconInk(color), parts = ICONS[icon] || ICONS.dot, m = MARKERS[kind] || MARKERS.badge;
    var paths = parts.map(function(p) {
      return '<path d="' + p.d + '"' + (p.fill ? ' fill="' + ink + '"' : ' fill="none"') + (p.evenodd ? ' fill-rule="evenodd"' : "") + (p.stroke ? ' stroke="' + ink + '" stroke-width="' + p.stroke + '" stroke-linecap="round" stroke-linejoin="round"' : "") + "/>";
    }).join("");
    var body = kind === "pin" ? '<path d="M16 40C16 40 4 26.5 4 15A12 12 0 1 1 28 15C28 26.5 16 40 16 40Z" fill="' + color + '" stroke="#fff" stroke-width="2"/>' : '<circle cx="15" cy="14" r="11" fill="' + color + '" stroke="#fff" stroke-width="2"/>';
    var k = m.icon / 24;
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + m.w + " " + m.h + '" width="' + (size || m.w) + '" height="' + Math.round((size || m.w) * m.h / m.w) + '" aria-hidden="true">' + body + '<g transform="translate(' + (m.cx - m.icon / 2) + " " + (m.cy - m.icon / 2) + ") scale(" + k + ')">' + paths + "</g></svg>";
  }
  function blankStyle() {
    return {
      version: 8,
      glyphs: FALLBACK_GLYPHS,
      sources: {},
      layers: [ {
        id: "background",
        type: "background",
        paint: {
          "background-color": "#f3f2ee"
        }
      } ]
    };
  }
  function loadBasemap(name) {
    if (!name || name === "none") return Promise.resolve(blankStyle());
    return getJSON(BASEMAPS[name] || name, 8e3).then(function(style) {
      if (!style.glyphs) style.glyphs = FALLBACK_GLYPHS;
      return style;
    }).catch(function(err) {
      console.warn("Basemap failed to load, using a plain background.", err);
      notice("Basemap unavailable, showing your data on a plain background.", 6e3);
      return blankStyle();
    });
  }
  function firstLabelLayer(map) {
    var layers = map.getStyle().layers || [];
    for (var i = 0; i < layers.length; i++) if (layers[i].type === "symbol") return layers[i].id;
    return undefined;
  }
  function geomFilter(type) {
    var t = type === "polygon" ? [ "Polygon", "MultiPolygon" ] : type === "line" ? [ "LineString", "MultiLineString" ] : [ "Point", "MultiPoint" ];
    return [ "in", [ "geometry-type" ], [ "literal", t ] ];
  }
  function combine(a, b) {
    return b ? [ "all", a, b ] : a;
  }
  function buildLayers(map, layer, accent) {
    var s = layer.style, color = colorExpr(s), vis = layer.visible === false ? "none" : "visible";
    var base = {
      source: layer._source
    };
    if (!layer.geojson) base["source-layer"] = layer.sourceLayer || layer.id;
    if (layer.minzoom != null) base.minzoom = layer.minzoom;
    if (layer.maxzoom != null) base.maxzoom = layer.maxzoom;
    var gf = combine(geomFilter(layer.type), layer._drawFilter);
    var mk = function(suffix, type, extra) {
      return Object.assign({}, base, {
        id: layer.id + "__" + suffix,
        type: type,
        filter: gf
      }, extra);
    };
    var L = {
      main: [],
      hover: null,
      select: null,
      labels: []
    };
    var opacity = s.opacity != null ? s.opacity : .7;
    if (layer.type === "polygon") {
      L.main.push(mk("fill", "fill", {
        layout: {
          visibility: vis
        },
        paint: {
          "fill-color": color,
          "fill-opacity": opacity
        }
      }));
      if (s.outline !== "none") {
        L.main.push(mk("outline", "line", {
          layout: {
            visibility: vis,
            "line-join": "round"
          },
          paint: {
            "line-color": s.outline || "#ffffff",
            "line-width": zoomScaled(s.outlineWidth != null ? s.outlineWidth : 1),
            "line-opacity": .9
          }
        }));
      }
      L.hover = [ mk("hover", "fill", {
        filter: NONE,
        layout: {
          visibility: vis
        },
        paint: {
          "fill-color": "#ffffff",
          "fill-opacity": .22
        }
      }), mk("hoverline", "line", {
        filter: NONE,
        layout: {
          visibility: vis,
          "line-join": "round"
        },
        paint: {
          "line-color": accent,
          "line-width": 1.5,
          "line-opacity": .7
        }
      }) ];
      L.select = [ mk("selcase", "line", {
        filter: NONE,
        layout: {
          visibility: vis,
          "line-join": "round"
        },
        paint: {
          "line-color": "#ffffff",
          "line-width": 6
        }
      }), mk("sel", "line", {
        filter: NONE,
        layout: {
          visibility: vis,
          "line-join": "round"
        },
        paint: {
          "line-color": accent,
          "line-width": 3
        }
      }) ];
    } else if (layer.type === "line") {
      var w = s.width != null ? s.width : 2.5;
      if (s.casing !== "none") {
        L.main.push(mk("casing", "line", {
          layout: {
            visibility: vis,
            "line-cap": "round",
            "line-join": "round"
          },
          paint: {
            "line-color": s.casing || "#ffffff",
            "line-width": sizeExpr(s, "width", 2.5, 2),
            "line-opacity": .9
          }
        }));
      }
      L.main.push(mk("line", "line", {
        layout: {
          visibility: vis,
          "line-cap": "round",
          "line-join": "round"
        },
        paint: {
          "line-color": color,
          "line-width": sizeExpr(s, "width", 2.5, 0),
          "line-opacity": s.opacity != null ? s.opacity : 1
        }
      }));
      L.hover = [ mk("hover", "line", {
        filter: NONE,
        layout: {
          visibility: vis,
          "line-cap": "round",
          "line-join": "round"
        },
        paint: {
          "line-color": accent,
          "line-width": sizeExpr(s, "width", 2.5, 3),
          "line-opacity": .25
        }
      }) ];
      L.select = [ mk("selcase", "line", {
        filter: NONE,
        layout: {
          visibility: vis,
          "line-cap": "round",
          "line-join": "round"
        },
        paint: {
          "line-color": "#ffffff",
          "line-width": sizeExpr(s, "width", 2.5, 6)
        }
      }), mk("sel", "line", {
        filter: NONE,
        layout: {
          visibility: vis,
          "line-cap": "round",
          "line-join": "round"
        },
        paint: {
          "line-color": accent,
          "line-width": sizeExpr(s, "width", 2.5, 2)
        }
      }) ];
    } else {
      var r = s.radius != null ? s.radius : 6;
      var ringR = s.marker === "circle" ? sizeExpr(s, "radius", 6, 5) : s.marker === "pin" ? 5 : 17;
      L.hover = [ mk("hover", "circle", {
        filter: NONE,
        layout: {
          visibility: vis
        },
        paint: {
          "circle-radius": ringR,
          "circle-color": accent,
          "circle-opacity": .15,
          "circle-stroke-width": 0
        }
      }) ];
      L.select = [ mk("sel", "circle", {
        filter: NONE,
        layout: {
          visibility: vis
        },
        paint: s.marker === "pin" ? {
          "circle-radius": 5,
          "circle-color": accent,
          "circle-opacity": .45,
          "circle-blur": .4,
          "circle-stroke-width": 0
        } : {
          "circle-radius": ringR,
          "circle-color": accent,
          "circle-opacity": .18,
          "circle-stroke-color": accent,
          "circle-stroke-width": 2
        }
      }) ];
      if (s.marker === "circle") {
        L.main.push(mk("shadow", "circle", {
          layout: Object.assign({
            visibility: vis
          }, s.radiusBy ? {
            "circle-sort-key": [ "-", 0, [ "to-number", [ "get", s.radiusBy.field ], 0 ] ]
          } : {}),
          paint: {
            "circle-radius": sizeExpr(s, "radius", 6, 1.5),
            "circle-color": "#0f172a",
            "circle-opacity": .28,
            "circle-blur": .6,
            "circle-translate": [ 0, 1.5 ]
          }
        }));
        var sortKey = s.radiusBy ? {
          "circle-sort-key": [ "-", 0, [ "to-number", [ "get", s.radiusBy.field ], 0 ] ]
        } : {};
        L.main.push(mk("point", "circle", {
          layout: Object.assign({
            visibility: vis
          }, sortKey),
          paint: {
            "circle-radius": sizeExpr(s, "radius", 6, 0),
            "circle-color": color,
            "circle-opacity": s.opacity != null ? s.opacity : 1,
            "circle-stroke-color": s.outline || "#ffffff",
            "circle-stroke-width": s.radiusBy ? [ "interpolate", [ "linear" ], [ "zoom" ], 2, .4, 8, 1, 14, 1.8 ] : zoomScaled(s.outlineWidth != null ? s.outlineWidth : 2)
          }
        }));
      } else {
        var kind = MARKERS[s.marker] ? s.marker : "badge";
        var defIcon = s.icon || "dot";
        var fallbackImg = addMarkerImage(map, kind, s.defaultColor || s.color || "#8792a2", defIcon);
        var img = fallbackImg;
        if (s.kind === "categorized" && (s.categories || []).length) {
          img = [ "match", [ "to-string", [ "get", s.field ] ] ];
          var seen = {};
          s.categories.forEach(function(c) {
            var v = String(c.value);
            if (seen[v]) return;
            seen[v] = true;
            img.push(v, addMarkerImage(map, kind, c.color, c.icon || defIcon));
          });
          img.push(fallbackImg);
        } else if (s.kind === "graduated" && (s.ranges || []).length) {
          var rr = s.ranges.slice().sort(function(a, b) {
            return a.min - b.min;
          });
          img = [ "step", [ "to-number", [ "get", s.field ], -1e308 ], addMarkerImage(map, kind, rr[0].color, defIcon) ];
          for (var i = 1; i < rr.length; i++) img.push(Number(rr[i].min), addMarkerImage(map, kind, rr[i].color, defIcon));
        }
        var size = s.size != null ? s.size : 1;
        L.main.push(mk("marker", "symbol", {
          layout: {
            visibility: vis,
            "icon-image": img,
            "icon-anchor": MARKERS[kind].anchor,
            "icon-offset": kind === "pin" ? [ 0, 2 ] : [ 0, 0 ],
            "icon-size": [ "interpolate", [ "linear" ], [ "zoom" ], 8, size * .6, 13, size, 18, size * 1.2 ],
            "icon-allow-overlap": true,
            "icon-ignore-placement": true,
            "symbol-z-order": "viewport-y"
          }
        }));
      }
    }
    if (layer.label && layer.label.field) {
      var lab = layer.label, pt = layer.type === "point";
      var off = !pt ? [ 0, 0 ] : s.marker === "pin" ? [ 0, .4 ] : s.marker === "badge" ? [ 0, 1.2 ] : [ 0, 1 ];
      var labBase = {};
      if (lab.sourceLayer && !layer.geojson) labBase = {
        "source-layer": lab.sourceLayer,
        filter: layer.filter || [ "boolean", true ]
      };
      L.labels.push(mk("label", "symbol", Object.assign({
        minzoom: lab.minzoom != null ? lab.minzoom : base.minzoom || 0,
        layout: {
          visibility: vis,
          "text-field": [ "to-string", [ "get", lab.field ] ],
          "text-font": LABEL_FONT,
          "text-size": lab.size || 12,
          "symbol-placement": layer.type === "line" && !lab.sourceLayer ? "line" : "point",
          "text-offset": off,
          "text-anchor": pt ? "top" : "center",
          "text-max-width": 9,
          "text-padding": 4
        },
        paint: {
          "text-color": lab.color || "#1f2937",
          "text-halo-color": lab.halo || "rgba(255,255,255,0.95)",
          "text-halo-width": 1.6,
          "text-halo-blur": .5
        }
      }, labBase)));
    }
    return L;
  }
  function identityFilter(f) {
    if (f.id != null) return [ "==", [ "id" ], f.id ];
    var props = f.properties || {}, conds = [];
    Object.keys(props).forEach(function(k) {
      var v = props[k];
      if (conds.length < 12 && v !== null && typeof v !== "object") conds.push([ "==", [ "get", k ], v ]);
    });
    return conds.length ? [ "all" ].concat(conds) : NONE;
  }
  function featureKey(f) {
    return f.layer.id + "|" + (f.id != null ? f.id : JSON.stringify(f.properties));
  }
  function legendSwatch(layer, color, icon, item) {
    var s = layer.style, wrap = el("span", "sw-wrap");
    if (layer.type === "point" && s.marker !== "circle") {
      wrap.innerHTML = markerSVG(s.marker, color, icon || s.icon || "dot", s.marker === "pin" ? 16 : 20);
      return wrap;
    }
    var sw = el("span", "sw sw-" + layer.type);
    sw.style.setProperty("--c", layer.type === "polygon" && s.opacity === 0 ? "transparent" : color);
    if (layer.type === "polygon") sw.style.setProperty("--o", s.outline && s.outline !== "none" && (s.outline !== "#ffffff" || s.opacity === 0) ? s.outline : shade(color, -.25));
    if (layer.type === "point" && item && item.radius != null) {
      var d = Math.max(6, Math.min(22, item.radius * 2));
      sw.style.width = sw.style.height = d + "px";
      wrap.style.width = "24px";
    }
    wrap.appendChild(sw);
    return wrap;
  }
  var OPACITY_PROPS = {
    fill: [ "fill-opacity" ],
    line: [ "line-opacity" ],
    circle: [ "circle-opacity", "circle-stroke-opacity" ],
    symbol: [ "icon-opacity", "text-opacity" ]
  };
  var baseOpacity = {};
  function setLayerOpacity(map, layerIds, k) {
    layerIds.forEach(function(id) {
      if (/__(hover|hoverline|sel|selcase)$/.test(id)) return;
      var l = map.getLayer(id);
      if (!l) return;
      (OPACITY_PROPS[l.type] || []).forEach(function(prop) {
        var key = id + "|" + prop;
        if (!(key in baseOpacity)) {
          var v = map.getPaintProperty(id, prop);
          baseOpacity[key] = typeof v === "number" ? v : 1;
        }
        map.setPaintProperty(id, prop, baseOpacity[key] * k);
      });
    });
  }
  function niceNumber(v) {
    if (v <= 0) return 0;
    var p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
    return (n >= 5 ? 5 : n >= 2 ? 2 : 1) * p;
  }
  function sizeLegend(rb, title) {
    var wrap = el("div", "lg-size");
    wrap.appendChild(el("div", "lg-size-title", title));
    var vals = [ niceNumber(rb.max), niceNumber(rb.min + (rb.max - rb.min) * .25), niceNumber(rb.min + (rb.max - rb.min) * .03) ].filter(function(v, i, a) {
      return v > 0 && a.indexOf(v) === i;
    });
    var radius = function(v) {
      var t = Math.max(0, Math.min(1, (v - rb.min) / (rb.max - rb.min || 1)));
      return (rb.minRadius + (rb.maxRadius - rb.minRadius) * Math.pow(t, rb.exponent || .5)) * .8;
    };
    var R = Math.max(radius(vals[0]), 14), W = R * 2 + 76, H = R * 2 + 6;
    var svg = '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H + '" aria-hidden="true">';
    var lastY = -Infinity;
    vals.forEach(function(v) {
      var r = radius(v) * (R / Math.max(radius(vals[0]), .01)), cy = H - 3 - r, top = cy - r;
      var ty = Math.max(top + 4, lastY + 12);
      lastY = ty;
      svg += '<circle cx="' + (R + 2) + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="#64748b" stroke-width="1"/>' + '<polyline points="' + (R + 2) + "," + top + " " + (R * 2 + 8) + "," + top + " " + (R * 2 + 12) + "," + (ty - 4) + '" fill="none" stroke="#cbd5e1"/>' + '<text x="' + (R * 2 + 15) + '" y="' + ty + '" font-size="11" fill="#475569">' + v.toLocaleString() + "</text>";
    });
    wrap.innerHTML += svg + "</svg>";
    return wrap;
  }
  function buildLegend(map, cfg, ids) {
    var box = $("legend");
    box.innerHTML = "";
    box.appendChild(el("h2", null, cfg.legendTitle || "Layers"));
    cfg.layers.slice().reverse().forEach(function(layer) {
      if (layer.legend === false) return;
      var s = layer.style, row = el("div", "lg-layer" + (layer.visible === false ? " off" : "")), op = null;
      var label = el("label", "lg-head");
      var cb = el("input");
      cb.type = "checkbox";
      cb.checked = layer.visible !== false;
      cb.addEventListener("change", function() {
        ids[layer.id].forEach(function(id) {
          map.setLayoutProperty(id, "visibility", cb.checked ? "visible" : "none");
        });
        row.classList.toggle("off", !cb.checked);
        if (!cb.checked) clearSelection();
      });
      label.appendChild(cb);
      if (s.kind === "single") label.appendChild(legendSwatch(layer, s.color, s.icon));
      label.appendChild(el("span", "lg-name", layer.name || layer.id));
      var headRow = el("div", "lg-headrow");
      headRow.appendChild(label);
      if (cfg.opacityControl !== false) {
        var btn = el("button", "lg-opbtn");
        btn.type = "button";
        btn.title = "Transparency";
        btn.setAttribute("aria-label", "Transparency of " + (layer.name || layer.id));
        btn.setAttribute("aria-expanded", "false");
        btn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="currentColor"/></svg>';
        headRow.appendChild(btn);
        var op = el("div", "lg-opacity");
        op.hidden = true;
        var range = el("input");
        range.type = "range";
        range.min = "0";
        range.max = "100";
        range.value = "100";
        range.setAttribute("aria-label", "Opacity of " + (layer.name || layer.id));
        var pct = el("span", "lg-oppct", "100%");
        range.addEventListener("input", function() {
          pct.textContent = range.value + "%";
          setLayerOpacity(map, ids[layer.id], range.value / 100);
        });
        btn.addEventListener("click", function() {
          op.hidden = !op.hidden;
          btn.setAttribute("aria-expanded", String(!op.hidden));
          btn.classList.toggle("active", !op.hidden);
        });
        op.appendChild(range);
        op.appendChild(pct);
      }
      row.appendChild(headRow);
      if (op) row.appendChild(op);
      var items = s.kind === "categorized" ? s.categories : s.kind === "graduated" ? s.ranges : null;
      if (items && items.length) {
        var ul = el("ul", "lg-items");
        items.forEach(function(it) {
          var li = el("li");
          li.appendChild(legendSwatch(layer, it.color, it.icon, it));
          var text = it.label != null ? it.label : s.kind === "graduated" ? it.min + " – " + it.max : it.value;
          li.appendChild(el("span", null, String(text)));
          ul.appendChild(li);
        });
        row.appendChild(ul);
      }
      if (layer.type === "point" && s.radiusBy) row.appendChild(sizeLegend(s.radiusBy, layer.sizeLabel || prettyKey(s.radiusBy.field)));
      box.appendChild(row);
    });
    var toggle = $("legend-toggle");
    toggle.addEventListener("click", function() {
      var open = box.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.classList.toggle("active", open);
      if (open) closeSheet();
    });
  }
  function formatValue(v, key) {
    if (typeof v === "boolean") return v ? "Yes" : "No";
    if (typeof v === "number" && Number.isInteger(v) && (/year|yr|_date|date_/i.test(key || "") || key && /^(commissioned|built|founded|established)$/i.test(key))) return String(v);
    if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, {
      maximumFractionDigits: 2
    });
    return String(v);
  }
  var isEmpty = function(v) {
    return v == null || v === "" || typeof v === "string" && /^(null|none)$/i.test(v.trim());
  };
  var isUrl = function(v) {
    return typeof v === "string" && /^https?:\/\/\S+$/i.test(v.trim());
  };
  var isEmail = function(v) {
    return typeof v === "string" && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());
  };
  var prettyKey = function(k) {
    return String(k).replace(/[_-]+/g, " ").replace(/^\w/, function(c) {
      return c.toUpperCase();
    });
  };
  var TITLE_GUESS = [ "name", "Name", "NAME", "title", "Title", "label", "Label" ];
  var HIDDEN_KEYS = /^(fid|ogc_fid|objectid|gid|id|shape_length|shape_area|shape_leng)$/i;
  function titleOf(layer, props) {
    var p = layer.popup || {};
    if (p.title && !isEmpty(props[p.title])) return {
      key: p.title,
      text: formatValue(props[p.title])
    };
    for (var i = 0; i < TITLE_GUESS.length; i++) if (!isEmpty(props[TITLE_GUESS[i]])) return {
      key: TITLE_GUESS[i],
      text: formatValue(props[TITLE_GUESS[i]])
    };
    return {
      key: null,
      text: layer.name || layer.id
    };
  }
  function card(state) {
    var f = state.items[state.index], layer = f._cfg, props = f.properties || {}, p = layer.popup || {};
    var look = featureLook(layer, props), title = titleOf(layer, props);
    var root = el("article", "wm-card");
    root.style.setProperty("--c", look.color);
    var head = el("header", "wm-head");
    var badge = el("span", "wm-badge");
    if (layer.type === "point" && layer.style.marker !== "circle") badge.innerHTML = markerSVG("badge", look.color, look.icon, 34); else {
      badge.classList.add("plain");
      badge.appendChild(el("span", "wm-dot wm-dot-" + layer.type));
    }
    head.appendChild(badge);
    var hText = el("div", "wm-htext");
    var kicker = el("div", "wm-kicker");
    kicker.appendChild(el("span", null, layer.name || layer.id));
    if (look.label != null && String(look.label) !== title.text) kicker.appendChild(el("span", "wm-chip", String(look.label)));
    hText.appendChild(kicker);
    hText.appendChild(el("h3", "wm-title", title.text));
    head.appendChild(hText);
    root.appendChild(head);
    var fields = p.fields === "*" || !p.fields ? Object.keys(props).filter(function(k) {
      return k !== title.key && !HIDDEN_KEYS.test(k);
    }).map(function(k) {
      return {
        field: k
      };
    }) : p.fields.map(function(f) {
      return typeof f === "string" ? {
        field: f
      } : f;
    });
    var links = [], dl = el("dl", "wm-fields");
    fields.forEach(function(fd) {
      var v = props[fd.field];
      if (isEmpty(v)) {
        if (p.hideEmpty === false) v = "–"; else return;
      }
      if (fd.link || isUrl(v) && fd.link !== false) {
        if (isUrl(v)) {
          links.push({
            href: String(v).trim(),
            text: fd.linkText || fd.label || "Open link"
          });
          return;
        }
      }
      var row = el("div", "wm-row" + (String(v).length > 40 ? " wide" : ""));
      row.appendChild(el("dt", null, fd.label || prettyKey(fd.field)));
      var dd = el("dd");
      if (isEmail(v)) {
        var a = el("a", null, String(v));
        a.href = "mailto:" + String(v).trim();
        dd.appendChild(a);
      } else dd.textContent = formatValue(v, fd.field) + (fd.suffix ? " " + fd.suffix : "");
      row.appendChild(dd);
      dl.appendChild(row);
    });
    if (dl.children.length) root.appendChild(dl);
    var actions = el("div", "wm-actions");
    links.forEach(function(l) {
      var a = el("a", "wm-btn", l.text);
      a.href = l.href;
      a.target = "_blank";
      a.rel = "noopener";
      actions.appendChild(a);
    });
    var zoom = el("button", "wm-btn ghost", "Zoom to");
    zoom.type = "button";
    zoom.addEventListener("click", function() {
      zoomToFeature(state.map, f);
    });
    actions.appendChild(zoom);
    root.appendChild(actions);
    if (state.items.length > 1) {
      var pager = el("nav", "wm-pager");
      var prev = el("button", null, "‹"), next = el("button", null, "›");
      prev.type = next.type = "button";
      prev.setAttribute("aria-label", "Previous feature");
      next.setAttribute("aria-label", "Next feature");
      prev.addEventListener("click", function() {
        state.go(state.index - 1);
      });
      next.addEventListener("click", function() {
        state.go(state.index + 1);
      });
      pager.appendChild(prev);
      pager.appendChild(el("span", null, state.index + 1 + " of " + state.items.length));
      pager.appendChild(next);
      root.appendChild(pager);
    }
    return root;
  }
  function geomBounds(g) {
    var b = [ Infinity, Infinity, -Infinity, -Infinity ];
    (function walk(c) {
      if (typeof c[0] === "number") {
        b[0] = Math.min(b[0], c[0]);
        b[1] = Math.min(b[1], c[1]);
        b[2] = Math.max(b[2], c[0]);
        b[3] = Math.max(b[3], c[1]);
      } else c.forEach(walk);
    })(g.coordinates);
    return b;
  }
  function zoomToFeature(map, f) {
    var b = geomBounds(f.geometry);
    var pad = isSmall() ? {
      top: 60,
      bottom: Math.round(window.innerHeight * .45),
      left: 30,
      right: 30
    } : 80;
    if (b[0] === b[2] && b[1] === b[3]) map.flyTo({
      center: [ b[0], b[1] ],
      zoom: Math.max(map.getZoom(), 16),
      padding: pad
    }); else map.fitBounds([ [ b[0], b[1] ], [ b[2], b[3] ] ], {
      padding: pad,
      maxZoom: 17
    });
  }
  var clearSelection = function() {};
  var closeSheet = function() {};
  function wireInteraction(map, cfg, parts) {
    var clickable = [], cfgByMapLayer = {};
    cfg.layers.forEach(function(layer) {
      if (layer.popup === false) return;
      parts[layer.id].main.forEach(function(l) {
        if (/__(fill|line|point|marker)$/.test(l.id)) {
          clickable.push(l.id);
          cfgByMapLayer[l.id] = layer;
        }
      });
    });
    if (!clickable.length) return;
    var setFilters = function(group, layerId, f) {
      (parts[layerId][group] || []).forEach(function(l) {
        map.setFilter(l.id, f ? combine(combine(geomFilter(cfgByLayerId[layerId].type), cfgByLayerId[layerId]._drawFilter), identityFilter(f)) : NONE);
      });
    };
    var cfgByLayerId = {};
    cfg.layers.forEach(function(l) {
      cfgByLayerId[l.id] = l;
    });
    var fillLayers = clickable.filter(function(id) {
      return /__fill$/.test(id);
    });
    var otherLayers = clickable.filter(function(id) {
      return !/__fill$/.test(id);
    });
    function rank(f) {
      var c = f._cfg, t = c.type;
      if (t === "point") return 0;
      if (t === "line") return 1;
      return c.style && c.style.kind === "single" && c.style.opacity === 0 ? 3 : 2;
    }
    function pick(point) {
      var pad = isSmall() ? 10 : 5, feats = [];
      if (otherLayers.length) feats = feats.concat(map.queryRenderedFeatures([ [ point.x - pad, point.y - pad ], [ point.x + pad, point.y + pad ] ], {
        layers: otherLayers
      }));
      if (fillLayers.length) feats = feats.concat(map.queryRenderedFeatures(point, {
        layers: fillLayers
      }));
      var seen = {}, out = [];
      feats.forEach(function(f, i) {
        var k = featureKey(f);
        if (seen[k]) return;
        seen[k] = true;
        f._cfg = cfgByMapLayer[f.layer.id];
        f._i = i;
        out.push(f);
      });
      out.sort(function(a, b) {
        return rank(a) - rank(b) || a._i - b._i;
      });
      return out;
    }
    var tip = $("tooltip"), hovered = null, raf = 0, lastEvt = null;
    map.on("mousemove", function(e) {
      lastEvt = e;
      if (raf) return;
      raf = requestAnimationFrame(function() {
        raf = 0;
        var f = pick(lastEvt.point)[0] || null, key = f ? featureKey(f) : null;
        map.getCanvas().style.cursor = f ? "pointer" : "";
        if (key !== (hovered && hovered.key)) {
          if (hovered) setFilters("hover", hovered.layerId, null);
          hovered = f ? {
            key: key,
            layerId: f._cfg.id
          } : null;
          if (f) setFilters("hover", f._cfg.id, f);
        }
        if (f && cfg.tooltip !== false && key !== selectedKey) {
          tip.textContent = titleOf(f._cfg, f.properties || {}).text;
          tip.hidden = false;
          tip.style.transform = "translate(" + (lastEvt.point.x + 14) + "px," + (lastEvt.point.y + 14) + "px)";
        } else tip.hidden = true;
      });
    });
    map.getCanvas().addEventListener("mouseleave", function() {
      tip.hidden = true;
      if (hovered) {
        setFilters("hover", hovered.layerId, null);
        hovered = null;
      }
    });
    var selectedKey = null;
    var selected = null, popup = null, sheet = $("sheet"), sheetBody = $("sheet-body");
    clearSelection = function() {
      if (selected) setFilters("select", selected.layerId, null);
      selected = null;
      selectedKey = null;
      if (popup) {
        var p = popup;
        popup = null;
        p.remove();
      }
      closeSheet();
    };
    closeSheet = function() {
      sheet.classList.remove("open");
      sheet.setAttribute("aria-hidden", "true");
    };
    $("sheet-close").addEventListener("click", clearSelection);
    document.addEventListener("keydown", function(e) {
      if (e.key === "Escape") clearSelection();
    });
    function show(items, index, lngLat) {
      var state = {
        map: map,
        items: items,
        index: index,
        go: function(i) {
          show(items, (i + items.length) % items.length, null);
        }
      };
      var f = items[index];
      if (selected) setFilters("select", selected.layerId, null);
      selected = {
        layerId: f._cfg.id
      };
      selectedKey = featureKey(f);
      setFilters("select", f._cfg.id, f);
      tip.hidden = true;
      var isPoint = f.geometry.type === "Point";
      var at = isPoint ? f.geometry.coordinates : lngLat || popup && popup.getLngLat() || map.getCenter();
      var node = card(state);
      if (isSmall()) {
        if (popup) {
          var p = popup;
          popup = null;
          p.remove();
        }
        $("legend").classList.remove("open");
        sheetBody.innerHTML = "";
        sheetBody.appendChild(node);
        sheet.classList.add("open");
        sheet.setAttribute("aria-hidden", "false");
        return;
      }
      closeSheet();
      var m = f._cfg.style.marker;
      var offset = isPoint && m === "pin" ? {
        top: [ 0, 6 ],
        "top-left": [ 0, 6 ],
        "top-right": [ 0, 6 ],
        bottom: [ 0, -40 ],
        "bottom-left": [ 0, -40 ],
        "bottom-right": [ 0, -40 ],
        left: [ 16, -20 ],
        right: [ -16, -20 ]
      } : isPoint ? 16 : 8;
      if (!popup) {
        popup = new maplibregl.Popup({
          className: "wm-popup",
          maxWidth: "340px",
          offset: offset,
          focusAfterOpen: false,
          closeOnClick: false
        });
        popup.setLngLat(at).setDOMContent(node).addTo(map);
        popup.on("close", function() {
          if (popup) {
            popup = null;
            clearSelection();
          }
        });
      } else {
        popup.setOffset(offset);
        popup.setLngLat(at).setDOMContent(node);
      }
      requestAnimationFrame(function() {
        if (!popup) return;
        var r = popup.getElement().getBoundingClientRect(), m = map.getContainer().getBoundingClientRect(), pad = 16;
        var dx = r.left < m.left + pad ? r.left - m.left - pad : r.right > m.right - pad ? r.right - m.right + pad : 0;
        var dy = r.top < m.top + pad ? r.top - m.top - pad : r.bottom > m.bottom - pad ? r.bottom - m.bottom + pad : 0;
        if (dx || dy) map.panBy([ dx, dy ], {
          duration: 300
        });
      });
    }
    map.on("click", function(e) {
      var items = pick(e.point);
      if (!items.length) {
        clearSelection();
        return;
      }
      show(items, 0, e.lngLat);
    });
    map._wmShowAt = function(lngLat) {
      var items = pick(map.project(lngLat));
      if (items.length) show(items, 0, lngLat);
    };
  }
  function wireSearch(map, cfg) {
    if (!cfg.search || !cfg.search.index) return;
    getJSON(cfg.search.index).then(function(rows) {
      var box = $("search"), input = $("search-input"), list = $("search-results");
      box.hidden = false;
      if (cfg.search.placeholder) input.placeholder = cfg.search.placeholder;
      var results = [], active = -1;
      var norm = function(s) {
        return String(s).toLowerCase();
      };
      function go(r) {
        list.innerHTML = "";
        input.value = r.t;
        input.blur();
        map.once("moveend", function() {
          setTimeout(function() {
            if (map._wmShowAt) map._wmShowAt(r.c);
          }, 250);
        });
        map.flyTo({
          center: r.c,
          zoom: Math.max(map.getZoom(), cfg.search.zoom || 16)
        });
      }
      function render() {
        list.innerHTML = "";
        results.forEach(function(r, i) {
          var li = el("li", null, r.t);
          li.setAttribute("role", "option");
          li.setAttribute("aria-selected", String(i === active));
          if (r.l) li.appendChild(el("small", null, r.l));
          li.addEventListener("mousedown", function(ev) {
            ev.preventDefault();
            go(r);
          });
          list.appendChild(li);
        });
      }
      input.addEventListener("input", function() {
        var q = norm(input.value.trim());
        active = -1;
        results = q.length < 2 ? [] : rows.filter(function(r) {
          return norm(r.t).indexOf(q) !== -1;
        }).sort(function(a, b) {
          return norm(a.t).indexOf(q) - norm(b.t).indexOf(q);
        }).slice(0, 8);
        render();
      });
      input.addEventListener("keydown", function(e) {
        if (!results.length) return;
        if (e.key === "ArrowDown") {
          active = (active + 1) % results.length;
          render();
          e.preventDefault();
        } else if (e.key === "ArrowUp") {
          active = (active - 1 + results.length) % results.length;
          render();
          e.preventDefault();
        } else if (e.key === "Enter") go(results[Math.max(active, 0)]); else if (e.key === "Escape") list.innerHTML = "";
      });
      input.addEventListener("blur", function() {
        setTimeout(function() {
          list.innerHTML = "";
        }, 150);
      });
    }).catch(function(err) {
      console.warn("Search index not loaded:", err);
    });
  }
  function start(cfg) {
    document.title = cfg.title || "Web map";
    $("title").textContent = cfg.title || "Web map";
    $("description").textContent = cfg.description || "";
    if (!document.getElementById("wm-made")) {
      var made = document.createElement("a");
      made.id = "wm-made";
      made.href = "https://mapship.link/webmap-kit/?utm_source=lite-map&utm_medium=badge";
      made.target = "_blank";
      made.rel = "noopener";
      made.textContent = "Made with Web Map Kit";
      made.style.cssText = "display:inline-block;margin-top:5px;font-size:11px;line-height:1.3;color:var(--muted,#667085);text-decoration:none;opacity:.85";
      $("titlebar").appendChild(made);
    }
    if (cfg.accentColor) document.documentElement.style.setProperty("--accent", cfg.accentColor);
    if (!Array.isArray(cfg.layers) || !cfg.layers.length) throw new Error("config.json has no layers");
    cfg.layers.forEach(layerDefaults);
    var accent = cfg.highlightColor || "#0f172a";
    var protocol = new pmtiles.Protocol;
    maplibregl.addProtocol("pmtiles", protocol.tile);
    var hadHash = /^#\d/.test(location.hash);
    return loadBasemap(cfg.basemap).then(function(style) {
      var map = new maplibregl.Map({
        container: "map",
        style: style,
        center: cfg.center || [ 0, 20 ],
        zoom: cfg.zoom != null ? cfg.zoom : 2,
        maxZoom: cfg.maxZoom || 20,
        bounds: !hadHash && !cfg.center && (cfg.view || cfg.bounds) ? [ [ (cfg.view || cfg.bounds)[0], (cfg.view || cfg.bounds)[1] ], [ (cfg.view || cfg.bounds)[2], (cfg.view || cfg.bounds)[3] ] ] : undefined,
        fitBoundsOptions: {
          padding: cfg.view ? 0 : isSmall() ? 30 : 60,
          maxZoom: cfg.view ? 18 : 16
        },
        hash: cfg.urlHash !== false,
        renderWorldCopies: cfg.worldCopies != null ? !!cfg.worldCopies : !(cfg.bounds && cfg.bounds[2] - cfg.bounds[0] > 300),
        attributionControl: {
          compact: true,
          customAttribution: cfg.attribution || undefined
        }
      });
      map.addControl(new maplibregl.NavigationControl({
        visualizePitch: false
      }), "top-right");
      map.addControl(new maplibregl.FullscreenControl, "top-right");
      map.addControl(new maplibregl.GeolocateControl({
        trackUserLocation: false
      }), "top-right");
      map.addControl(new maplibregl.ScaleControl({
        unit: cfg.units || "metric"
      }), "bottom-left");
      var bar = $("loading");
      map.on("dataloading", function() {
        bar.classList.add("on");
      });
      map.on("idle", function() {
        bar.classList.remove("on");
      });
      map.on("error", function(e) {
        var msg = e && e.error && e.error.message || "";
        console.warn("Map error:", msg);
        if (/pmtiles|geojson|404|Failed to fetch/i.test(msg) && !/glyph|font/i.test(msg)) notice("Some map data could not be loaded. Check the data paths in config.json.", 8e3);
      });
      map.on("load", function() {
        var dataUrl = cfg.data ? absolute(cfg.data) : null;
        if (dataUrl) {
          if (isPMTiles(dataUrl)) {
            protocol.add(new pmtiles.PMTiles(dataUrl));
            map.addSource("data", {
              type: "vector",
              url: "pmtiles://" + dataUrl,
              attribution: cfg.dataAttribution || undefined
            });
          } else map.addSource("data", {
            type: "vector",
            url: dataUrl,
            attribution: cfg.dataAttribution || undefined
          });
        }
        var before = cfg.dataAboveLabels ? undefined : firstLabelLayer(map);
        var parts = {}, ids = {}, labels = [];
        cfg.layers.forEach(function(layer) {
          if (!layer.id) throw new Error("Every layer in config.json needs an id");
          if (layer.geojson) {
            layer._source = "gj-" + layer.id;
            map.addSource(layer._source, {
              type: "geojson",
              data: absolute(layer.geojson),
              generateId: true,
              attribution: layer.attribution || undefined
            });
          } else if (layer.data) {
            var u = absolute(layer.data);
            layer._source = "src-" + layer.id;
            if (isPMTiles(u)) protocol.add(new pmtiles.PMTiles(u));
            map.addSource(layer._source, {
              type: "vector",
              url: isPMTiles(u) ? "pmtiles://" + u : u
            });
          } else if (dataUrl) layer._source = "data"; else throw new Error('Layer "' + layer.id + '" has no data. Set "data" at the top of config.json or "geojson" on the layer.');
          var L = buildLayers(map, layer, accent);
          parts[layer.id] = L;
          var order = layer.type === "polygon" ? L.main.concat(L.hover || [], L.select || []) : layer.type === "line" ? L.main.slice(0, -1).concat(L.select || [], L.main.slice(-1), L.hover || []) : (L.hover || []).concat(L.select || [], L.main);
          order.forEach(function(l) {
            map.addLayer(l, layer.type === "point" ? undefined : before);
          });
          ids[layer.id] = order.concat(L.labels).map(function(l) {
            return l.id;
          });
          labels = labels.concat(L.labels);
        });
        labels.forEach(function(l) {
          map.addLayer(l);
        });
        buildLegend(map, cfg, ids);
        wireInteraction(map, cfg, parts);
        wireSearch(map, cfg);
        if (!hadHash && !cfg.center) {
          var fit = function(b) {
            map.fitBounds(b, {
              padding: isSmall() ? 30 : 60,
              duration: 0,
              maxZoom: 16
            });
          };
          if (cfg.view) map.fitBounds([ [ cfg.view[0], cfg.view[1] ], [ cfg.view[2], cfg.view[3] ] ], {
            padding: 0,
            duration: 0,
            maxZoom: 18
          }); else if (cfg.bounds) fit([ [ cfg.bounds[0], cfg.bounds[1] ], [ cfg.bounds[2], cfg.bounds[3] ] ]); else if (dataUrl && isPMTiles(dataUrl)) {
            new pmtiles.PMTiles(dataUrl).getHeader().then(function(h) {
              fit([ [ h.minLon, h.minLat ], [ h.maxLon, h.maxLat ] ]);
            }).catch(function() {});
          }
        }
        window.webmap = map;
      });
      return map;
    });
  }
  getJSON("config.json").then(start).catch(function(err) {
    console.error(err);
    $("title").textContent = "Map could not start";
    notice(location.protocol === "file:" ? "Open this map through a web server, not by double-clicking. See the guide, step 1." : "Problem in config.json: " + err.message);
  });
})();
