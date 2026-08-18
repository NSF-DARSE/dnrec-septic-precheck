"""Tests for geospatial screening.

The coordinate parsing is the part that would corrupt results silently. The CSV
writes latitude as "38,658307", which float() rejects and a naive comma strip turns
into 38658307. The first failure drops rows, the second produces a number that a
bounding box check on the wrong axis might not catch. Both are tested here.

The other property held here is that this never becomes a compliance answer. It
measures from a geocoded address point and the regulation measures from the
disposal area, so the output is a screening prompt and a permit with no
coordinates contributes no fact at all.
"""
import math

import pytest

from septic import geo


class TestCoordinateParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("38,658307", 38.658307),
        ("-75,574018", -75.574018),
        ("38,72009", 38.72009),
        ("39,813601", 39.813601),
    ])
    def test_comma_decimal_separator(self, raw, expected):
        """The format this export actually uses."""
        assert geo.parse_coordinate(raw) == pytest.approx(expected)

    def test_naive_float_fails_on_the_real_format(self):
        """Establishes why the parser exists rather than trusting float()."""
        with pytest.raises(ValueError):
            float("38,658307")

    def test_comma_strip_would_be_catastrophic(self):
        """The tempting one liner produces a number six orders of magnitude out."""
        assert float("38,658307".replace(",", "")) == 38658307.0
        assert geo.parse_coordinate("38,658307") == pytest.approx(38.658307)

    @pytest.mark.parametrize("raw,expected", [
        ("38.658307", 38.658307),
        ("-75.574018", -75.574018),
        ("39", 39.0),
    ])
    def test_dot_decimal_still_works(self, raw, expected):
        assert geo.parse_coordinate(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", [None, "", "nan", "NaN", "none", "abc"])
    def test_unparsable_is_none(self, raw):
        assert geo.parse_coordinate(raw) is None

    def test_geocoded_location_uses_dots(self):
        """An independent cross check, since this column has a different format."""
        assert geo.parse_geocoded_location("(38.658307, -75.574018)") == (
            pytest.approx(38.658307), pytest.approx(-75.574018)
        )

    @pytest.mark.parametrize("raw", [None, "", "38.6 -75.5", "(bad)"])
    def test_bad_geocoded_location_is_none(self, raw):
        assert geo.parse_geocoded_location(raw) is None


class TestDelawareBoundingBox:
    @pytest.mark.parametrize("lat,lon", [
        (38.658307, -75.574018),
        (39.813601, -75.616268),
        (38.733966, -75.241955),
    ])
    def test_real_permits_are_inside(self, lat, lon):
        assert geo.in_delaware(lon, lat)

    @pytest.mark.parametrize("lat,lon", [
        (37.977976, -77.692915),   # the geocoding failure sentinel in this export
        (38658307.0, -75.5),       # what a naive comma strip produces
        (0.0, 0.0),
        (40.7, -74.0),             # New York
    ])
    def test_wrong_coordinates_are_outside(self, lat, lon):
        assert not geo.in_delaware(lon, lat)

    def test_row_outside_delaware_is_rejected(self):
        with pytest.raises(geo.CoordinateError):
            geo.permit_point({
                "Latitude": "37,977976", "Longitude": "-77,692915",
            })

    def test_cross_check_disagreement_raises(self):
        """If the two columns disagree, the comma parsing is wrong.

        Raising beats picking a winner, because a silent choice here is exactly
        the corruption being guarded against.
        """
        with pytest.raises(geo.CoordinateError):
            geo.permit_point({
                "Latitude": "38,658307",
                "Longitude": "-75,574018",
                "Geocoded Location": "(39.111111, -75.222222)",
            })

    def test_matching_columns_are_marked_cross_checked(self):
        point = geo.permit_point({
            "Latitude": "38,658307",
            "Longitude": "-75,574018",
            "Geocoded Location": "(38.658307, -75.574018)",
        })
        assert point is not None
        assert point.cross_checked

    def test_missing_coordinates_return_none(self):
        assert geo.permit_point({"Latitude": "nan", "Longitude": "nan"}) is None


class TestProjection:
    def test_distances_are_computed_in_metres_not_degrees(self):
        """A degree is not a distance, and not the same distance on both axes.

        At Delaware's latitude a degree of longitude is about 87 km and a degree
        of latitude about 111 km. If anything ever computes in degrees, this
        catches it.
        """
        lat, lon = 38.7, -75.5
        east_1, north_1 = geo.to_utm(lon, lat)
        east_2, north_2 = geo.to_utm(lon + 0.01, lat)
        east_3, north_3 = geo.to_utm(lon, lat + 0.01)

        east_shift = math.hypot(east_2 - east_1, north_2 - north_1)
        north_shift = math.hypot(east_3 - east_1, north_3 - north_1)

        # About 870 m versus about 1110 m. Equal would mean degrees.
        assert 700 < east_shift < 950
        assert 1000 < north_shift < 1200
        assert north_shift > east_shift * 1.15

    def test_utm_coordinates_are_plausible_for_delaware(self):
        east, north = geo.to_utm(-75.616268, 39.813601)
        assert 380000 < east < 560000
        assert 4250000 < north < 4420000

    def test_feet_conversion(self):
        assert geo.METRES_TO_FEET == pytest.approx(3.28084, rel=1e-5)


class TestScreening:
    """These need the layers, so they skip when data/gis is absent."""

    @pytest.fixture(autouse=True)
    def require_layers(self):
        if not geo.available_layers():
            pytest.skip("no GIS layers under data/gis")

    def test_layers_load_and_project(self):
        for name in geo.available_layers():
            layer = geo.load_layer(name)
            assert len(layer) > 0, f"{name} loaded no geometry"
            assert len(layer.labels) == len(layer.geometries)

    def test_screening_returns_a_finite_distance(self):
        screening = geo.screen_point(39.813601, -75.616268)
        assert screening.nearest_water is not None
        distance = screening.nearest_water.distance_feet
        assert math.isfinite(distance)
        assert distance > 0

    def test_no_distance_is_nan(self):
        """An invalid geometry makes shapely return NaN, which compares false
        against every threshold and would vanish from a nearest search."""
        screening = geo.screen_point(38.733966, -75.241955)
        for nearest in screening.per_layer.values():
            assert math.isfinite(nearest.distance_feet), (
                f"{nearest.layer} produced a non finite distance"
            )

    def test_well_distance_is_unavailable_not_estimated(self):
        """No public well layer exists, so this must say so rather than guess."""
        screening = geo.screen_point(38.733966, -75.241955)
        assert screening.nearest_well is None
        assert any("well" in note.lower() for note in screening.unavailable)

    def test_screening_offers_a_fact_to_the_engine(self):
        screening = geo.screen_point(39.813601, -75.616268)
        facts = screening.facts()
        assert "dist_point_to_mapped_water" in facts
        assert facts["dist_point_to_mapped_water"] > 0

    def test_flag_wording_never_claims_compliance(self):
        """It prompts a reviewer to check the plan. It does not decide."""
        screening = geo.screen_point(39.813601, -75.616268)
        joined = " ".join(screening.flags()).lower()
        for banned in ("compliant", "complies", "meets the requirement",
                       "in compliance", "approved"):
            assert banned not in joined, f"screening text claims {banned!r}"

    def test_out_of_state_point_raises(self):
        with pytest.raises(geo.CoordinateError):
            geo.screen_point(40.7128, -74.0060)


class TestNoCoordinatesMeansNoFact:
    """Ten percent of permits have no coordinates. They must not read as a pass."""

    def test_row_without_coordinates_yields_no_facts(self):
        screening = geo.screen_permit({"Latitude": "nan", "Longitude": "nan"})
        assert not screening.has_location
        assert screening.facts() == {}

    def test_missing_location_is_stated_in_the_flags(self):
        screening = geo.screen_permit({})
        joined = " ".join(screening.flags())
        assert "no usable coordinates" in joined

    def test_rejected_coordinates_yield_no_facts(self):
        screening = geo.screen_permit({
            "Latitude": "37,977976", "Longitude": "-77,692915",
        })
        assert screening.facts() == {}
        assert any("rejected" in note.lower() for note in screening.unavailable)

    def test_absent_fact_leaves_a_rule_unknown(self):
        """The engine turns a missing parameter into UNKNOWN, never a pass."""
        from septic.rules.engine import evaluate
        from septic.rules.schema import Citation, Operator, Outcome, Rule, Severity

        rule = Rule(
            id="TEST-water-screen",
            description="screening test",
            citation=Citation(section="Exhibit C", page=173, quote="test"),
            parameter="dist_point_to_mapped_water",
            operator=Operator.GE,
            threshold=100,
            units="feet",
            severity=Severity.ADVISORY,
            verified=True,
            remedy="check the plan",
            notes="test fixture",
        )
        screening = geo.screen_permit({})
        report = evaluate(screening.facts(), [rule])
        assert report.evaluations[0].outcome is Outcome.UNKNOWN
