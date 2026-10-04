# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import tempfile
import time
from qgis.PyQt.QtCore import QCoreApplication
from .edition import PROVIDER_ID
from qgis.core import Qgis, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsExpressionContextUtils, QgsFeatureRequest, QgsGeometry, QgsGraduatedSymbolRenderer, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterExtent, QgsProcessingException, QgsProcessingParameterBoolean, QgsProcessingParameterDefinition, QgsProcessingParameterEnum, QgsProcessingParameterFile, QgsProcessingParameterFolderDestination, QgsProcessingParameterMultipleLayers, QgsProcessingParameterNumber, QgsProcessingParameterString, QgsProject, QgsRectangle, QgsSingleSymbolRenderer, QgsVectorLayer, QgsVectorLayerSimpleLabeling, QgsVectorTileWriter, QgsWkbTypes
KIT_VERSION = '1.0.0'
WEB_APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
BASEMAPS = ['positron', 'liberty', 'bright', 'none']
POINT_STYLES = ['circle', 'badge', 'pin']
WEB_MERCATOR_MAX_LAT = 85.0511

def _zxy_to_tileid(z, x, y):
    acc = ((1 << z * 2) - 1) // 3
    a = z - 1
    while a >= 0:
        s = 1 << a
        rx = s & x
        ry = s & y
        acc += (3 * rx ^ ry) << a
        if ry == 0:
            if rx != 0:
                x = s - 1 - x
                y = s - 1 - y
            x, y = (y, x)
        a -= 1
    return acc

def _varint(out, i):
    while True:
        b = i & 127
        i >>= 7
        if i:
            out.append(b | 128)
        else:
            out.append(b)
            return

def _directory(entries):
    out = bytearray()
    _varint(out, len(entries))
    last = 0
    for e in entries:
        _varint(out, e[0] - last)
        last = e[0]
    for e in entries:
        _varint(out, e[3])
    for e in entries:
        _varint(out, e[2])
    for i, e in enumerate(entries):
        if i > 0 and e[1] == entries[i - 1][1] + entries[i - 1][2]:
            _varint(out, 0)
        else:
            _varint(out, e[1] + 1)
    return gzip.compress(bytes(out), mtime=0)

def _root_and_leaves(entries, max_root=16384 - 127):
    root = _directory(entries)
    if len(root) <= max_root:
        return (root, b'')
    leaf_size = 4096
    while True:
        roots, leaves = ([], bytearray())
        for i in range(0, len(entries), leaf_size):
            chunk = _directory(entries[i:i + leaf_size])
            roots.append([entries[i][0], len(leaves), len(chunk), 0])
            leaves += chunk
        root = _directory(roots)
        if len(root) <= max_root:
            return (root, bytes(leaves))
        leaf_size *= 2

def _rv(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 127) << shift
        if not b & 128:
            return (result, i)
        shift += 7

def _fields(buf):
    i, n = (0, len(buf))
    while i < n:
        start = i
        key, i = _rv(buf, i)
        fn, wt = (key >> 3, key & 7)
        if wt == 0:
            val, i = _rv(buf, i)
        elif wt == 1:
            val, i = (buf[i:i + 8], i + 8)
        elif wt == 2:
            ln, i = _rv(buf, i)
            val, i = (buf[i:i + ln], i + ln)
        elif wt == 5:
            val, i = (buf[i:i + 4], i + 4)
        else:
            raise ValueError('bad wire type')
        yield (fn, wt, val, buf[start:i])

def _fix_feature(fbuf):
    ftype, geom = (0, b'')
    for fn, wt, val, _ in _fields(fbuf):
        if fn == 3 and wt == 0:
            ftype = val
        elif fn == 4 and wt == 2:
            geom = val
    if ftype not in (1, 2, 3) or not geom:
        return (None, False)
    i, moves, lines, fixed, out = (0, 0, 0, False, bytearray())
    while i < len(geom):
        start = i
        cmd, i = _rv(geom, i)
        cid, cnt = (cmd & 7, cmd >> 3)
        if cid in (1, 2):
            for _ in range(cnt * 2):
                _, i = _rv(geom, i)
            if cnt == 0:
                if cid == 1:
                    return (None, False)
                fixed = True
                continue
            if cid == 1:
                moves += cnt
            else:
                lines += cnt
        elif cid != 7:
            return (None, False)
        out += geom[start:i]
    ok = moves >= 1 if ftype == 1 else lines >= 1 if ftype == 2 else lines >= 2
    if not ok:
        return (None, False)
    if not fixed:
        return (fbuf, False)
    rebuilt = bytearray()
    for fn, wt, val, raw in _fields(fbuf):
        rebuilt += _len_field(4, out) if fn == 4 and wt == 2 else raw
    return (bytes(rebuilt), True)

def stamp_assets(index_path, ver):
    import re
    try:
        with open(index_path, encoding='utf-8') as f:
            html = f.read()
    except OSError:
        return
    pat = '((?:src|href)=")((?!https?:|//|data:)[^"?#]+\\.(?:js|css))(?:\\?v=[^"]*)?(")'
    html = re.sub(pat, lambda m: m.group(1) + m.group(2) + '?v=' + ver + m.group(3), html)
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html)

def _len_field(fn, payload):
    out = bytearray()
    _varint(out, fn << 3 | 2)
    _varint(out, len(payload))
    return bytes(out) + bytes(payload)

def clean_tile(data):
    gz = data[:2] == b'\x1f\x8b'
    raw = gzip.decompress(data) if gz else data
    dropped, repaired, out = (0, 0, bytearray())
    try:
        for fn, wt, val, rawf in _fields(raw):
            if fn != 3 or wt != 2:
                out += rawf
                continue
            layer, kept = (bytearray(), 0)
            for lfn, lwt, lval, lraw in _fields(val):
                if lfn == 2 and lwt == 2:
                    feat, was_fixed = _fix_feature(lval)
                    if feat is None:
                        dropped += 1
                    else:
                        layer += lraw if feat is lval else _len_field(2, feat)
                        kept += 1
                        repaired += was_fixed
                else:
                    layer += lraw
            if kept:
                out += _len_field(3, layer)
    except (IndexError, ValueError):
        return (data, 0)
    if not dropped and (not repaired):
        return (data, 0)
    out = bytes(out)
    return (gzip.compress(out, mtime=0) if gz else out, dropped)

