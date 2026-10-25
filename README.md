# שלטים — photographic archive

A static Leaflet photography map with a Hebrew visual editor that saves directly to local project files. Original photographs are never modified. The blue/pink street map, street names, photographs, and supplied Hebrew font are served locally.

## Run the archive and editor

From this project folder in the VS Code terminal:

```sh
.venv/bin/python scripts/serve.py
```

Open **http://127.0.0.1:8001**. In **תפריט**, choose **עריכת הארכיון**. Stop with **Ctrl+C**. If that port is already occupied, stop your previous server or run `.venv/bin/python scripts/serve.py --port 8002` and use the printed URL.

For first-time setup on another machine:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/prepare.py
.venv/bin/python scripts/serve.py
```

No Node.js, database, account, or hosted backend is needed. The helper listens only on `127.0.0.1`. It permits editor requests from its own origin and writes only archive metadata, generated derivatives, backups, and address cache. Do not expose this local authoring helper as a public server.

## Tags in the UI

1. Open **עריכת הארכיון** and select signs using their checkboxes. **בחירת הכול** selects all; **ביטול הבחירה** clears selection. Click a thumbnail to preview its original.
2. Type in **הוספת תגית**. Matching tags appear as suggestions; choose one with a click or the arrow keys and Enter. If it does not exist, choose **יצירת תגית** to create and apply it in one action.
3. Click a chip under **כל התגיות** to check all signs carrying that tag. This replaces the previous selection without changing any tags or creating an unsaved edit. An unused tag selects no signs. To apply a tag, use the input and its suggestions. A new tag can be created before selecting signs and assigned later.
4. Assigned tags appear as removable chips under **תגיות בשלטים שנבחרו**. Click **×** to remove one from the selected signs. A fraction such as `2/3` means only two of the three selected signs currently carry that tag.
5. Press **שמירה** (or Cmd/Ctrl+S) and wait for **כל השינויים נשמרו בקבצים**.

There are no category or value fields in the UI. Suggestions and the library reuse all existing labels, including earlier grouped assignments. Whitespace and capitalization are normalized when matching tags to avoid accidental duplicates. Adding a tag preserves the other assigned tags; removing it leaves the tag available in the library.

Map filters are single-choice text options. Choosing a tag replaces the previous selection; clicking the active tag again returns to **הכול**. **הכול** also resets the selection. Only the active option is bold and underlined; the others are plain text. Options have no pill outlines, fills, or horizontal padding, so they share the menu’s right-hand text alignment. Only tags assigned to signs appear as map filters. Creating an unused tag does not add an empty visitor filter. No classifications are guessed.

Available tags are stored in the existing `config.json` format under `categoryValues.tags`; new assignments use the `tags` bucket inside each photo's annotations. This compatibility layer lets the simplified editor work with the existing local save server, preserving older labels, addresses, coordinates, and unrelated settings. The old grouping is not shown to users.

## Addresses and location correction

1. In the editor, click **כתובת ומיקום** on any photo, including photos already positioned from GPS.
2. Enter the street, house number, and city. Press **חיפוש כתובת** to search; typing alone makes no requests.
3. Select a result. The editor becomes a compact panel so you can see the map. Review the pin, drag it, or click the map to adjust it. You can also use **בחירת מיקום במפה** without an address lookup, or enter latitude/longitude directly.
4. Press **אישור המיקום** to accept the address and coordinates into your draft. **ביטול התיקון** restores the previously confirmed values. A search result or unconfirmed pin never changes saved coordinates.
5. Press **שמירה** to write the confirmed correction to disk. **חזרה לתמונות** returns to the grid without confirming the pending pin.

The address and confirmed coordinates are stored together in the photograph's annotation entry. Manual coordinates override EXIF; `null` uses the original GPS. Address lookups are approximate: review the actual sign/building location before confirming.

The chosen provider is [OpenStreetMap Nominatim](https://nominatim.org/release-docs/latest/api/Search/), configured by `geocoding.endpoint` in `config.json`. Follow its [usage policy](https://operations.osmfoundation.org/policies/nominatim/): this editor searches only on a button press, uses a single serialized request queue with at least 1.1 seconds between requests, caches results, and offers no autocomplete or background bulk lookup. Only the address and search parameters are sent; no photograph, filename, or tag is sent. Ordinary archive viewing never requests geocoding. Failed/no-match searches leave the old location intact and manual placement remains available.

## How saving works

- **Project files are permanent storage.** The tag library goes to `config.json`; per-sign tags, addresses, and corrected coordinates go to `data/annotations.json`. `data/photos.json` is regenerated automatically after saving.
- **Browser localStorage holds recovery drafts only.** A draft is a browser recovery copy of unsaved edits; only **שמירה** writes them to the project. Closing the editor with unsaved changes asks whether to **שמירה** (save, then close) or **ביטול השינויים** (discard the edits and their active recovery draft, then close). **×** or **Esc** dismisses the question and keeps editing. A failed save keeps the window open and preserves the edits. Refreshing can restore a compatible recovery draft. Clearing browser storage does not remove edits already saved to project files.
- The server validates edits and prepares the archive before committing metadata. It backs up the previous three metadata files to `.editor/backups/`. Failed preparation leaves existing metadata unchanged; a failed commit restores it.
- If you also edit files in VS Code while the UI is open, a conflicting save is rejected. Your draft remains available. **טעינת הקבצים מחדש** loads the current project files and retains the previous draft for download, rather than overwriting newer work.
- Address-query results are cached separately in `.editor/geocode-cache.json`. `.editor/` is ignored by Git and should not be uploaded to a static host. Keep that folder if you want local backups and address-cache history.

## Add originals and cutout thumbnails

Put originals in **`signs_images/`**. Do not change existing filenames. Subfolders are supported.

For an irregular sign cutout:

1. Open a copy in your image editor, select the sign, and mask away the background. Keep the actual outline, including holes.
2. Trim excessive transparent canvas around it. Around **500–1000px** on the longest side is sufficient for these map thumbnails.
3. Export **PNG with transparency**, without flattening onto white or another color.
4. Save in **`cropped_images/`**, using the exact original filename stem:

```text
signs_images/PXL_20261019_133320019.jpg
cropped_images/PXL_20261019_133320019.png
```

Mirror subfolders. A cropped JPEG with the original filename also works for rectangular crops. Common export suffixes such as ` copy` and ` copy 2` are recognized automatically, without renaming your files. An exact filename/stem takes priority; keep only one matching copy otherwise. Then run:

```sh
.venv/bin/python scripts/prepare.py
```

Reload the archive. Preparation preserves orientation, embedded color profiles, and alpha, trims transparent margins, and creates small PNG derivatives for transparent sources and JPEG derivatives for opaque sources. Originals are read only. No matching crop means the original supplies the thumbnail. A completely transparent crop produces an error instead of an invisible sign.

Original JPEG/PNG/WebP/GIF/AVIF images open uncropped in the image window. TIFF/BMP receive full-resolution JPEG viewing copies in `assets/converted/`. For HEIC/HEIF support, install the optional decoder with `.venv/bin/python -m pip install pillow-heif` before preparation.

## Map behavior and styling

Photos stay close to their geographic points. Pairs at an identical location sit diagonally: one 10px up/left and the other 10px down/right. Larger shared-location groups use deterministic radial nudges within 10px, allowing overlap. There are no clusters or connector lines. Map thumbnails are normalized by visible (alpha-weighted) sign area rather than a fixed square, preserving each cutout’s proportions. Long narrow signs become wider so they carry similar visual weight to square signs. Target area grows with zoom; an extreme-shape length cap prevents future very thin crops from overwhelming the map. Preparation stores the thumbnail dimensions and visible area in `data/photos.json`, so sizing needs no browser image analysis. Click an exposed photo, or use keyboard focus. If photographs overlap, the image window's arrows let you browse the photos intersecting the clicked thumbnail. The window preserves image proportions and leaves the map visible; close with **×**, **Esc**, or a click outside. A strip beneath the image shows its tags (or **ללא תגיות**). On the local editor server, **עריכה** opens the editor with only that sign selected and its address controls ready. Opening the editor from an existing editing preview preserves the current unsaved draft.

The **תפריט** button stays in the same position when the drawer slides in/out. Close it using that button, the **×** at the top left of the drawer, or **Esc**. Clicking/panning the map or resetting filters keeps the drawer open. A fine near-white pink (`#ffedf2`, matching street labels) outline follows the cutout shape on map thumbnails and editor previews. **קנה מידה** is a zoom slider from **רחוק** to **קרוב**, grouped with **גודל השלטים** in the same display section. Its endpoints track the map’s minimum and maximum zoom, including after resizing; scrolling and pinching also update the slider. The menu omits a total sign count. **הכול**, inside the filter chips, clears the active filters without moving the map. The **גודל השלטים** slider adjusts thumbnail dimensions from 60% to 160%, preserving equal visual area across signs. This display preference is remembered in the current browser; it does not change project metadata or image files. The menu uses 18px primary text and 14px secondary text on one aligned column, with local editing at the bottom. Street names are Hebrew with the available local name as fallback. Their density increases with zoom and avoids photographs/other labels.

