# data/gis

Delaware FirstMap hydrography layers, downloaded once by `scripts/fetch_gis.py`.
Nothing in this project fetches geospatial data at runtime.

Five layers from the state Hydrology services cover all surface water in Delaware:
major rivers, flowlines, lakes and ponds, public ponds, and tax ditches. Stored as
gzipped GeoJSON in WGS84. Total size is about 3.3 MB compressed.

No public well location layer exists, so well distances cannot be screened from
coordinates and must be read off the site plan.

Start here: `SOURCE.md` documents provenance, simplification tolerances, feature
counts and the completeness check. `manifest.json` records the service totals at
download time.
