"""The street map panel that sits beside the screening figure.

This is the one part of the console that needs a network. Everything else works
with the wire pulled out: Textract output comes off the disk cache, the rules are
a local YAML file, the hydrography is committed GeoJSON, and the basemap under
the screening figure is a cached USGS tile. A street map cannot be cached, so it
is drawn beside our own figure rather than instead of it, and the figure is the
one that carries the measurement, the setback ring, the scale bar and the
provenance line.

If the panel does not load, nothing is lost that a reviewer needed. The
coordinates are printed under it and the link opens the same view in a real
browser tab, so a dead venue wifi costs the demo a picture of a roof and no more
than that.

On which map is drawn. The default is the keyless Google embed, the
maps.google.com address with output=embed. It answers 200 to a browser GET,
redirects to www.google.com/maps/embed, and that response sets no framing
header, so it frames. Worth knowing if it ever looks broken: a HEAD request to
the same address answers 404 and the redirect carries x-frame-options
SAMEORIGIN, so probing it with curl -I says it is dead when it is not.

It is keyless, which means it is also unsupported, and Google can withdraw it
without notice. Setting SEPTIC_MAPS_EMBED_KEY switches the panel to the
documented Maps Embed API, which needs that API enabled on the project. Nothing
else changes.

The addresses are built here and not in app.py because the console asserts that
it references no remote resource, and that assertion is worth keeping true for
the file that renders the review. What is remote lives in one named place.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

KEYLESS_EMBED = "https://maps.google.com/maps"
EMBED_API = "https://www.google.com/maps/embed/v1/place"
LINK_BASE = "https://www.google.com/maps/search/"

# 17 is close enough to see the driveway and the outbuildings without losing the
# road that names the parcel. The screening figure covers the wider view.
DEFAULT_ZOOM = 17

def api_key() -> str:
    """The Google Maps Embed API key, or empty when none is configured."""
    return os.environ.get("SEPTIC_MAPS_EMBED_KEY", "").strip()


def provider() -> str:
    """Which address the panel will use, decided by whether a key is present."""
    return "embed-api" if api_key() else "keyless"


def embed_url(lat: float, lon: float, zoom: int = DEFAULT_ZOOM) -> str:
    """The src for the embedded map at a point."""
    key = api_key()
    if key:
        return f"{EMBED_API}?" + urlencode({
            "key": key,
            "q": f"{lat},{lon}",
            "zoom": zoom,
            "maptype": "satellite",
        })
    return f"{KEYLESS_EMBED}?" + urlencode({
        "q": f"{lat},{lon}",
        "z": zoom,
        "output": "embed",
    })


def link_url(lat: float, lon: float) -> str:
    """The address that opens the same point in a full Google Maps tab."""
    return f"{LINK_BASE}?" + urlencode({"api": 1, "query": f"{lat},{lon}"})


def panel_html(
    lat: float,
    lon: float,
    height: int = 420,
    zoom: int = DEFAULT_ZOOM,
    tokens: dict | None = None,
) -> str:
    """A self contained document holding the map, its caption and its fallback.

    Rendered through streamlit.components.html, which gives it its own frame.
    Streamlit strips an iframe out of markdown, so the map cannot be appended to
    the screening card and has to be its own component.
    """
    tokens = tokens or {}
    surface = tokens.get("surface", "#FFFFFF")
    line = tokens.get("line", "#D8DCE3")
    muted = tokens.get("muted", "#5A6472")
    radius = tokens.get("radius", "8px")
    font = tokens.get("font", "system-ui, sans-serif")

    src = embed_url(lat, lon, zoom)
    link = link_url(lat, lon)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body {{ margin:0; padding:0; font-family:{font}; background:{surface}; }}
  .wrap {{
    border:1px solid {line}; border-radius:{radius}; background:{surface};
    padding:16px; box-sizing:border-box;
  }}
  .cap {{
    font-size:12px; color:{muted}; text-transform:uppercase;
    letter-spacing:0.07em; margin-bottom:12px;
  }}
  .frame {{
    position:relative; border-radius:4px; overflow:hidden;
    border:1px solid {line}; background:{surface};
  }}
  /* Sits behind the map. Visible only if the map never paints, which is what a
     failed network looks like from in here. */
  .fallback {{
    position:absolute; inset:0; display:flex; align-items:center;
    justify-content:center; text-align:center; padding:24px;
    font-size:13px; color:{muted}; line-height:1.5;
  }}
  iframe {{ position:relative; display:block; width:100%; border:0; }}
  .foot {{ font-size:12px; color:{muted}; margin-top:10px; line-height:1.5; }}
  .foot a {{ color:inherit; }}
</style></head>
<body>
  <div class="wrap">
    <div class="cap">Street and satellite view</div>
    <div class="frame" style="height:{height}px">
      <div class="fallback">
        This panel is the only thing on the page that needs a network.<br>
        The screening figure beside it does not.
      </div>
      <iframe src="{src}" height="{height}" loading="lazy"
              referrerpolicy="no-referrer-when-downgrade"
              title="Google map of the permit location"></iframe>
    </div>
    <div class="foot">
      {lat}, {lon} &middot;
      <a href="{link}" target="_blank" rel="noopener noreferrer">Open in Google Maps</a>
      <br>Orientation only. No distance on this panel was measured, and nothing
      here is part of any finding.
    </div>
  </div>
</body></html>"""
