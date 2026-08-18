"""Cross-check that the three preflight output formats agree.

python -m septic preflight writes the same result set three ways: the console,
out/preflight_report.txt, and out/preflight_report.json. If those ever disagree
then one of them is stale or a renderer dropped a check, and a green console with
a red report file is the kind of thing nobody notices until it matters.

Run after "python -m septic preflight". Exits non-zero on any mismatch.
"""
import json
from pathlib import Path

txt = Path("out/preflight_report.txt").read_text(encoding="utf-8")
j = json.loads(Path("out/preflight_report.json").read_text(encoding="utf-8"))
console = Path("out/preflight_console_check.txt").read_text(encoding="utf-8")

txt_header = txt.splitlines()[0]
json_header = j["header"]
print("txt header:", txt_header)
print("json header:", json_header)

txt_pass = sum(1 for line in txt.splitlines() if "PASS" in line and line.strip().startswith(("S", "B", "T")))
txt_fail = sum(1 for line in txt.splitlines() if "FAIL" in line and line.strip().startswith(("S", "B", "T")))
json_pass = sum(1 for c in j["checks"] if c["status"] == "PASS")
json_fail = sum(1 for c in j["checks"] if c["status"] == "FAIL")
print(f"txt: {txt_pass} pass, {txt_fail} fail")
print(f"json: {json_pass} pass, {json_fail} fail")
assert txt_pass == json_pass and txt_fail == json_fail, "MISMATCH"

console_clean = console.lstrip("\ufeff").strip()
txt_clean = txt.strip()
assert console_clean == txt_clean, "CONSOLE MISMATCH"

assert json_header["timestamp"] in txt_header
assert json_header["commit"] in txt_header
print(f"timestamp: {json_header['timestamp']}")
print(f"commit: {json_header['commit']}")
print()
print("ALL OUTPUTS AGREE")
