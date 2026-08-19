"""Test that basemap layers cannot enter the measurement path.

The roads layer is drawn on the figure for orientation. It must never reach
screen_point, because a road is not a feature the regulation measures to and
its presence in the distance search would change the screening result.

The safety property: available_layers() returns only WATER_LAYERS and
available_basemap_layers() returns only BASEMAP_LAYERS, and those two tuples
are disjoint. screen_point uses available_layers() and nothing else.
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from septic import geo


class TestBasemapLayerSeparation:
    """Basemap layers must never enter the measurement path."""

    def test_water_and_basemap_tuples_are_disjoint(self):
        """No layer name appears in both tuples."""
        water = set(geo.WATER_LAYERS)
        basemap = set(geo.BASEMAP_LAYERS)
        overlap = water & basemap
        assert not overlap, (
            f"layers in both WATER_LAYERS and BASEMAP_LAYERS: {overlap}"
        )

    def test_roads_centerline_not_in_water_layers(self):
        """The roads layer must not be in WATER_LAYERS."""
        assert "roads_centerline" not in geo.WATER_LAYERS

    def test_available_layers_returns_only_water(self):
        """available_layers() must draw exclusively from WATER_LAYERS."""
        available = geo.available_layers()
        for name in available:
            assert name in geo.WATER_LAYERS, (
                f"{name} returned by available_layers() but not in WATER_LAYERS"
            )

    def test_available_basemap_layers_returns_only_basemap(self):
        """available_basemap_layers() must draw exclusively from BASEMAP_LAYERS."""
        available = geo.available_basemap_layers()
        for name in available:
            assert name in geo.BASEMAP_LAYERS, (
                f"{name} returned by available_basemap_layers() but not in "
                f"BASEMAP_LAYERS"
            )

    def test_screen_point_ignores_roads(self):
        """The screening distance for permit 281364 is measured to water only.

        This is the assertion the whole basemap separation exists to protect.
        Permit 281364 sits 470.6 ft from Wilson Run. A road runs closer, so if
        roads ever entered the distance search the reviewer would be shown
        348.9 ft instead, labelled as a water feature, which is a wrong setback
        distance presented as a real one.

        Pinning the number alone would not catch that, because a constant says
        nothing about whether the exclusion is doing any work. So this measures
        both ways and asserts they differ: the default answer must be the water
        only answer, and including roads must change it.
        """
        lat, lon = 39.813601, -75.616268

        default = geo.screen_point(lat, lon)
        if default.nearest_water is None:
            pytest.skip("no water layers present")

        water_only = geo.screen_point(lat, lon, layers=list(geo.WATER_LAYERS))
        with_roads = geo.screen_point(
            lat, lon, layers=list(geo.WATER_LAYERS) + list(geo.BASEMAP_LAYERS)
        )

        assert abs(default.nearest_water.distance_feet - 470.6) < 1.0, (
            f"permit 281364 should screen at 470.6 ft to Wilson Run, got "
            f"{default.nearest_water.distance_feet:.1f} ft"
        )
        assert default.nearest_water.label == "Wilson Run"
        assert (
            default.nearest_water.distance_feet
            == water_only.nearest_water.distance_feet
        ), "the default search included something that is not a water layer"

        if geo.available_basemap_layers():
            assert (
                with_roads.nearest_water.distance_feet
                < default.nearest_water.distance_feet - 10
            ), (
                "including roads did not change the answer, so this test is "
                "no longer proving that excluding them matters"
            )

    def test_screen_point_does_not_accept_basemap_layer_names(self):
        """Passing a basemap layer name explicitly to screen_point still works
        but is the caller's choice, not something that happens by default."""
        # Verify that the default path (layers=None) never includes basemap
        import inspect
        source = inspect.getsource(geo.screen_point)
        assert "available_layers()" in source
        assert "available_basemap_layers" not in source
        assert "BASEMAP_LAYERS" not in source
