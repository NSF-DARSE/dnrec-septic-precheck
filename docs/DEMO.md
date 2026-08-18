# Demo walkthrough

## Starting the console

```bash
pip install streamlit
streamlit run app.py
```

No network and no AWS credentials are needed. The console serves Textract output
from the on-disk cache. Uploading a packet that has not been cached will explain
what is needed rather than hanging.

## The three staged packets

| permit | pages | verdict | ran | not applicable | could not be read |
| --- | --- | --- | --- | --- | --- |
| 281364 | 13 | NO DEFICIENCIES FOUND | 5 | 3 | 7 |
| 282133 | 1 | NO DEFICIENCIES FOUND | 1 | 0 | 14 |
| 282863 | 19 | NO DEFICIENCIES FOUND | 2 | 5 | 8 |

Permit 281364 is the projector packet. It has the best coverage of the three: five
checks ran, three were excluded because the system is pressure dosed and those
rules govern gravity systems, and seven could not be read because they need a
dimension off the site plan that the tool cannot extract from a scanned drawing.

## What the screen shows

When the console starts, the main column shows a large dashed drop target reading
"Drop an application packet here." and listing the three possible outcomes.

Once a packet is loaded:

- The drop target folds into a closed expander labelled "Review a different
  packet".
- The main column shows a provenance line (document name, pages, review time),
  then a verdict banner with the coverage figure, then the full report body in an
  embedded frame.
- The sidebar carries "Rules applied" and a "Show all rules" toggle. When that
  toggle is on and no packet is loaded, the main column shows the full reference
  table of all 15 requirements with their thresholds, citations and regulation
  quotes.

## Which packet to upload for the demo

Use permit 281364. It is the only one with enough extracted values to show checks
passing, inapplicable rules being excluded with an explanation, and unreadable
checks being itemised with what the reviewer should look at. The other two read
almost nothing off the packet and mostly show the "could not be read" list.

## Three questions a reviewer will ask

**Why could so many checks not be read?**
Most of the 15 checks need a dimension off a scanned site plan drawing, and the
tool cannot read measurements off raster drawings. It says so honestly and tells
the reviewer exactly which value to look for and where it normally appears.

**Does this tool ever approve or deny anything?**
No. It flags deficiencies and puts the regulation citation next to each one, or it
says it cannot verify, or it reports nothing found. The reviewer decides.

**Where does a finding's citation come from?**
Every threshold is traced to a section and page in the 2014 Delaware regulation.
The rule set carries the verbatim regulation text, and the "Show all rules" toggle
in the sidebar displays all 15 with their page numbers and quotes.