- `styles.css`: `--map-blue`, `--map-pink`, and `--street-label-color`, local font, menu, editor, and image window.
- `map-display.js`: thumbnail nudges and street-label placement.
- `config.json`: `areaPadding` controls overview context and `panPadding` limits panning. Both are fractions of the photo extent; panning adds half the current viewport around the permitted center area so wide, zoomed-out windows can still move sideways. The viewport stays within the downloaded street coverage when available; limits adjust with zoom and screen size. The current settings allow an extra zoom-out level and more panning around the signs. `streetCoveragePadding` controls the surrounding area downloaded when refreshing the local street snapshot. `basemap: "street"` restores standard tiles.
- `scripts/prepare_roads.py`: optionally refreshes `data/streets.geojson` and `data/street-labels.geojson`. It sends a bounding box around your photos to the previously chosen `overpass-api.de` service, without photos/tags/filenames. Refresh after adding photographs outside the saved street coverage.

## Static publishing

Upload `index.html`, `styles.css`, `app.js`, `editor.js`, `map-display.js`, `config.json`, the supplied `TheBasics-Medium…woff2` font, `vendor/`, `data/`, `assets/`, and `signs_images/`, preserving paths. Exclude `.venv/`, `.editor/`, `tests/`, `test-results/`, and `scripts/`. Crops are needed only when preparing derivatives locally.

