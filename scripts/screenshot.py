"""Take screenshots of the console at 1920 and 1400 widths.

Captures permit 281364 (NO DEFICIENCIES FOUND) and the synthetic
demonstration packet (DEFICIENCIES FOUND) at both widths.
"""
import subprocess
import sys
import time
import traceback
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright not installed, run: pip install playwright && playwright install chromium")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

PACKETS = [
    (ROOT / "out" / "examples" / "permit_281364_60839580.pdf", "281364"),
    (ROOT / "out" / "examples" / "permit_282133_60843649.pdf", "282133"),
    (ROOT / "out" / "examples" / "synthetic_demonstration_packet.pdf", "synthetic"),
]
WIDTHS = [1920, 1400]


def main():
    print("Starting streamlit...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
         "--server.headless", "true", "--server.port", "8503",
         "--browser.gatherUsageStats", "false"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    time.sleep(10)
    print("Streamlit should be running now")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            for width in WIDTHS:
                for packet_path, label in PACKETS:
                    if not packet_path.exists():
                        print(f"  Skipping {label}, file not found")
                        continue
                    print(f"Capturing {label} at {width}w...")
                    page = browser.new_page(
                        viewport={"width": width, "height": 1080}
                    )
                    page.goto(
                        "http://localhost:8503",
                        wait_until="networkidle",
                        timeout=30000,
                    )
                    page.wait_for_timeout(3000)

                    # Upload the PDF
                    file_input = page.locator('input[type="file"]')
                    file_input.set_input_files(str(packet_path))
                    page.wait_for_timeout(15000)

                    # Wait for the metric row to appear
                    try:
                        page.wait_for_selector(".metric-row", timeout=20000)
                    except Exception:
                        pass
                    page.wait_for_timeout(2000)

                    fname = f"console_{label}_{width}w.png"
                    page.screenshot(path=str(OUT / fname), full_page=True)
                    print(f"  Saved {fname}")
                    page.close()

            browser.close()
            print("Done!")
    except Exception:
        traceback.print_exc()
    finally:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    main()