def mbtiles_to_pmtiles(mbtiles_path, pmtiles_path, bounds, feedback=None):
    db = sqlite3.connect(mbtiles_path)
    meta = dict(db.execute('SELECT name, value FROM metadata').fetchall())
    rows = db.execute('SELECT zoom_level, tile_column, tile_row FROM tiles').fetchall()
    if not rows:
        db.close()
        raise QgsProcessingException('No tiles were produced. Check that your layers have features inside the chosen zoom range.')
    ids = sorted(((_zxy_to_tileid(z, x, (1 << z) - 1 - r), z, x, r) for z, x, r in rows))
    entries, seen, offset, gz, dropped = ([], {}, 0, None, 0)
    tmp = tempfile.TemporaryFile()
    total = len(ids)
    for n, (tid, z, x, r) in enumerate(ids):
        data = db.execute('SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?', (z, x, r)).fetchone()[0]
        data, bad = clean_tile(bytes(data))
        dropped += bad
        if gz is None:
            gz = data[:2] == b'\x1f\x8b'
        h = hashlib.sha256(data).digest()
        if h in seen:
            last = entries[-1]
            if tid == last[0] + last[3] and last[1] == seen[h]:
                last[3] += 1
            else:
                entries.append([tid, seen[h], len(data), 1])
        else:
            tmp.write(data)
            entries.append([tid, offset, len(data), 1])
            seen[h] = offset
            offset += len(data)
        if feedback and n % 500 == 0:
            feedback.setProgress(60 + 30 * n / total)
            if feedback.isCanceled():
                break
    db.close()
    metadata = {'name': meta.get('name', 'Web Map Kit'), 'generator': 'Web Map Kit ' + KIT_VERSION}
    if meta.get('json'):
        try:
            metadata.update(json.loads(meta['json']))
        except ValueError:
            pass
    meta_bytes = gzip.compress(json.dumps(metadata).encode('utf-8'), mtime=0)
    root, leaves = _root_and_leaves(entries)
    w, s, e, n_ = bounds
    zmin, zmax = (ids[0][1], max((i[1] for i in ids)))
    e7 = lambda v: int(round(v * 10000000.0))
    header = bytearray(b'PMTiles\x03')
    root_off = 127
    meta_off = root_off + len(root)
    leaf_off = meta_off + len(meta_bytes)
    data_off = leaf_off + len(leaves)
    header += struct.pack('<QQQQQQQQ', root_off, len(root), meta_off, len(meta_bytes), leaf_off, len(leaves), data_off, offset)
    header += struct.pack('<QQQ', total, len(entries), len(seen))
    header += bytes([1, 2, 2 if gz else 1, 1, zmin, zmax])
    header += struct.pack('<iiii', e7(w), e7(s), e7(e), e7(n_))
    header += bytes([min(zmax, max(zmin, zmin + 2))])
    header += struct.pack('<ii', e7((w + e) / 2), e7((s + n_) / 2))
    assert len(header) == 127
    with open(pmtiles_path, 'wb') as f:
        f.write(header)
        f.write(root)
        f.write(meta_bytes)
        f.write(leaves)
        tmp.seek(0)
        shutil.copyfileobj(tmp, f)
    tmp.close()
    return {'tiles': total, 'unique': len(seen), 'bytes': os.path.getsize(pmtiles_path), 'minzoom': zmin, 'maxzoom': zmax, 'dropped': dropped}

def _enum_value(obj, *names):
    for n in names:
        cur = obj
        try:
            for part in n.split('.'):
                cur = getattr(cur, part)
            return cur
        except AttributeError:
            continue
    return None

def geometry_kind(layer):
    gt = layer.geometryType()
    point = _enum_value(Qgis, 'GeometryType.Point')
    line = _enum_value(Qgis, 'GeometryType.Line')
    poly = _enum_value(Qgis, 'GeometryType.Polygon')
    if point is None:
        point, line, poly = (QgsWkbTypes.PointGeometry, QgsWkbTypes.LineGeometry, QgsWkbTypes.PolygonGeometry)
    if gt == point:
        return 'point'
    if gt == line:
        return 'line'
    if gt == poly:
        return 'polygon'
    return None

def _unit_kind(unit):
    checks = [('mm', ['RenderUnit.Millimeters'], ['RenderMillimeters']), ('px', ['RenderUnit.Pixels'], ['RenderPixels']), ('pt', ['RenderUnit.Points'], ['RenderPoints']), ('in', ['RenderUnit.Inches'], ['RenderInches']), ('map', ['RenderUnit.MapUnits', 'RenderUnit.MetersInMapUnits'], ['RenderMapUnits', 'RenderMetersInMapUnits'])]
    from qgis.core import QgsUnitTypes
    for kind, new_names, old_names in checks:
        for n in new_names:
            v = _enum_value(Qgis, n)
            if v is not None and unit == v:
                return kind
        for n in old_names:
            v = getattr(QgsUnitTypes, n, None)
            if v is not None and unit == v:
                return kind
    return 'mm'

def to_px(value, unit):
    k = _unit_kind(unit)
    v = float(value)
    if k == 'px':
        return v
    if k == 'pt':
        return v * 1.333
    if k == 'in':
        return v * 96
    if k == 'map':
        return 2.0
    return v * 3.78

def hex_color(qcolor):
    return qcolor.name()

