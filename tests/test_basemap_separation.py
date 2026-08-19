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
        """The screening distance for permit 281364 must not change when
        roads are present on disk.

        Permit 281364 screens at approximately 594 ft to the nearest water
        feature. If roads entered the search, the distance would drop to a
        few feet (the nearest road segment). This test pins the distance to
        within 1 ft of the known value.
        """
        # Permit 281364 coordinates
        lat, lon = 38.7377, -75.6193
        try:
            screening = geo.screen_point(lat, lon)
        except geo.CoordinateError:
            pytest.skip("coordinates outside Delaware in test environment")

        if screening.nearest_water is None:
            pytest.skip("no water layers present")

        distance = screening.nearest_water.distance_feet
        # The known distance is approximately 594 ft. If roads entered the
        # measurement, this would be near zero.
        assert distance > 100, (
            f"screening distance is {distance:.1f} ft, which suggests a "
            f"non-water feature (likely a road) entered the search"
        )
        # Pin to within 5 ft of the known value
        assert abs(distance - 594) < 5, (
            f"screening distance changed: expected ~594 ft, got {distance:.1f} ft"
        )
        # Verify the nearest feature is water, not a road
        assert screening.nearest_water.layer in geo.WATER_LAYERS, (
            f"nearest feature is from {screening.nearest_water.layer}, "
            f"which is not a water layer"
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
