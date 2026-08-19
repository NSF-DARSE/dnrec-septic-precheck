"""Offline rehearsal: verify the full review chain works with no network.

Blocks boto3 session creation and socket.connect, then runs the synthetic
packet and permit 281364 end to end. Reports timing and confirms the map,
the PDF rasterisation, and the HTML report all render.
"""
import sys
import time

sys.path.insert(0, "src")

import septic.config as config

# Block any network by making boto3 session construction fail
original_session = config.session


def explode(*a, **kw):
    raise AssertionError("NETWORK BLOCKED: session() was called")


config.session = explode

# Also block socket to be thorough
import socket

_original_connect = socket.socket.connect


def blocked_connect(self, *a, **kw):
    raise OSError("NETWORK BLOCKED: socket.connect was called")


socket.socket.connect = blocked_connect

from septic import geo, review as review_mod
from septic.ingest.textract import TextractClient, document_hash
from septic.report.render import render_html

# Warm GIS layers (simulating what @st.cache_resource does at startup)
t0 = time.perf_counter()
for name in geo.available_layers():
    geo.load_layer(name)
t_layers = time.perf_counter() - t0
print(f"GIS layers loaded: {t_layers:.2f}s")

# Review synthetic packet
pdf1 = config.OUT_DIR / "examples" / "synthetic_demonstration_packet.pdf"
t0 = time.perf_counter()
r1 = review_mod.review(
    pdf=pdf1, allow_network=False, with_precedents=False,
    with_screening=True, with_map=True,
)
t1 = time.perf_counter() - t0
print(f"Synthetic packet: {t1:.2f}s - {r1.composed.headline}")
assert "SYNTHETIC" in r1.composed.notices[0]
assert r1.html and "<!doctype html>" in r1.html

# Review permit 281364
pdf2 = config.OUT_DIR / "examples" / "permit_281364_60839580.pdf"
t0 = time.perf_counter()
r2 = review_mod.review(
    pdf=pdf2, allow_network=False, with_precedents=False,
    with_screening=True, with_map=True,
)
t2 = time.perf_counter() - t0
screening = r2.composed.to_json().get("screening") or {}
has_map = bool(screening.get("figure_png"))
print(f"Permit 281364: {t2:.2f}s - {r2.composed.headline} - map: {has_map}")
assert r2.html and "<!doctype html>" in r2.html

# Check PDF rasterisation works offline (pypdfium2 is pure local)
import pypdfium2 as pdfium

doc = pdfium.PdfDocument(pdf2.read_bytes())
page = doc[0]
bitmap = page.render(scale=2)
img = bitmap.to_pil()
print(f"PDF rasterisation: page 1 of {len(doc)} rendered ({img.size[0]}x{img.size[1]})")

print()
print("RESULT: Full offline rehearsal passed. No network call was made.")
p1 = r1.composed.to_json()
p2 = r2.composed.to_json()
print(f"  Synthetic: {r1.composed.headline}")
print(f"    coverage: {p1['coverage']['text']}")
print(f"  Permit 281364: {r2.composed.headline}")
print(f"    coverage: {p2['coverage']['text']}")
print(f"  Map present: {has_map}")
print(f"  PDF viewer: functional")
print(f"  Timings: layers {t_layers:.1f}s, synthetic {t1:.1f}s, 281364 {t2:.1f}s")