The public archive is read-only: the authoring entry appears only when the local helper API is available on localhost. There is no public saving/geocoding backend. Root and subdirectory hosting are supported. Basic preview without editing still works with `python3 -m http.server 8001 --bind 127.0.0.1`.

Street data is © OpenStreetMap contributors, under the [Open Database License](https://opendatacommons.org/licenses/odbl/1-0/); source/license links are embedded in the snapshots and attribution remains visible. The optional tile mode follows the [OpenStreetMap tile usage policy](https://operations.osmfoundation.org/policies/tiles/). The Leaflet license is in `vendor/`; the retained clustering plugin is unused.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests use temporary photographs/metadata and a local mock address provider. They verify persistence, backups, conflicts, rollback, request restrictions, rate limits, caching, transparent cutouts, and original-file preservation without querying an external geocoder.

## Prepare for publishing (without uploading)

Save any editor changes first. From the project folder, run:

```sh
.venv/bin/python scripts/export_site.py
```

This creates **`dist/`** (the public website files) and **`signs-publish.zip`** (the same files zipped with `index.html` at the top level). Nothing is uploaded, registered, or published. Re-run this command after saving new edits or adding photos; these outputs are snapshots, not live copies.

The export includes the map, assigned tags, confirmed map positions, font, transparent thumbnails, and separate JPEG viewing images limited to 2048px on their longest edge. Originals stay unchanged locally. Exported viewing images omit EXIF metadata, including the original GPS; the map coordinates remain in the public map data. Public data omits typed addresses, original EXIF coordinates, and internal annotation details. Local editor forms and its script, APIs, geocoding configuration, recovery drafts, backups, raw crops, Python scripts, and original full-resolution photos are excluded. The exported map is read-only even when previewed on localhost.

To preview this exact export locally:

```sh
.venv/bin/python -m http.server 8002 --bind 127.0.0.1 --directory dist
```

Open **http://127.0.0.1:8002**; stop with **Ctrl+C**. This preview has no editing tools. Continue editing through `scripts/serve.py` on port 8001. When ready to publish later, use only `dist/` or `signs-publish.zip`, not the whole project folder. No hosting provider has been selected or configured yet.