def symbol_style(symbol, kind):
    out = {'color': hex_color(symbol.color())}
    alpha = symbol.color().alphaF() * symbol.opacity()
    sl = symbol.symbolLayer(0) if symbol.symbolLayerCount() else None
    if kind == 'polygon':
        try:
            from qgis.PyQt.QtCore import Qt
            if sl is not None and hasattr(sl, 'brushStyle') and (sl.brushStyle() == Qt.NoBrush):
                alpha = 0.0
        except Exception:
            pass
        out['opacity'] = round(alpha, 2)
        if sl is not None and hasattr(sl, 'strokeColor'):
            pen_none = False
            try:
                from qgis.PyQt.QtCore import Qt
                pen_none = sl.strokeStyle() == Qt.NoPen
            except Exception:
                pass
            if pen_none or sl.strokeColor().alpha() == 0:
                out['outline'] = 'none'
            else:
                out['outline'] = hex_color(sl.strokeColor())
                if hasattr(sl, 'strokeWidth'):
                    out['outlineWidth'] = round(max(0.5, to_px(sl.strokeWidth(), sl.strokeWidthUnit())), 1)
    elif kind == 'line':
        out['opacity'] = round(alpha, 2)
        try:
            unit = symbol.widthUnit() if hasattr(symbol, 'widthUnit') else symbol.outputUnit()
            out['width'] = round(max(0.8, to_px(symbol.width(), unit)), 1)
        except Exception:
            out['width'] = 2.5
    else:
        out['opacity'] = round(alpha, 2)
        try:
            unit = symbol.sizeUnit() if hasattr(symbol, 'sizeUnit') else symbol.outputUnit()
            out['radius'] = round(max(3, to_px(symbol.size(), unit) / 2), 1)
        except Exception:
            out['radius'] = 6
        if sl is not None and hasattr(sl, 'strokeColor') and (sl.strokeColor().alpha() > 0):
            out['outline'] = hex_color(sl.strokeColor())
    if out.get('opacity') == 1.0:
        out.pop('opacity')
    return out

def data_defined_size(layer, kind, warnings):
    if kind != 'point':
        return None
    r = layer.renderer()
    syms, keep = ([], None)
    try:
        if isinstance(r, QgsSingleSymbolRenderer):
            syms = [r.symbol()]
        elif isinstance(r, QgsCategorizedSymbolRenderer):
            keep = r.categories()
            syms = [r.sourceSymbol()] + [c.symbol() for c in keep]
        elif isinstance(r, QgsGraduatedSymbolRenderer):
            syms = [r.sourceSymbol()]
    except Exception:
        return None
    field_names = [f.name() for f in layer.fields()]
    for sym in syms:
        if sym is None or not hasattr(sym, 'dataDefinedSize'):
            continue
        prop = sym.dataDefinedSize()
        if not prop or not prop.isActive():
            continue
        field = prop.field() if prop.propertyType() == prop.FieldBasedProperty else None
        if not field:
            m = re.fullmatch('\\s*"?([^"()]+)"?\\s*', prop.expressionString() or '')
            field = m.group(1) if m else None
        t = prop.transformer()
        if field not in field_names or t is None or (not hasattr(t, 'minSize')):
            warnings.append('"%s": data-defined size is only copied when it scales a single field with the size assistant.' % layer.name())
            return None
        exponent = getattr(t, 'exponent', lambda: 0.5)()
        kind_name = str(t.type()).split('.')[-1].lower()
        if 'linear' in kind_name:
            exponent = 1.0
        elif 'flannery' in kind_name:
            exponent = 0.5716
        elif 'area' in kind_name:
            exponent = 0.5
        unit = sym.sizeUnit() if hasattr(sym, 'sizeUnit') else sym.outputUnit()
        return {'field': field, 'min': t.minValue(), 'max': t.maxValue(), 'minRadius': round(to_px(t.minSize(), unit) / 2, 2), 'maxRadius': round(to_px(t.maxSize(), unit) / 2, 2), 'exponent': round(float(exponent), 4)}
    return None

def json_value(v):
    try:
        if v is None or (hasattr(v, 'isNull') and v.isNull()):
            return None
    except Exception:
        pass
    if isinstance(v, (int, float, str, bool)):
        return v
    try:
        return v.toString() if hasattr(v, 'toString') else str(v)
    except Exception:
        return str(v)

def layer_style(layer, kind, warnings):
    r = layer.renderer()
    field_names = [f.name() for f in layer.fields()]
    if isinstance(r, QgsCategorizedSymbolRenderer):
        attr = r.classAttribute()
        if attr in field_names:
            cats = r.categories()
            src = r.sourceSymbol()
            base = symbol_style(src if src is not None else cats[0].symbol(), kind) if cats else {}
            style = {k: v for k, v in base.items() if k != 'color'}
            style.update({'kind': 'categorized', 'field': attr, 'categories': []})
            hidden = []
            for c in cats:
                if not c.renderState():
                    hidden += [json_value(v) for v in (c.value() if isinstance(c.value(), list) else [c.value()])]
                    continue
                sym = c.symbol()
                col = hex_color(sym.color())
                size = symbol_style(sym, kind)
                vals = c.value() if isinstance(c.value(), list) else [c.value()]
                for v in vals:
                    v = json_value(v)
                    if v is None or v == '':
                        style['defaultColor'] = col
                        continue
                    item = {'value': v, 'label': c.label() or str(v), 'color': col}
                    if kind == 'line' and 'width' in size:
                        item['width'] = size['width']
                    if kind == 'point' and 'radius' in size:
                        item['radius'] = size['radius']
                    style['categories'].append(item)
            hidden = [v for v in hidden if v not in (None, '')]
            if hidden:
                style['hideValues'] = hidden
            for key in ('width', 'radius'):
                vals = set((c.get(key) for c in style['categories']))
                if len(vals) == 1 and None not in vals:
                    style[key] = vals.pop()
                    for c in style['categories']:
                        c.pop(key, None)
            return style
        warnings.append('"%s": categories use an expression, not a field, so one colour is used.' % layer.name())
    if isinstance(r, QgsGraduatedSymbolRenderer):
        attr = r.classAttribute()
        rngs = r.ranges()
        if attr in field_names and rngs:
            base = symbol_style(rngs[0].symbol(), kind)
            style = {k: v for k, v in base.items() if k != 'color'}
            style.update({'kind': 'graduated', 'field': attr, 'ranges': []})
            for rg in rngs:
                if not rg.renderState():
                    continue
                sym = rg.symbol()
                item = {'min': rg.lowerValue(), 'max': rg.upperValue(), 'label': rg.label(), 'color': hex_color(sym.color())}
                size = symbol_style(sym, kind)
                if kind == 'line' and 'width' in size:
                    item['width'] = size['width']
                if kind == 'point' and 'radius' in size:
                    item['radius'] = size['radius']
                style['ranges'].append(item)
            for key in ('width', 'radius'):
                vals = set((x.get(key) for x in style['ranges']))
                if len(vals) == 1 and None not in vals:
                    style[key] = vals.pop()
                    for x in style['ranges']:
                        x.pop(key, None)
            return style
        warnings.append('"%s": graduated style uses an expression, so one colour is used.' % layer.name())
    if isinstance(r, QgsSingleSymbolRenderer) and r.symbol() is not None:
        style = symbol_style(r.symbol(), kind)
        style['kind'] = 'single'
        return style
    warnings.append('"%s": style type "%s" is not supported yet, so one colour is used. Single, categorized and graduated styles are copied exactly.' % (layer.name(), r.type() if r else 'none'))
    try:
        from qgis.core import QgsRenderContext
        syms = r.symbols(QgsRenderContext())
        if syms:
            style = symbol_style(syms[0], kind)
            style['kind'] = 'single'
            return style
    except Exception:
        pass
    return {'kind': 'single'}

