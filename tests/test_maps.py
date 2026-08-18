"""Tests for the figures.

The property that matters is that a figure cannot show a threshold the rule set
does not hold, and that nothing renders through a network call.
"""
import pytest

from septic import geo

pytest.importorskip("matplotlib")

from septic import maps  # noqa: E402


class TestRingsComeFromTheRules:
    def test_every_ring_matches_a_rule_threshold(self):
        """A figure must not invent a distance.

        Ring radii are read from rules_7101.yaml, so a reader can check any number
        on the map against the rule set and the regulation behind it.
        """
        from septic.rules.engine import load_rules

        thresholds = {
            float(r.threshold) for r in load_rules()
            if r.threshold is not None and isinstance(r.threshold, (int, float))
        }
        specs = maps.ring_specs()
        assert specs, "expected some rings"
        for feet, rule_ids, _label in specs:
            assert feet in thresholds, f"{feet} ft is not any rule's threshold"
            assert rule_ids

    def test_coincident_rings_are_grouped(self):
        """The well and watercourse setbacks are both 100 ft.

        Drawn separately they land on top of each other and the legend shows two
        colours for one visible circle, which reads as a drawing error.
        """
        specs = maps.ring_specs()
        radii = [feet for feet, _rules, _label in specs]
        assert len(radii) == len(set(radii)), "coincident radii were not grouped"

    def test_labels_carry_the_citation(self):
        for _feet, _rules, label in maps.ring_specs():
            assert "p." in label, f"ring label {label!r} has no page reference"

    def test_grouped_label_names_both_targets(self):
        specs = {feet: label for feet, _rules, label in maps.ring_specs()}
        hundred = specs.get(100.0)
        if hundred is None:
            pytest.skip("no 100 ft ring in the current rule set")
        assert "well" in hundred and "watercourse" in hundred


class TestPermitMap:
    @pytest.fixture(autouse=True)
    def require_layers(self):
        if not geo.available_layers():
            pytest.skip("no GIS layers under data/gis")

    def test_draws_png_and_svg(self, tmp_path):
        result = maps.permit_map("281364", 39.813601, -75.616268, out_dir=tmp_path)
        assert result is not None
        assert result.png.exists() and result.png.stat().st_size > 20000
        assert result.svg.exists() and result.svg.stat().st_size > 10000

    def test_reports_the_measured_distance(self, tmp_path):
        result = maps.permit_map("281364", 39.813601, -75.616268, out_dir=tmp_path)
        assert result.nearest_feet is not None
        assert result.nearest_feet > 0
        # Agrees with the screening path, which is a separate code route.
        screening = geo.screen_point(39.813601, -75.616268)
        assert result.nearest_feet == pytest.approx(
            screening.nearest_water.distance_feet, rel=0.02
        )

    def test_svg_contains_no_remote_reference(self, tmp_path):
        """No basemap tiles, no web requests.

        An SVG unavoidably declares XML namespaces such as
        xmlns="http://www.w3.org/2000/svg", which are identifiers a renderer never
        dereferences, so the check is for resources that would actually be
        fetched: embedded images, stylesheets, and external entities.
        """
        import re

        result = maps.permit_map("281364", 39.813601, -75.616268, out_dir=tmp_path)
        content = result.svg.read_text(encoding="utf-8", errors="replace")

        fetched = re.findall(
            r"(?:xlink:href|href|src)\s*=\s*[\"'](https?://[^\"']+)[\"']",
            content,
        )
        assert not fetched, f"SVG would fetch {fetched}"
        assert "<image" not in content, "SVG embeds a raster, likely a basemap tile"
        assert "<!ENTITY" not in content, "SVG declares an external entity"
        for banned in ("tile.openstreetmap", "arcgisonline", "basemaps",
                       "contextily", "fonts.googleapis"):
            assert banned not in content, f"SVG references {banned}"


class TestComparisonFigure:
    def test_renders_from_supplied_distributions(self, tmp_path):
        """Plotting is tested on synthetic data, so it needs no CSV."""
        data = {
            "year_min": 2014,
            "groups": {
                "denied and returned": {
                    "selected": 104, "screened": 104, "with_distance": 3,
                    "no_coordinates": 69, "rejected_coordinates": 0,
                    "distances": [120.0, 400.0, 900.0],
                },
                "approved": {
                    "selected": 1226, "screened": 1226, "with_distance": 4,
                    "no_coordinates": 2, "rejected_coordinates": 37,
                    "distances": [150.0, 500.0, 800.0, 1500.0],
                },
            },
        }
        summary = maps.comparison_figure(data, out_dir=tmp_path)
        assert summary["denied"]["n"] == 3
        assert summary["approved"]["n"] == 4
        assert (tmp_path / "distance_to_water_by_outcome.png").exists()
        assert (tmp_path / "distance_to_water_by_outcome.svg").exists()

    def test_states_sample_sizes(self, tmp_path):
        data = {
            "year_min": 2014,
            "groups": {
                "denied and returned": {"distances": [100.0, 200.0],
                                        "selected": 10, "no_coordinates": 8},
                "approved": {"distances": [300.0], "selected": 20,
                             "no_coordinates": 0},
            },
        }
        summary = maps.comparison_figure(data, out_dir=tmp_path)
        assert summary["denied"]["n"] == 2
        assert summary["approved"]["n"] == 1
        assert summary["denied"]["median_feet"] is not None

    def test_reports_separation_honestly(self, tmp_path):
        """Overlapping distributions must not be reported as a separation."""
        identical = [100.0, 200.0, 300.0, 400.0, 500.0] * 4
        data = {
            "year_min": 2014,
            "groups": {
                "denied and returned": {"distances": list(identical),
                                        "selected": 20, "no_coordinates": 0},
                "approved": {"distances": list(identical), "selected": 20,
                             "no_coordinates": 0},
            },
        }
        summary = maps.comparison_figure(data, out_dir=tmp_path)
        assert summary["separates"] is False

    def test_detects_a_real_separation(self, tmp_path):
        """And a genuine difference must not be reported as a null."""
        data = {
            "year_min": 2014,
            "groups": {
                "denied and returned": {"distances": [10.0] * 20,
                                        "selected": 20, "no_coordinates": 0},
                "approved": {"distances": [1500.0] * 20, "selected": 20,
                             "no_coordinates": 0},
            },
        }
        summary = maps.comparison_figure(data, out_dir=tmp_path)
        assert summary["separates"] is True
