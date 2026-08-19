# Demo runbook

Last updated 2026-08-18. The presentation is 19 August at 3:30 pm.

## Starting cold

From a clean terminal on the presentation laptop:

```bash
cd C:\Users\Jarvis\Desktop\Hackathon2026
.venv\Scripts\activate
python scripts/build_synthetic_packet.py   # generates the synthetic PDF and cache
pip install streamlit                       # if not already present
streamlit run app.py
```

No network and no AWS credentials are needed. The console serves Textract output
from the on-disk SHA256 cache. Uploading a packet that has not been cached will
explain what is needed rather than hanging.

The GIS layers and the regulation graph load at startup (the first page paint takes
a few seconds). After that, each review takes about 5 to 6 seconds for a real
13-page packet.

## What the screen shows

On open, the main column shows a dark brand band reading "Septic permit application
review", followed by a large dashed drop target with "Drop an application packet
here" and the three possible outcomes. No sidebar, no menu, no list of packets.

Once a packet is uploaded:

- The drop target folds into a closed expander labelled "Review a different packet."
- A provenance line shows the document name, page count, and review time.
- A metric row with four cards: checks ran, not applicable, could not be read,
  and the verdict with a segmented coverage bar and legend.
- If the packet carries a notice (the synthetic packet does), it renders in an
  amber banner directly under the metric row.
- The screen splits into two columns. The left (wider) column carries the
  findings grouped by outcome: deficiencies with FAIL pills and citations, could
  not be evaluated (grouped by the missing value that blocked them, with the
  blocked rules listed compactly beneath each cause), checks that passed, and
  does not apply. Every requirement reads as a sentence, not a machine
  expression. The right column shows the full scanned packet as a scrollable
  document.
- At the bottom: a download button for the printable HTML report, and a toggle
  to show all 15 rules with their thresholds and regulation quotes.

## The three packets and what they demonstrate

### 1. Synthetic demonstration packet (DEFICIENCIES FOUND)

File: `out/examples/synthetic_demonstration_packet.pdf`

This is a constructed example, not a real permit application. The presenter should
say so out loud when loading it. It exists because every real packet in the corpus
is an approved permit, so none produces DEFICIENCIES FOUND, and the demo cannot
show the outcome that matters most without it.

What it shows:

- Verdict: **DEFICIENCIES FOUND**
- Coverage: 7 of 15 checks ran, 1 not applicable to this system, 7 could not be
  read
- Two cited deficiencies:
  - ISO-001: disposal area to well distance is 60 feet, below the 100 foot minimum
    (Exhibit C, page 173)
  - PERC-001: percolation rate is 140 minutes per inch, above the 120 maximum
    (Section 5.2.4.2.5.7, page 52)
- Five checks that pass, one that does not apply, seven that cannot be read
- The notice banner states plainly that this is constructed

### 2. Permit 281364 (NO DEFICIENCIES FOUND, best coverage)

File: `out/examples/permit_281364_60839580.pdf`

A real 13-page DNREC permit packet. The strongest demonstration of the tool
working on a real document: it reads five values off the form, compares each one
against the regulation, and finds nothing wrong.

What it shows:

- Verdict: **NO DEFICIENCIES FOUND**
- Coverage: 5 of 15 checks ran, 3 not applicable to this system, 7 could not be
  read
- The findings table shows which rules passed and why each was excluded or
  unreadable, with the unreadable section grouped by cause
- The full 13-page packet scrolls in the viewer beside the findings
- A location screening map showing roads for orientation, the permit location,
  nearby mapped surface water, isolation distance rings from the rule set, and
  the measured distance to the nearest water feature

### 3. Permit 282133 (NO DEFICIENCIES FOUND, minimal coverage)

File: `out/examples/permit_282133_60843649.pdf`

A real single-page DNREC permit packet. The strongest argument for why the
coverage bar exists. The verdict says NO DEFICIENCIES FOUND, which sounds clean,
but the coverage bar shows 1 of 15 checks ran and 14 could not be read. A reviewer
looking at only the headline would see a clean bill. A reviewer looking at the
coverage bar sees that almost nothing was checked. That is exactly the
misinterpretation the three-way coverage figure prevents.

What it shows:

- Verdict: **NO DEFICIENCIES FOUND**
- Coverage: 1 of 15 checks ran, 14 could not be read
- The segmented bar is almost entirely amber (could not be read), with one thin
  green sliver (ran). The visual is unmissable.

## Suggested demo order

1. Start with the **synthetic packet**. It is the outcome everyone comes to see.
   Walk through the two deficiencies, point at the citations, open the regulation
   text disclosure. Say: this is a constructed example so we can show you what a
   real deficiency looks like without exposing anyone's application.

2. Switch to **permit 281364**. This is a real packet. Point at the coverage: 5 of
   15 checks ran because most isolation distances are measurements on a scanned
   drawing that no OCR can take. Walk through the passes, the excluded rules, and
   the seven that could not be read (grouped: "6 checks could not run because X
   was not machine readable"). Scroll through the packet in the viewer to the site
   plan page and point: that is where the distances live, and that is why they
   cannot be read. Scroll down to the location screening map: it shows roads for
   orientation, the permit location, and the nearest mapped water with a measured
   distance.

3. Finish with **permit 282133**. The punch line: 1 of 15. This is what NO
   DEFICIENCIES FOUND looks like when coverage is nearly zero. The tool refuses to
   let that read as a clean pass, because it is not one, and the coverage bar
   makes the difference visible from across the room.

## Three questions a reviewer will ask

**Why could so many checks not be read?**
Most of the 15 checks need a measurement dimensioned on a scanned site plan.
Textract reads text and form fields. It cannot measure a distance between drawn
features on a raster image. The tool reports each unreadable check with the value
to look for, where on the packet it normally appears, and the section to compare it
against. A check that did not run is not a check that passed.

**Does this tool ever approve or deny anything?**
No. It flags deficiencies and puts the regulation citation next to each one, or it
says it cannot verify, or it reports nothing found. The reviewer decides.

**Where does a finding's citation come from?**
Every threshold is traced to a section and page in the 2014 Delaware regulation.
The rule set carries the verbatim text. Click "Regulation text" on any finding to
read the quoted passage, or toggle "Show all rules" at the bottom of the page to
see all 15 with their page numbers and quotes.

## Offline contingency

Everything runs from the local cache. No network is needed after `pip install`.
If wifi fails:

- The console serves cached Textract analyses by SHA256 hash.
- The PDF viewer rasterises locally with pypdfium2.
- The location screening map is drawn from committed GIS data in `data/gis/`
  (water layers and road centrelines, about 9 MB total).
- Bedrock (wording and embeddings) degrades gracefully: the report renders with
  original wording and no precedent list, which is a complete report.
- The only thing that breaks is uploading a packet that has never been cached.
  Stick to the three staged packets and nothing reaches for the network.

## Fallback: recorded video

If the laptop fails entirely, the recorded video covers the same three-packet
walkthrough. It does not replace the live demo but it serves as proof that the
tool runs.
