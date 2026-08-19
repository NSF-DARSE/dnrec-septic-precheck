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
- When the verdict is DEFICIENCIES FOUND, a closed expander labelled "Draft
  correction letter (edit before sending)" appears below the report download.
  It contains a plain text letter the reviewer can paste into their own template.
  It is a draft for the reviewer to edit and sign, not a determination. The tool
  does not decide what to send.

## The three packets and what they demonstrate

Each packet shows one of the three outcomes. All three are built by
`python scripts/build_synthetic_packet.py` and review offline from cache.

### 1. Packet A (DEFICIENCIES FOUND)

File: `out/examples/application_packet_a.pdf`

The outcome everyone comes to see. Every check runs, so the verdict rests on the
whole rule set rather than on a fraction of it.

What it shows:

- Verdict: **DEFICIENCIES FOUND**
- Coverage: **15 of 15 checks ran**, none unread
- Three cited deficiencies:
  - ISO-001: disposal area to well is 60 feet, below the 100 foot minimum
    (Exhibit C, page 173)
  - PERC-001: percolation rate is 140 minutes per inch, above the 120 maximum
    (Section 5.2.4.2.5.7, page 52)
  - SLOPE-001: slope across the disposal area is 4 percent, above the 2 percent
    maximum (Section 5.3.12.1.2, page 60)
- Twelve checks that pass, each with the value read and the section it was
  compared against
- A draft correction letter, in the expander under the findings, itemising each
  deficiency with the value found, the requirement, the citation, the regulation
  quote and a remedy
- A location screening map: aerial imagery, mapped surface water 205 feet north,
  the 100 foot setback ring, and the caveat that the distance is measured from the
  address point rather than the disposal area

### 2. Packet B (NO DEFICIENCIES FOUND)

File: `out/examples/application_packet_b.pdf`

What a clean packet looks like when the tool could actually check it.

What it shows:

- Verdict: **NO DEFICIENCIES FOUND**
- Coverage: **15 of 15 checks ran**, none failed, none unread
- Fifteen passing rows, each naming the value read and the requirement it met
- A location screening map, 1639 feet from the nearest mapped water

Say plainly that this is still not an approval. It means nothing was flagged
among the checks that ran, and the reviewer decides.

### 3. Packet C (CANNOT VERIFY)

File: `out/examples/application_packet_c.pdf`

The interlock. This is the packet the tool refuses to answer on.

What it shows:

- Verdict: **CANNOT VERIFY**
- Coverage: 0 of 15 checks ran, **15 could not be read**
- The segmented bar is entirely amber. There is no green.
- The unread checks are grouped by cause, so the applicant is asked once for each
  missing value rather than once per rule it blocks
- No map, because nothing on the packet could be read, including its location

This is the most important of the three for a regulator. The tool had nothing to
work with and said so, rather than guessing or returning a clean-looking verdict
on no evidence.

## Suggested demo order

1. **Packet A, deficiencies.** The outcome everyone comes to see. Walk the three
   deficiencies, point at the citation chip on each row, and open one regulation
   text disclosure so they see the quoted provision rather than a paraphrase.
   Point at the coverage: 15 of 15 ran, so this verdict rests on the whole rule
   set. Then open the draft correction letter. That is the reviewer's actual
   output, itemised with citations, for a person to edit and sign.

2. **Packet B, no deficiencies.** Fifteen of fifteen ran, none failed. Say the
   line that matters: this is not an approval, it means nothing was flagged among
   the checks that ran, and the reviewer decides. Scroll the findings and let the
   packet stay pinned beside them, then open the Location tab for the screening
   map.

3. **Packet C, cannot verify.** The punch line, and the one a regulator should
   care about most. Nothing on the packet could be read, so the bar is entirely
   amber and there is no green at all. The tool declines to answer rather than
   guessing or returning a clean-looking verdict on no evidence. Point out that
   the unread checks are grouped by cause, so an applicant is asked once for each
   missing value rather than once per rule it blocks.

If there is time, upload a real packet as a fourth. `permit_281364_60839580.pdf`
is a genuine 13 page DNREC packet and reviews at 5 of 15, which is the honest
picture of what the tool can read off a scanned application today. It is the
strongest answer to anyone asking whether this works outside a prepared example.

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
