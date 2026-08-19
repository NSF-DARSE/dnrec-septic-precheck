# GIS layer provenance

All layers here were downloaded once, by hand, with `python scripts/fetch_gis.py`.
Nothing in this project fetches geospatial data at runtime. The demo has to work
with the network unplugged, so a layer that is not in this directory does not
exist as far as the code is concerned.

## Source

Delaware FirstMap, the state's public geospatial platform, run by the Delaware
Department of Technology and Information.

ArcGIS REST root: `https://enterprise.firstmap.delaware.gov/arcgis/rest/services`

Downloaded: 2026-08-17

## Layers obtained

| file | service | layer | features | gzipped |
| --- | --- | --- | --- | --- |
| `surface_water_major_rivers.geojson.gz` | `Hydrology/DE_Water/FeatureServer` | 0 MajorRivers | 2,984 | 0.1 MB |
| `surface_water_flowlines.geojson.gz` | `Hydrology/DE_Water/FeatureServer` | 1 FlowLine | 60,712 | 1.5 MB |
| `surface_water_lakes_ponds.geojson.gz` | `Hydrology/DE_Water/FeatureServer` | 2 Lakes and Ponds | 14,711 | 1.3 MB |
| `public_ponds.geojson.gz` | `Hydrology/DE_Public_Ponds/FeatureServer` | 0 | 51 | 0.0 MB |
| `tax_ditches.geojson.gz` | `Hydrology/DE_TaxDitch/FeatureServer` | 0 | 21,449 | 0.4 MB |
| `roads_centerline.geojson.gz` | `Transportation/DE_Roadways_Main/FeatureServer` | 1 CENTER LINE | 78,444 | 2.3 MB |

The `DE_Water` service describes itself as "This water is derived from the NHD
Dataset. August 2014", so the surface water geometry is the National Hydrography
Dataset as published by the state.

## Completeness

Each layer holds every feature the service returns inside Delaware's bounding box,
`-75.80, 38.44, -74.98, 39.85` in WGS84, which covers Sussex, Kent and New Castle
counties.

This was checked rather than assumed, because a truncated water layer is worse
than a missing one: a permit beside an unmapped stream reads as far from water,
which is a false all clear rather than a visible gap. The service reports 123,960
flowlines and 39,498 lakes and ponds in total, and those larger numbers were
alarming until the counts were re-run with the same bounding box filter, which
returns 60,712 and 14,711 exactly. The difference is NHD features in the
surrounding watersheds outside the state. The bbox filtered counts match the
downloaded feature counts exactly, so nothing is missing.

`scripts/fetch_gis.py` records the unfiltered service total in `manifest.json` for
each layer so this can be rechecked later.

## What was reduced, and why that is acceptable

Geometry is generalised on the server through `maxAllowableOffset`, and
coordinates are rounded to five decimal places, about one metre.

| layer | simplification |
| --- | --- |
| MajorRivers | 0.0001 degrees, about 11 m |
| FlowLine | 0.0003 degrees, about 33 m |
| Lakes and Ponds | 0.0002 degrees, about 22 m |
| Tax ditches | 0.0003 degrees, about 33 m |
| Public ponds | none |

Unsimplified, the flowline network alone is 72 MB and the full set does not belong
in a repository. Simplified and gzipped the complete set is 3.3 MB.

The tradeoff has to be stated plainly, because it bounds what the output can
claim. A 33 metre generalisation on a stream centreline is a large fraction of the
100 foot, 30.5 metre, isolation distance in Exhibit C. That is one of several
reasons the geospatial output is a screening flag telling a reviewer to check the
site plan, and never a compliance determination. See the module docstring in
`src/septic/geo.py` for the full list.

Only the attributes a map label needs are kept: `GNIS_NAME`, `NAME`, `FTYPE`,
`FCODE`, `TAXDITCH`, `POND_NAME`, `OBJECTID`. The rest roughly doubled the file
and nothing used it.

## Wells

No public well location layer was found. `Hydrology` carries `DE_NCCO_WRPA`, a
New Castle County water resource protection area layer, but not well points.
`Environmental/DE_DNREC_Monitoring_Network` was not investigated far enough to
confirm whether it contains domestic wells, and monitoring wells would be the
wrong features for an Exhibit C setback anyway.

This matters, because the well setback is the most commonly binding isolation
distance in the regulation: 100 feet from the disposal area under Exhibit C. Well
locations for a real screening would have to come from DNREC's own well permit
records, which are not in the public FirstMap catalogue. The regulation itself
says as much: Section 5.2.1.5 requires the applicant's site drawing to show wells
within 150 feet, and describes a records search through the Department's Water
Supply Section when a well cannot be located, which is a manual process precisely
because there is no authoritative public layer.

So the well distance is reported as unavailable rather than estimated. The folders
searched were `Hydrology`, `Utilities` and `Environmental`.

## Coordinate systems

Stored in WGS84, EPSG:4326, so the files need no side metadata to interpret.

All distance computation projects to UTM zone 18N, EPSG:32618, which covers
Delaware, and then converts metres to feet because the regulation is written in
feet. Distances are never computed in degrees. A degree of longitude at Delaware's
latitude is about 87 km while a degree of latitude is about 111 km, so treating
degrees as a distance would be wrong by about 25 percent in a direction that
depends on the bearing.


## The roads layer is a basemap and nothing else

`roads_centerline` was added on 2026-08-18, from DelDOT data published through
FirstMap. It exists so a reviewer can see where a parcel sits. It is drawn behind
everything on the screening figure and it is never measured against.

That separation is enforced in code rather than by convention. `WATER_LAYERS` and
`BASEMAP_LAYERS` in `src/septic/geo.py` are different tuples, and the nearest
feature search runs over `available_layers()`, which only ever returns the water
layers. A road is not a feature any provision of the regulation measures to, and
a basemap layer that could reach `screen_point()` would be a way for one to.

It is also generalised harder in the opposite direction from the water layers, and
for the opposite reason. The water layers are simplified to keep them committable,
and the tolerance is a cost paid against measurement accuracy. Roads carry no
measurement, but they are looked at closely: the figure covers roughly 900 feet,
about 274 metres, so the 55 metre tolerance first used here turned every road into
a few straight segments. They are now at 0.0001 degrees, about 11 metres, which is
finer than any water layer.

The first download of this layer also stopped at 40,000 features of 78,444, the
default `--max-features`, which left half the state with no roads on the map. A
truncated water layer is a false all clear, as described above. A truncated road
layer is a blank basemap, which looks broken rather than dangerous, but it is
still wrong. The committed file holds all 78,444 segments the service reports
inside the bounding box.


## Aerial Imagery

USGS National Map Imagery Only service. US federal, public domain.
Endpoint: basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export
Fetched 2026-08-19 by scripts/fetch_imagery.py.
Used as decoration and orientation only. Never enters a measurement.
