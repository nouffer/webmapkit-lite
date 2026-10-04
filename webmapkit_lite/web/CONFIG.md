# config.json reference

Everything about your map is set in `config.json`. The export script writes it for you; edit it by hand to fine-tune.

## Map settings

| Setting | Example | What it does |
| --- | --- | --- |
| `title` | `"Riverside parks"` | Title shown on the map and in the browser tab |
| `description` | `"Click a park for details."` | Short line under the title (hidden on phones) |
| `basemap` | `"positron"` | Background map: `positron` (light grey), `liberty`, `bright`, `none`, or any MapLibre style URL. Free from OpenFreeMap, no key needed |
| `data` | `"data/data.pmtiles"` | Your vector tiles. A relative path, or a full URL (e.g. your Cloudflare R2 file) |
| `dataAttribution` | `"© City of Riverside"` | Credit shown in the corner |
| `bounds` | `[81.80, 7.19, 81.86, 7.25]` | Optional start view: west, south, east, north. If left out, the map zooms to your data |
| `center`, `zoom` | `[81.83, 7.22]`, `13` | Optional fixed start view instead of `bounds` |
| `units` | `"metric"` | Scale bar: `metric`, `imperial` or `nautical` |
| `search` | `{ "index": "data/search.json" }` | Search box. The export script builds the index |
| `highlightColor` | `"#0f172a"` | Colour of the hover and selection highlight |
| `accentColor` | `"#0f766e"` | Colour of checkboxes, links and the loading bar |
| `tooltip` | `false` | Turn off the name tooltip that follows the mouse |
| `dataAboveLabels` | `true` | Draw your polygons and lines above the basemap's place names (default: below) |
| `layers` | `[ … ]` | Your layers, bottom to top (same order as QGIS, reversed) |

## Layer settings

| Setting | Example | What it does |
| --- | --- | --- |
| `id` | `"parks"` | Unique name, letters/numbers/underscores |
| `name` | `"Parks"` | Name shown in the legend |
| `type` | `"polygon"` | `polygon`, `line` or `point` |
| `sourceLayer` | `"parks"` | Layer name inside the PMTiles file (defaults to `id`) |
| `geojson` | `"data/parks.geojson"` | Use a GeoJSON file instead of tiles (good for small layers, under ~5 MB) |
| `visible` | `false` | Start with the layer switched off |
| `minzoom`, `maxzoom` | `12` | Only show the layer between these zoom levels |
| `legend` | `false` | Hide the layer from the legend |
| `style` | see below | Colours and sizes |
| `label` | `{ "field": "name", "minzoom": 14 }` | Text labels from a field. Also `size`, `color`, `halo`, and `sourceLayer` (a point layer to place polygon labels, so each shape gets one label; the export script creates it) |
| `popup` | see below | What appears when someone clicks a feature. `false` turns it off |

## Style

Common: `opacity` (0–1), `outline` (colour, or `"none"` for polygons), `outlineWidth`.
Lines: `width`, `casing` (outline colour around the line, or `"none"`).
Points: `marker` = `"circle"`, `"badge"` (round icon) or `"pin"` (map pin); `radius` for circles; `size` (e.g. `1.2`) for badges and pins; `icon` for the default icon.

Icons: `dot`, `school`, `health`, `shop`, `cafe`, `food`, `park`, `home`, `star`, `info`, `bus`, `water`, `warning`, `office`, `flag`. Set one per category with `"icon": "school"`. Add your own in `icons.js`.

Icon markers by category:

```json
"style": { "kind": "categorized", "field": "category", "marker": "badge",
  "categories": [ { "value": "School", "color": "#2f6fb0", "icon": "school" },
                  { "value": "Clinic", "color": "#d1495b", "icon": "health" } ] }
```

Single colour:

```json
"style": { "kind": "single", "color": "#7fbf6a", "opacity": 0.8 }
```

By category (like QGIS "Categorized"):

```json
"style": { "kind": "categorized", "field": "type", "defaultColor": "#cccccc",
  "categories": [ { "value": "Park", "color": "#7fbf6a", "label": "Public park" } ] }
```

By number ranges (like QGIS "Graduated"):

```json
"style": { "kind": "graduated", "field": "pop_density",
  "ranges": [ { "min": 0, "max": 2000, "color": "#edf8fb", "label": "Under 2,000" },
              { "min": 2000, "max": 99999, "color": "#88419d", "label": "2,000 and over" } ] }
```

## Popup

```json
"popup": { "title": "name",
  "fields": [ { "field": "area_ha", "label": "Area", "suffix": "ha" },
              { "field": "website", "label": "Website", "link": true, "linkText": "Visit" } ] }
```

Use `"fields": "*"` to show every attribute (ID columns are skipped).
Other popup option: `"hideEmpty": false` shows empty fields as "–".
Web links become buttons automatically; email addresses become clickable.
When several features overlap, the popup shows arrows to page through them. On phones it opens as a panel from the bottom.

## Preview on your computer

Run `python serve.py` in this folder and open http://localhost:8000.
Double-clicking `index.html` will not work: browsers block map data loaded from local files.