def scale_to_zoom(scale):
    if not scale or scale <= 0:
        return None
    return round(max(0.0, min(22.0, math.log2(559082264.028 / scale))), 1)
TITLE_GUESS = ['name', 'title', 'label', 'nom', 'nombre', 'naam']
HIDDEN_FIELDS = re.compile('^(fid|ogc_fid|objectid|gid|shape_length|shape_area|shape_leng)$', re.I)

def title_field(layer, exclude=None):

    def usable(name):
        i = layer.fields().indexOf(name)
        if i < 0 or HIDDEN_FIELDS.match(name) or name == exclude:
            return False
        try:
            return layer.editorWidgetSetup(i).type() != 'Hidden'
        except Exception:
            return True
    names = [f.name() for f in layer.fields() if usable(f.name())]
    expr = (layer.displayExpression() or '').strip()
    m = re.fullmatch('"?([^"]+)"?', expr)
    if m and m.group(1) in names:
        return m.group(1)
    lower = {n.lower(): n for n in names}
    for g in TITLE_GUESS:
        if g in lower:
            return lower[g]
    for f in layer.fields():
        if f.name() in names and (f.type() == 10 or f.typeName().lower() in ('string', 'text', 'varchar')):
            return f.name()
    return None
PHOTO_VALUE = re.compile('\\.(jpe?g|png|webp|gif)(\\?.*)?$', re.I)

def photo_column(layer, sample=100):
    for i, f in enumerate(layer.fields()):
        if not (f.type() == 10 or f.typeName().lower() in ('string', 'text', 'varchar')):
            continue
        filled = hits = 0
        req = QgsFeatureRequest().setSubsetOfAttributes([i]).setFlags(QgsFeatureRequest.NoGeometry).setLimit(sample)
        for feat in layer.getFeatures(req):
            v = feat[i]
            if v is None or str(v).strip() in ('', 'NULL'):
                continue
            filled += 1
            hits += bool(PHOTO_VALUE.search(str(v).strip()))
        if filled and hits / float(filled) >= 0.6:
            return f.name()
    return None

def renderer_filter(layer):
    try:
        from qgis.core import QgsRenderContext
        r = layer.renderer().clone()
        rc = QgsRenderContext()
        rc.expressionContext().appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        r.startRender(rc, layer.fields())
    except Exception:
        return lambda f: True
    keep = [r]

    def drawn(f):
        try:
            rc.expressionContext().setFeature(f)
            return bool(keep[0].willRenderFeature(f, rc))
        except Exception:
            return True
    return drawn

def all_fields_hidden(layer):
    fields = layer.fields()
    if not len(fields):
        return False
    for i in range(len(fields)):
        try:
            if layer.editorWidgetSetup(i).type() != 'Hidden':
                return False
        except Exception:
            return False
    return True

def popup_fields(layer, title):
    out = []
    for i, f in enumerate(layer.fields()):
        if f.name() == title or HIDDEN_FIELDS.match(f.name()):
            continue
        try:
            if layer.editorWidgetSetup(i).type() == 'Hidden':
                continue
        except Exception:
            pass
        alias = layer.attributeAlias(i)
        item = {'field': f.name()}
        if alias:
            item['label'] = alias
        out.append(item)
    return out

def label_settings(layer):
    if not layer.labelsEnabled() or not isinstance(layer.labeling(), QgsVectorLayerSimpleLabeling):
        return None
    s = layer.labeling().settings()
    if s.isExpression or s.fieldName not in [f.name() for f in layer.fields()]:
        return None
    lab = {'field': s.fieldName}
    try:
        fmt = s.format()
        lab['color'] = hex_color(fmt.color())
        lab['size'] = round(max(9, min(22, to_px(fmt.size(), fmt.sizeUnit()))), 1)
    except Exception:
        pass
    try:
        if s.scaleVisibility and s.minimumScale:
            lab['minzoom'] = scale_to_zoom(s.minimumScale)
    except Exception:
        pass
    return lab

def safe_id(name, used):
    base = re.sub('[^a-z0-9]+', '_', name.lower()).strip('_') or 'layer'
    if base[0].isdigit():
        base = 'l_' + base
    cand, i = (base, 2)
    while cand in used:
        cand = '%s_%d' % (base, i)
        i += 1
    used.add(cand)
    return cand

def densified_copy(layer, max_zoom, context):
    from qgis.core import QgsPointXY
    from qgis.core import QgsFeature
    merc = QgsCoordinateReferenceSystem('EPSG:3857')
    interval = 40075016.686 / 2 ** max_zoom / 32.0
    xf = QgsCoordinateTransform(layer.crs(), merc, context.transformContext())
    wkb = QgsWkbTypes.displayString(QgsWkbTypes.multiType(QgsWkbTypes.flatType(layer.wkbType())))
    mem = QgsVectorLayer('%s?crs=EPSG:3857' % wkb, layer.name(), 'memory')
    pr = mem.dataProvider()
    pr.addAttributes(layer.fields().toList())
    mem.updateFields()
    feats = []
    for f in layer.getFeatures():
        nf = QgsFeature(mem.fields())
        nf.setAttributes(f.attributes())
        if f.hasGeometry():
            g = f.geometry()
            try:
                g.transform(xf)
                g = g.densifyByDistance(interval)
                g.convertToMultiType()
                parts = [list(pl) + [QgsPointXY(pl[-1])] for pl in g.asMultiPolyline() if len(pl) >= 2]
                if not parts:
                    continue
                nf.setGeometry(QgsGeometry.fromMultiPolylineXY(parts))
            except Exception:
                continue
        feats.append(nf)
    pr.addFeatures(feats)
    return mem
