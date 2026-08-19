"""The spatial index must select exactly what a full scan selects.

Drawing a figure used to test every geometry in every layer against the window,
about 180,000 intersects calls for one map. An STRtree per layer answers the same
question in a fraction of the time, but a wrong index fails silently: the map
simply loses features, and a reviewer has no way to know a stream was omitted.

So this compares the index against the scan it replaced, on the real layers, at a
real location, rather than asserting a count someone wrote down.
"""
from __future__ import annotations

import pytest

from septic import geo


@pytest.fixture(scope="module")
def window():
    from shapely.geometry import box

    # Packet A's location, and the same 900 foot radius the figure uses.
    easting, northing = geo.to_utm(-75.4277, 38.9108)
    radius = 900 * 0.3048
    return box(easting - radius, northing - radius,
               easting + radius, northing + radius)


class TestLayerIndex:
    def test_index_selects_what_a_full_scan_selects(self, window):
        layers = geo.available_layers() + geo.available_basemap_layers()
        if not layers:
            pytest.skip("no GIS layers present")

        for name in layers:
            layer = geo.load_layer(name)
            scanned = {
                position for position, geometry in enumerate(layer.geometries)
                if geometry.intersects(window)
            }
            tree, geometries, _labels = geo.layer_index(name)
            indexed = {
                position for position in tree.query(window)
                if geometries[position].intersects(window)
            }
            assert scanned == indexed, (
                f"{name}: the index and a full scan disagree on "
                f"{len(scanned ^ indexed)} geometries, so the figure would "
                f"silently gain or lose features"
            )

    def test_index_covers_every_geometry_in_the_layer(self):
        """The tree indexes positions in the layer's own geometry list."""
        layers = geo.available_layers()
        if not layers:
            pytest.skip("no GIS layers present")
        name = layers[0]
        layer = geo.load_layer(name)
        tree, geometries, labels = geo.layer_index(name)
        assert len(geometries) == len(layer.geometries)
        assert len(labels) == len(layer.labels)
        assert geometries is not None and len(geometries) > 0

    def test_basemap_layers_are_never_in_the_measurement_set(self):
        """Restates the separation the index must not quietly undo."""
        assert not set(geo.WATER_LAYERS) & set(geo.BASEMAP_LAYERS)
        assert not set(geo.available_layers()) & set(
            geo.available_basemap_layers()
        )