TILE_LIMIT = 250000

def _tiles_in_bbox(w, s, e, n, z):

    def tx(lon):
        return int((lon + 180.0) / 360.0 * (1 << z))

    def ty(lat):
        lat = max(-85.0511, min(85.0511, lat))
        r = math.radians(lat)
        return int((1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * (1 << z))
    m = (1 << z) - 1
    x0, x1 = (max(0, min(m, tx(w))), max(0, min(m, tx(e))))
    y0, y1 = (max(0, min(m, ty(n))), max(0, min(m, ty(s))))
    return (x1 - x0 + 1) * (y1 - y0 + 1)

def plan_zooms(layers, minz, maxz, context, feedback):
    wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
    area, pts, npts = (None, None, 0)
    for lyr in layers:
        try:
            ext = QgsCoordinateTransform(lyr.crs(), wgs84, context.transformContext()).transformBoundingBox(lyr.extent())
        except Exception:
            continue
        if ext.isEmpty() and geometry_kind(lyr) != 'point':
            continue
        if geometry_kind(lyr) == 'point':
            npts += max(0, lyr.featureCount())
            pts = QgsRectangle(ext) if pts is None else pts.combineExtentWith(ext) or pts
        else:
            area = QgsRectangle(ext) if area is None else area.combineExtentWith(ext) or area
    full = None
    for r in (area, pts):
        if r is not None:
            full = QgsRectangle(r) if full is None else full.combineExtentWith(r) or full
    if full is None:
        return (maxz or 14, 0)

    def estimate(zmax):
        total = 0
        for z in range(minz, zmax + 1):
            a = _tiles_in_bbox(area.xMinimum(), area.yMinimum(), area.xMaximum(), area.yMaximum(), z) if area else 0
            p = min(_tiles_in_bbox(pts.xMinimum(), pts.yMinimum(), pts.xMaximum(), pts.yMaximum(), z), npts) if pts else 0
            total += max(a, p)
        return total
    safe = minz
    for z in range(minz, 19):
        if estimate(z) > TILE_LIMIT:
            break
        safe = z
    if maxz <= 0:
        span = max(full.width(), full.height(), 0.0001)
        auto = int(math.ceil(math.log2(360.0 / span) + 7))
        maxz = max(4, min(16, auto, max(safe, minz)))
        feedback.pushInfo('Max zoom chosen automatically: %d (for an area about %s° across).' % (maxz, '%.0f' % span if span >= 10 else '%.2g' % span))
    elif estimate(maxz) > TILE_LIMIT:
        raise QgsProcessingException('Max zoom %d would create about %s tiles for an area this large. That could take hours and several GB. Set Max zoom to %d or lower, or 0 for automatic. Points and lines still look sharp when zoomed in further.' % (maxz, format(estimate(maxz), ','), safe))
    return (maxz, estimate(maxz))

def render_thumbnail(layers, bounds, path, context, size=(1200, 630)):
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor
    from qgis.core import QgsMapRendererSequentialJob, QgsMapSettings
    merc = QgsCoordinateReferenceSystem('EPSG:3857')
    xf = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'), merc, context.transformContext())
    ext = xf.transformBoundingBox(QgsRectangle(*bounds))
    w, h = size
    cx, cy = (ext.center().x(), ext.center().y())
    ew, eh = (max(ext.width(), 1.0) * 1.08, max(ext.height(), 1.0) * 1.08)
    if ew / eh < w / h:
        ew = eh * w / h
    else:
        eh = ew * h / w
    order = [n.layer() for n in QgsProject.instance().layerTreeRoot().findLayers()]
    chosen = {l.id() for l in layers}
    ms = QgsMapSettings()
    ms.setLayers([l for l in order if l and l.id() in chosen] or list(layers))
    ms.setDestinationCrs(merc)
    ms.setTransformContext(context.transformContext())
    ms.setExtent(QgsRectangle(cx - ew / 2, cy - eh / 2, cx + ew / 2, cy + eh / 2))
    ms.setOutputSize(QSize(w, h))
    ms.setBackgroundColor(QColor('#eef1f5'))
    ms.setFlag(QgsMapSettings.Antialiasing, True)
    job = QgsMapRendererSequentialJob(ms)
    job.start()
    job.waitForFinished()
    if not job.renderedImage().save(path, 'PNG'):
        raise RuntimeError("couldn't save the image")

class ExportWebMap(QgsProcessingAlgorithm):
    LAYERS = 'LAYERS'
    TITLE = 'TITLE'
    DESCRIPTION = 'DESCRIPTION'
    MIN_ZOOM = 'MIN_ZOOM'
    MAX_ZOOM = 'MAX_ZOOM'
    BASEMAP = 'BASEMAP'
    POINT_STYLE = 'POINT_STYLE'
    SEARCH = 'SEARCH'
    TEMPLATE = 'TEMPLATE'
    PUBLISH = 'PUBLISH'
    START_VIEW = 'START_VIEW'
    OUTPUT = 'OUTPUT'

    def tr(self, s):
        return QCoreApplication.translate('WebMapKit', s)

    def createInstance(self):
        return ExportWebMap()

    def name(self):
        return 'exportwebmap'

    def displayName(self):
        return self.tr('Export web map')

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr("Exports the chosen vector layers as a ready-to-publish web map: vector tiles (PMTiles), a search index and config.json with your QGIS colours, legend, labels and popups.\n\nLayers: leave empty to export every visible vector layer.\nMax zoom: leave at 0 and the tool picks one for your area (about 7 for the world, 14 for a country, 16 for a town).\nTemplate folder: the Web Map Kit 'template' folder. The map files are copied into the output folder the first time.\n\nAfter exporting, run serve.py in the output folder to preview.")

    def flags(self):
        f = super().flags()
        no_thread = _enum_value(Qgis, 'ProcessingAlgorithmFlag.NoThreading') or _enum_value(QgsProcessingAlgorithm, 'FlagNoThreading')
        return f | no_thread if no_thread is not None else f

    def initAlgorithm(self, config=None):
        vec = _enum_value(Qgis, 'ProcessingSourceType.VectorAnyGeometry')
        if vec is None:
            vec = QgsProcessing.TypeVectorAnyGeometry
        self.addParameter(QgsProcessingParameterMultipleLayers(self.LAYERS, self.tr('Layers to publish (empty = all visible)'), vec, optional=True))
        self.addParameter(QgsProcessingParameterString(self.TITLE, self.tr('Map title'), defaultValue=QgsProject.instance().title() or 'My web map'))
        self.addParameter(QgsProcessingParameterString(self.DESCRIPTION, self.tr('Short description (optional)'), defaultValue='', optional=True))
        self.addParameter(QgsProcessingParameterNumber(self.MIN_ZOOM, self.tr('Min zoom'), defaultValue=0, minValue=0, maxValue=20))
        self.addParameter(QgsProcessingParameterNumber(self.MAX_ZOOM, self.tr('Max zoom (detail level, 0 = automatic)'), defaultValue=0, minValue=0, maxValue=18))
        self.addParameter(QgsProcessingParameterEnum(self.BASEMAP, self.tr('Background map'), options=['Light grey (Positron)', 'Streets (Liberty)', 'Bright', 'None'], defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(self.POINT_STYLE, self.tr('Point markers'), options=['Circles (like QGIS)', 'Round icon badges', 'Map pins'], defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(self.SEARCH, self.tr('Add a search box'), defaultValue=True))
        folder = _enum_value(QgsProcessingParameterFile, 'Folder')
        if folder is None:
            folder = _enum_value(Qgis, 'ProcessingFileParameterBehavior.Folder')
        tpl = QgsProcessingParameterFile(self.TEMPLATE, self.tr('Custom web app folder (advanced, optional)'), behavior=folder, optional=True)
        adv = _enum_value(Qgis, 'ProcessingParameterFlag.Advanced') or _enum_value(QgsProcessingParameterDefinition, 'FlagAdvanced')
        if adv is not None:
            tpl.setFlags(tpl.flags() | adv)
        self.addParameter(tpl)
        sv = QgsProcessingParameterExtent(self.START_VIEW, self.tr('Start view (empty = fit all layers)'), optional=True)
        if adv is not None:
            sv.setFlags(sv.flags() | adv)
        self.addParameter(sv)
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUTPUT, self.tr('Output map folder')))
        self.addParameter(QgsProcessingParameterBoolean(self.PUBLISH, self.tr('Publish to Cloudflare R2 when done (uses the settings saved by the Publish tool)'), defaultValue=False))

    def _layers(self, parameters, context):
        chosen = self.parameterAsLayerList(parameters, self.LAYERS, context) or []
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        if not chosen:
            chosen = [n.layer() for n in root.findLayers() if n.isVisible() and isinstance(n.layer(), QgsVectorLayer)]
        chosen = [l for l in chosen if isinstance(l, QgsVectorLayer) and l.isValid() and geometry_kind(l)]
        order = [l.id() for l in root.layerOrder()]
        chosen.sort(key=lambda l: order.index(l.id()) if l.id() in order else -1, reverse=True)
        return chosen

    def _start_view(self, parameters, context, feedback):
        if not parameters.get(self.START_VIEW):
            return None
        try:
            ext = self.parameterAsExtent(parameters, self.START_VIEW, context)
            crs = self.parameterAsExtentCrs(parameters, self.START_VIEW, context)
            if ext is None or ext.isEmpty():
                return None
            if crs.isValid():
                xf = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem('EPSG:4326'), context.transformContext())
                ext = xf.transformBoundingBox(ext)
            w, s_, e, n = (max(-180, ext.xMinimum()), max(-WEB_MERCATOR_MAX_LAT, ext.yMinimum()), min(180, ext.xMaximum()), min(WEB_MERCATOR_MAX_LAT, ext.yMaximum()))
            if w >= e or s_ >= n:
                return None
            feedback.pushInfo('The map opens at your QGIS view (%.2f, %.2f to %.2f, %.2f).' % (w, s_, e, n))
            return (w, s_, e, n)
        except Exception as ex:
            feedback.pushInfo('(Start view not used: %s. The map opens showing all layers.)' % ex)
            return None

    def processAlgorithm(self, parameters, context, feedback):
        layers = self._layers(parameters, context)
        if not layers:
            raise QgsProcessingException('No vector layers to export. Tick some layers or choose them in the dialog.')
        title = self.parameterAsString(parameters, self.TITLE, context) or 'My web map'
        minz = self.parameterAsInt(parameters, self.MIN_ZOOM, context)
        maxz = self.parameterAsInt(parameters, self.MAX_ZOOM, context)
        maxz, est = plan_zooms(layers, minz, maxz, context, feedback)
        if minz > maxz:
            raise QgsProcessingException('Min zoom must be lower than max zoom.')
        basemap = BASEMAPS[self.parameterAsEnum(parameters, self.BASEMAP, context)]
        point_style = POINT_STYLES[self.parameterAsEnum(parameters, self.POINT_STYLE, context)]
        want_search = self.parameterAsBool(parameters, self.SEARCH, context)
        template = self.parameterAsFile(parameters, self.TEMPLATE, context) or WEB_APP_DIR
        description = self.parameterAsString(parameters, self.DESCRIPTION, context) or ''
        out_dir = self.parameterAsString(parameters, self.OUTPUT, context)
        start_view = self._start_view(parameters, context, feedback)
        os.makedirs(os.path.join(out_dir, 'data'), exist_ok=True)
        if not os.path.exists(os.path.join(template, 'index.html')):
            raise QgsProcessingException('The web app folder is missing index.html: %s' % template)
        for item in os.listdir(template):
            if item in ('data', 'config.json') or item.startswith('.'):
                continue
            src, dst = (os.path.join(template, item), os.path.join(out_dir, item))
            if item == 'style.css' and os.path.exists(dst):
                continue
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        warnings, used_ids, cfg_layers = ([], set(), [])
        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        tile_layers, keep_alive = ([], [])
        full_extent = None
        search_rows = []
        root = QgsProject.instance().layerTreeRoot()
        feedback.setProgressText('Reading styles…')
        for layer in layers:
            kind = geometry_kind(layer)
            lid = safe_id(layer.name(), used_ids)
            xf = QgsCoordinateTransform(layer.crs(), wgs84, context.transformContext())
            try:
                ext = xf.transformBoundingBox(layer.extent())
                if layer.featureCount() != 0 and (not ext.isNull()) and (ext.width() >= 0) and (ext.height() >= 0):
                    if full_extent is None:
                        full_extent = QgsRectangle(ext)
                    else:
                        full_extent.combineExtentWith(ext)
            except Exception:
                pass
            style = layer_style(layer, kind, warnings)
            dds = data_defined_size(layer, kind, warnings)
            if dds:
                style['radiusBy'] = dds
            if layer.opacity() < 1:
                style['opacity'] = round(style.get('opacity', 1.0 if kind != 'polygon' else 0.7) * layer.opacity(), 2)
            if kind == 'point':
                style['marker'] = 'circle' if style.get('radiusBy') else point_style
            photo_col = photo_column(layer)
            if photo_col:
                feedback.pushInfo('    "%s" holds photos: left out of the popup. Photos in popups are in Web Map Kit (full): https://mapship.link/webmap-kit/' % photo_col)
            tfield = title_field(layer, exclude=photo_col)
            entry = {'id': lid, 'name': layer.name(), 'type': kind, 'sourceLayer': lid, 'style': style, 'popup': {'title': tfield, 'fields': popup_fields(layer, tfield)} if tfield else {'fields': popup_fields(layer, None)}}
            if photo_col:
                entry['popup']['fields'] = [x for x in entry['popup']['fields'] if x['field'] != photo_col]
            if all_fields_hidden(layer):
                entry['popup'] = False
            if style.get('radiusBy'):
                entry['sizeLabel'] = layer.attributeDisplayName(layer.fields().indexOf(style['radiusBy']['field']))
            node = root.findLayer(layer.id())
            if node is not None and (not node.isVisible()):
                entry['visible'] = False
            if layer.hasScaleBasedVisibility():
                zmin_l, zmax_l = (scale_to_zoom(layer.minimumScale()), scale_to_zoom(layer.maximumScale()))
                if zmin_l is not None:
                    entry['minzoom'] = zmin_l
                if zmax_l is not None:
                    entry['maxzoom'] = zmax_l
            src = layer
            if kind == 'line':
                src = densified_copy(src, maxz, context)
                keep_alive.append(src)
            rb = style.get('radiusBy')
            if kind == 'point' and rb and (layer.featureCount() > 5000) and (minz <= 3 < maxz):
                vals = sorted((v for v in (json_value(f[rb['field']]) for f in layer.getFeatures(QgsFeatureRequest().setSubsetOfAttributes([rb['field']], layer.fields()).setFlags(QgsFeatureRequest.NoGeometry))) if isinstance(v, (int, float))))
                cut = vals[int(len(vals) * 0.8)] if vals else None
                if cut is not None:
                    low = QgsVectorTileWriter.Layer(src)
                    low.setLayerName(lid)
                    low.setMaxZoom(3)
                    low.setFilterExpression('"%s" >= %s' % (rb['field'].replace('"', '""'), repr(float(cut))))
                    high = QgsVectorTileWriter.Layer(src)
                    high.setLayerName(lid)
                    high.setMinZoom(4)
                    tile_layers.extend([low, high])
                    feedback.pushInfo('    zoomed out (0–3): showing the largest 20%% (%s ≥ %s); all features from zoom 4' % (rb['field'], format(cut, 'g')))
                else:
                    rb = None
            if not (kind == 'point' and rb and (layer.featureCount() > 5000) and (minz <= 3 < maxz)):
                tl = QgsVectorTileWriter.Layer(src)
                tl.setLayerName(lid)
                tile_layers.append(tl)
            lab = label_settings(layer)
            if lab:
                if kind == 'polygon':
                    pts = QgsVectorLayer('Point?crs=%s' % layer.crs().authid(), lid + '_labels', 'memory')
                    pr = pts.dataProvider()
                    pr.addAttributes([layer.fields().field(lab['field'])])
                    pts.updateFields()
                    feats = []
                    drawn = renderer_filter(layer)
                    for f in layer.getFeatures():
                        if not f.hasGeometry() or not drawn(f):
                            continue
                        g = f.geometry().pointOnSurface()
                        if g.isEmpty():
                            continue
                        from qgis.core import QgsFeature
                        nf = QgsFeature(pts.fields())
                        nf.setGeometry(g)
                        nf.setAttributes([f[lab['field']]])
                        feats.append(nf)
                    pr.addFeatures(feats)
                    keep_alive.append(pts)
                    tl2 = QgsVectorTileWriter.Layer(pts)
                    tl2.setLayerName(lid + '_labels')
                    tile_layers.append(tl2)
                    lab['sourceLayer'] = lid + '_labels'
                entry['label'] = lab
            if want_search and tfield and (entry.get('popup') is not False):
                count = 0
                drawn = renderer_filter(layer)
                for f in layer.getFeatures():
                    v = json_value(f[tfield])
                    if v in (None, '') or not f.hasGeometry() or (not drawn(f)):
                        continue
                    try:
                        g = f.geometry()
                        g = g if kind == 'point' else g.pointOnSurface()
                        g.transform(xf)
                        p = g.asPoint() if not g.isMultipart() else g.asMultiPoint()[0]
                        search_rows.append({'t': str(v), 'c': [round(p.x(), 6), round(p.y(), 6)], 'l': layer.name(), '_vis': entry.get('visible', True), '_order': len(cfg_layers)})
                        count += 1
                    except Exception:
                        continue
                    if count >= 50000:
                        warnings.append('"%s": search index limited to the first 50,000 features.' % layer.name())
                        break
            cfg_layers.append(entry)
            feedback.pushInfo('  %s: %s, %s style' % (layer.name(), kind, style.get('kind')))
        feedback.setProgressText('Writing vector tiles (zoom %d–%d)…' % (minz, maxz))
        feedback.pushInfo('Writing up to about %s tiles (zoom %d–%d). Large maps take a few minutes and QGIS may look frozen until it finishes.' % (format(est, ','), minz, maxz))
        feedback.setProgress(5)
        tmp_mb = os.path.join(out_dir, 'data', '_export_tmp.mbtiles')
        if os.path.exists(tmp_mb):
            os.remove(tmp_mb)
        writer = QgsVectorTileWriter()
        writer.setDestinationUri('type=mbtiles&url=%s' % tmp_mb)
        writer.setMinZoom(minz)
        writer.setMaxZoom(maxz)
        writer.setLayers(tile_layers)
        writer.setTransformContext(context.transformContext())
        if full_extent is not None and (not full_extent.isNull()):
            pad = max(full_extent.width(), full_extent.height()) * 0.01 or 0.001
            roi = QgsRectangle(max(-180, full_extent.xMinimum() - pad), max(-WEB_MERCATOR_MAX_LAT, full_extent.yMinimum() - pad), min(180, full_extent.xMaximum() + pad), min(WEB_MERCATOR_MAX_LAT, full_extent.yMaximum() + pad))
            try:
                to_merc = QgsCoordinateTransform(wgs84, QgsCoordinateReferenceSystem('EPSG:3857'), context.transformContext())
                writer.setExtent(to_merc.transformBoundingBox(roi))
            except Exception:
                pass
        try:
            writer.setMetadata({'name': title, 'description': 'Made with Web Map Kit'})
        except Exception:
            pass
        if not writer.writeTiles(feedback):
            raise QgsProcessingException('QGIS could not write the vector tiles: %s' % writer.errorMessage())
        if feedback.isCanceled():
            return {}
        feedback.setProgressText('Converting to PMTiles…')
        if full_extent is not None and (not full_extent.isNull()) and (full_extent.width() == 0 or full_extent.height() == 0):
            full_extent.grow(0.005)
        if full_extent is None or full_extent.isEmpty():
            full_extent = QgsRectangle(-180, -WEB_MERCATOR_MAX_LAT, 180, WEB_MERCATOR_MAX_LAT)
        bounds = (max(-180, full_extent.xMinimum()), max(-WEB_MERCATOR_MAX_LAT, full_extent.yMinimum()), min(180, full_extent.xMaximum()), min(WEB_MERCATOR_MAX_LAT, full_extent.yMaximum()))
        pm_path = os.path.join(out_dir, 'data', 'data.pmtiles')
        stats = mbtiles_to_pmtiles(tmp_mb, pm_path, bounds, feedback)
        os.remove(tmp_mb)
        cfg_path = os.path.join(out_dir, 'config.json')
        if os.path.exists(cfg_path):
            shutil.copy2(cfg_path, os.path.join(out_dir, 'config.backup.json'))
        ver = '%d' % time.time()
        cfg = {'title': title, 'description': description, 'basemap': basemap, 'data': 'data/data.pmtiles?v=' + ver, 'bounds': [round(b, 6) for b in bounds], 'layers': cfg_layers}
        cfg['badge'] = True
        if start_view:
            cfg['view'] = [round(b, 6) for b in start_view]
        if want_search and search_rows:
            search_rows.sort(key=lambda r: (not r['_vis'], -r['_order']))
            seen, rows = (set(), [])
            for r in search_rows:
                key = (r['t'].strip().lower(), round(r['c'][0], 2), round(r['c'][1], 2))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({'t': r['t'], 'c': r['c'], 'l': r['l']})
            search_rows = sorted(rows, key=lambda r: r['t'].lower())
            with open(os.path.join(out_dir, 'data', 'search.json'), 'w', encoding='utf-8') as f:
                json.dump(search_rows, f, ensure_ascii=False, separators=(',', ':'))
            cfg['search'] = {'index': 'data/search.json?v=' + ver, 'placeholder': 'Search…'}
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        stamp_assets(os.path.join(out_dir, 'index.html'), ver)
        feedback.setProgress(100)
        try:
            render_thumbnail(layers, start_view or bounds, os.path.join(out_dir, 'preview.png'), context)
        except Exception as e:
            feedback.pushInfo('(No thumbnail: %s)' % e)
        mb = stats['bytes'] / 1048576.0
        feedback.pushInfo('')
        feedback.pushInfo('Done. %d layers, %d tiles (zoom %d–%d), data.pmtiles is %.1f MB.' % (len(cfg_layers), stats['tiles'], stats['minzoom'], stats['maxzoom'], mb))
        if stats.get('dropped'):
            feedback.pushInfo('Removed %d empty feature pieces from the tiles (a known QGIS writer quirk; nothing is missing).' % stats['dropped'])
        if mb > 25:
            feedback.pushInfo("Note: files over 25 MB can't go on Cloudflare Pages directly; upload data.pmtiles to R2 (see guide, step 4).")
        for w in warnings:
            feedback.pushWarning(w) if hasattr(feedback, 'pushWarning') else feedback.reportError(w, False)
        result = {self.OUTPUT: out_dir, 'PMTILES': pm_path, 'TILES': stats['tiles']}
        if self.parameterAsBool(parameters, self.PUBLISH, context):
            result['URL'] = self._publish(out_dir, title, context, feedback)
        else:
            feedback.pushInfo('Preview: open a terminal in %s and run:  python3 serve.py' % out_dir)
        return result

    def _publish(self, out_dir, title, context, feedback):
        from qgis.core import QgsApplication, QgsSettings
        alg = None
        for aid in ('script:publishwebmapr2', PROVIDER_ID + ':publishwebmapr2'):
            alg = QgsApplication.processingRegistry().algorithmById(aid)
            if alg:
                break
        st = QgsSettings()
        creds = {k: st.value('webmapkit/r2/' + k, '') for k in ('account', 'bucket', 'access_key', 'secret', 'public_url')}
        if alg is None:
            feedback.reportError('To publish, also add webmap_kit_publish.py to the Toolbox (see README).', False)
            return ''
        if not all((creds[k] for k in ('account', 'bucket', 'access_key', 'secret'))):
            feedback.reportError("To publish, run 'Publish web map to Cloudflare R2' once with 'Remember these settings' ticked.", False)
            return ''
        feedback.pushInfo('')
        feedback.pushInfo('Publishing to Cloudflare R2…')
        import processing
        res = processing.run(alg.id(), {'FOLDER': out_dir, 'MAP_NAME': '', 'ACCOUNT': creds['account'], 'BUCKET': creds['bucket'], 'ACCESS_KEY': creds['access_key'], 'SECRET': creds['secret'], 'PUBLIC_URL': creds['public_url'], 'REMEMBER': False}, context=context, feedback=feedback, is_child_algorithm=True)
        return res.get('URL', '')
