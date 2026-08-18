# Rule verification checklist

15 rules are staged for certification. Every one is
`verified: false`, so the engine returns UNKNOWN for all of them and the
verdict for any application is CANNOT VERIFY. That is the intended state.

Each block below is self contained. The verbatim quote is the text the
threshold came from, and the cross references and definitions the rule
depends on are inlined underneath it, so a value can be confirmed without
opening the PDF. Page numbers are given for the cases where you want to.

## How to certify a rule

1. Read the quote and confirm it says what the threshold claims.
2. Read the caveats. Several distances have reductions the Department can
   approve, and one rule must not fire on replacement systems at all.
3. Confirm `applies_to` matches the systems the requirement governs.
4. If correct, set `verified: true` in
   `src/septic/rules/rules_7101.yaml` and record your name and the date
   in `notes`.
5. If wrong, leave it unverified and record why. An unverified rule is
   invisible to reviewers, which is the safe direction to fail.

## Regulation

Delaware Regulations Governing On-Site Wastewater Treatment and Disposal
Systems, January 11, 2014. 245 pages.
`docs/regulations/de-onsite-wastewater-2014.pdf`

Graph backing this document: 2102 sections, 40 exhibits, 19 definitions, 2927 edges.

## All rules at a glance

| # | rule | requirement | citation | page |
| --- | --- | --- | --- | --- |
| 1 | `ISO-001-disposal-area-to-well` | >= 100 feet | Exhibit C | 173 |
| 2 | `ISO-002-disposal-area-to-watercourse` | >= 100 feet | Exhibit C | 173 |
| 3 | `ISO-003-disposal-area-to-property-line` | >= 10 feet | Exhibit C | 173 |
| 4 | `ISO-004-disposal-area-to-escarpment` | >= 15 feet | Exhibit C | 173 |
| 5 | `ISO-005-septic-tank-to-well` | >= 50 feet | Exhibit C | 173 |
| 6 | `ISO-006-septic-tank-to-watercourse` | >= 25 feet | Exhibit C | 173 |
| 7 | `PERC-001-site-maximum-percolation-rate` | <= 120 minutes per inch | 5.2.4.2.5.7 | 52 |
| 8 | `PERC-002-percolation-test-hole-count` | >= 3 holes | 5.2.4.2.2 | 51 |
| 9 | `SEP-001-limiting-zone-below-trench-bottom` | >= 36 inches | 5.3.12.1.3 | 61 |
| 10 | `SEP-002-conventional-limiting-zone-minimum-depth` | >= 20 inches | 5.2.4.2.4.2 | 51 |
| 11 | `FLOW-001-residential-minimum-design-flow` | >= 240 gallons per day | 5.3.3.3 | 56 |
| 12 | `FLOW-002-residential-flow-per-bedroom` | >= 120 gallons per day per bedroom | 5.3.3.3 | 56 |
| 13 | `SLOPE-001-gravity-bed-maximum-slope` | <= 2 percent | 5.3.12.1.2 | 60 |
| 14 | `SITE-001-site-evaluation-report-present` | no threshold (presence check) | 5.2.1.1 | 43 |
| 15 | `SITE-002-wells-within-150-feet-shown` | no threshold (presence check) | 5.2.1.5 | 44 |

---

## 1. ISO-001-disposal-area-to-well

- Requirement: **>= 100 feet**
- Parameter checked: `dist_disposal_to_well`
- Citation: **Exhibit C, page 173**
- Severity if failed: return
- Applies when: `system_scale` = small
- Verified: **False**

The disposal area must be at least 100 feet from a well.

**Verbatim text from the regulation**

> MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS, row "Disposal area", column "Well": 100, notes a, c, d, e, h, i

**Exhibit C content, page 173**

```
MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse
Dwellings and 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or
Escarpment 
>25% 
Septic tank 
Grease trap 
Distribution box 
Dosing chamber 
Diversion valve 
or box 
Advanced 
treatment unit 
50 10 25 10 f -- -- 
Disposal area 100 
a, c, d, e, 
h, i 
10 100 b 10 g 10 15
MINIMUM ISOLATION DISTANCES (FEET) FOR LARGE SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse Dwellings & 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or 
Escarpment 
>25% 
```

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read page 173 (Exhibit C small systems table) and page 174 (the notes). Obligation followed from Section 5.3.4.1 on page 57 via the REFERENCES edge in the graph: "The minimum isolation distances set forth in Exhibit C shall be maintained when designing, locating, repairing, replacing, and installing on-site wastewater treatment and disposal systems." Four documented reductions apply and a reviewer must check them before treating a shortfall as a deficiency. Note a: 50 feet may be approved under the Delaware Regulations Governing the Construction and Use of Wells. Note e: 50 feet may be considered for replacement systems on lots recorded before April 8, 1984 where lot size will not allow 100 feet, subject to well casing and grouting requirements. Note h: replacement systems may reduce to 50 feet with an additional 12 inches of suitable soil. Note i: the Department may reduce to 50 feet where advanced treatment is incorporated. Note d raises the distance to 150 feet for public or industrial wells, so this rule is the residential domestic well case and a separate rule is needed for public wells. Note c changes the measurement datum for elevated sand mound and capping fill systems to the outer edge of the stone or gravel-less chamber.

**Remedy shown to the applicant**

Move the disposal area so it is at least 100 feet from every well shown on the site plan, or document the Department approval that permits a lesser distance under Exhibit C note a, e, h or i.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 2. ISO-002-disposal-area-to-watercourse

- Requirement: **>= 100 feet**
- Parameter checked: `dist_disposal_to_watercourse`
- Citation: **Exhibit C, page 173**
- Severity if failed: return
- Applies when: `system_scale` = small
- Verified: **False**

The disposal area must be at least 100 feet from a watercourse.

**Verbatim text from the regulation**

> MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS, row "Disposal area", column "Watercourse": 100, note b

**Exhibit C content, page 173**

```
MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse
Dwellings and 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or
Escarpment 
>25% 
Septic tank 
Grease trap 
Distribution box 
Dosing chamber 
Diversion valve 
or box 
Advanced 
treatment unit 
50 10 25 10 f -- -- 
Disposal area 100 
a, c, d, e, 
h, i 
10 100 b 10 g 10 15
MINIMUM ISOLATION DISTANCES (FEET) FOR LARGE SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse Dwellings & 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or 
Escarpment 
>25% 
```

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read page 173 (table) and page 174 (notes). Note b is load bearing here and a reviewer should read it before flagging: a lesser distance to a minimum of 50 feet may be approved if the watercourse has not been designated for use as a public water supply or shellfish, and there is no setback at all from an ephemeral watercourse. Note b also assigns the determination of whether a watercourse is ephemeral to the Class D soil scientist, so this check cannot be settled from the site plan alone when the classification is contested.

**Remedy shown to the applicant**

Move the disposal area so it is at least 100 feet from the watercourse, or document that the watercourse is ephemeral or not designated for public water supply or shellfish use under Exhibit C note b.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 3. ISO-003-disposal-area-to-property-line

- Requirement: **>= 10 feet**
- Parameter checked: `dist_disposal_to_property_line`
- Citation: **Exhibit C, page 173**
- Severity if failed: return
- Applies when: `system_scale` = small
- Verified: **False**

The disposal area must be at least 10 feet from dwellings and property lines.

**Verbatim text from the regulation**

> MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS, row "Disposal area", column "Dwellings and Property Lines": 10, note g

**Exhibit C content, page 173**

```
MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse
Dwellings and 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or
Escarpment 
>25% 
Septic tank 
Grease trap 
Distribution box 
Dosing chamber 
Diversion valve 
or box 
Advanced 
treatment unit 
50 10 25 10 f -- -- 
Disposal area 100 
a, c, d, e, 
h, i 
10 100 b 10 g 10 15
MINIMUM ISOLATION DISTANCES (FEET) FOR LARGE SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse Dwellings & 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or 
Escarpment 
>25% 
```

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read page 173 (table) and page 174 (notes). The table column covers dwellings and property lines with the same value, so one rule serves both only if the extractor reports the smaller of the two measured distances. That is a scope ambiguity a reviewer should settle: if the extractor cannot distinguish them, splitting this into two rules is the safer shape. Note g permits 5 feet from an interior lot or easement line within a recorded subdivision where the absorption facility serves a central sewer system.

**Remedy shown to the applicant**

Move the disposal area so it is at least 10 feet from every property line and dwelling shown on the site plan.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 4. ISO-004-disposal-area-to-escarpment

- Requirement: **>= 15 feet**
- Parameter checked: `dist_disposal_to_escarpment`
- Citation: **Exhibit C, page 173**
- Severity if failed: return
- Applies when: `system_scale` = small
- Verified: **False**

The disposal area must be at least 15 feet from the top of a bank or an escarpment steeper than 25 percent.

**Verbatim text from the regulation**

> MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS, row "Disposal area", column "Top of Bank or Escarpment >25%": 15

**Exhibit C content, page 173**

```
MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse
Dwellings and 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or
Escarpment 
>25% 
Septic tank 
Grease trap 
Distribution box 
Dosing chamber 
Diversion valve 
or box 
Advanced 
treatment unit 
50 10 25 10 f -- -- 
Disposal area 100 
a, c, d, e, 
h, i 
10 100 b 10 g 10 15
MINIMUM ISOLATION DISTANCES (FEET) FOR LARGE SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse Dwellings & 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or 
Escarpment 
>25% 
```

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read page 173. This cell carries no note letters, so the value stands without documented reductions, which makes it one of the cleaner rules in this set. The column heading qualifies the trigger as an escarpment steeper than 25 percent, so the rule only fires when the site has one. Section 5.2.1.9.5 on page 45 requires escarpments to be recorded in the site evaluation report, which is where the input for this check comes from.

**Remedy shown to the applicant**

Move the disposal area so it is at least 15 feet from the top of the bank or escarpment, or show on the site plan that no escarpment steeper than 25 percent is present.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 5. ISO-005-septic-tank-to-well

- Requirement: **>= 50 feet**
- Parameter checked: `dist_tank_to_well`
- Citation: **Exhibit C, page 173**
- Severity if failed: return
- Applies when: `system_scale` = small
- Verified: **False**

The septic tank must be at least 50 feet from a well.

**Verbatim text from the regulation**

> MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS, row "Septic tank Grease trap Distribution box Dosing chamber Diversion valve or box Advanced treatment unit", column "Well": 50

**Exhibit C content, page 173**

```
MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse
Dwellings and 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or
Escarpment 
>25% 
Septic tank 
Grease trap 
Distribution box 
Dosing chamber 
Diversion valve 
or box 
Advanced 
treatment unit 
50 10 25 10 f -- -- 
Disposal area 100 
a, c, d, e, 
h, i 
10 100 b 10 g 10 15
MINIMUM ISOLATION DISTANCES (FEET) FOR LARGE SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse Dwellings & 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or 
Escarpment 
>25% 
```

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read page 173. The 50 foot value is shared by six components on one table row: septic tank, grease trap, distribution box, dosing chamber, diversion valve or box, and advanced treatment unit. This rule covers the septic tank only, because that is the component a residential site plan always shows. A reviewer should decide whether the other five components warrant their own rules or whether the extractor should report the minimum distance across all tank-side components. This cell carries no note letters.

**Remedy shown to the applicant**

Move the septic tank so it is at least 50 feet from every well shown on the site plan.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 6. ISO-006-septic-tank-to-watercourse

- Requirement: **>= 25 feet**
- Parameter checked: `dist_tank_to_watercourse`
- Citation: **Exhibit C, page 173**
- Severity if failed: return
- Applies when: `system_scale` = small
- Verified: **False**

The septic tank must be at least 25 feet from a watercourse.

**Verbatim text from the regulation**

> MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS, row "Septic tank Grease trap Distribution box Dosing chamber Diversion valve or box Advanced treatment unit", column "Watercourse": 25

**Exhibit C content, page 173**

```
MINIMUM ISOLATION DISTANCES (FEET) FOR SMALL SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse
Dwellings and 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or
Escarpment 
>25% 
Septic tank 
Grease trap 
Distribution box 
Dosing chamber 
Diversion valve 
or box 
Advanced 
treatment unit 
50 10 25 10 f -- -- 
Disposal area 100 
a, c, d, e, 
h, i 
10 100 b 10 g 10 15
MINIMUM ISOLATION DISTANCES (FEET) FOR LARGE SYSTEMS
Components Well Water 
Supply 
Pressure 
Line 
Watercourse Dwellings & 
Property Lines 
Other 
active 
on-lot 
systems 
Top of Bank 
or 
Escarpment 
>25% 
```

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read page 173. Note that the tank row value of 25 feet is a different and smaller distance than the disposal area row value of 100 feet in the same column, and that the tank cell carries no note letters while the disposal area cell carries note b. Reading across the wrong row here would understate the disposal area requirement by 75 feet, which is why the column alignment was verified against pdfplumber extract_tables as well as the text layer.

**Remedy shown to the applicant**

Move the septic tank so it is at least 25 feet from the watercourse.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 7. PERC-001-site-maximum-percolation-rate

- Requirement: **<= 120 minutes per inch**
- Parameter checked: `perc_rate`
- Citation: **5.2.4.2.5.7, page 52**
- Severity if failed: return
- Applies when: always, the requirement is unconditional
- Verified: **False**

A system may not be placed on soil with a percolation rate slower than 120 minutes per inch.

**Verbatim text from the regulation**

> On-site wastewater treatment and disposal systems shall not be placed on those portions of any sites that have percolation rates slower than 120 mpi.

**Where this sits:** 5 6.5.1.4 Design Engineer Report > 5.2 Soil Investigations > 5.2.4 Soil Percolation Rate Determination > 5.2.4.2 Soil Percolation Test > 5.2.4.2.5 The following procedures shall be used f

**Rest of the cited section**

> arithmetic average of all percolation tests conducted. Percolation rates slower than 120 minutes per inch (mpi) are unacceptable and shall not be used to determine the arithmetic average percolation rate but shall be reported. On-site wastewater treatment and disposal systems shall not be placed on those portions of any sites that have percolation rates slower than 120 mpi.

**Defined terms used in this section**

- "Disposal" (defined in 2.0)
- "Treatment" (defined in 2.0)

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read page 52. This is the cleanest threshold in the regulation: the sentence states the direction in words rather than a symbol, so it does not depend on the missing glyph problem that affected other passages. The same 120 minutes per inch limit is stated three more times for specific system types, which corroborates it: Section 5.2.1.3.1 on page 43 calls rates over 120 minutes per inch very slowly permeable and a limiting layer, Section 5.3.12.1.4.1 on page 61 forbids seepage trenches and beds above 120, and Section 5.3.12.4.4 on page 62 forbids elevated sand mounds above 120. applies_to is empty because the prohibition is written against any site and any system, not a subset. Section 5.2.4.2.5.7 also states that rates slower than 120 are excluded from the arithmetic average but must still be reported, so a reviewer should confirm which number the application is quoting: the site average or an individual test hole.

**Remedy shown to the applicant**

The soil in the proposed disposal area percolates too slowly for any system under these Regulations. Relocate the disposal area to soil that percolates at 120 minutes per inch or faster, or apply for an innovative or alternative system.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 8. PERC-002-percolation-test-hole-count

- Requirement: **>= 3 holes**
- Parameter checked: `perc_test_holes`
- Citation: **5.2.4.2.2, page 51**
- Severity if failed: return
- Applies when: always, the requirement is unconditional
- Verified: **False**

A soil percolation test must consist of three test holes.

**Verbatim text from the regulation**

> One (1) soil percolation test shall consist of three (3) test holes.

**Where this sits:** 5 6.5.1.4 Design Engineer Report > 5.2 Soil Investigations > 5.2.4 Soil Percolation Rate Determination > 5.2.4.2 Soil Percolation Test

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read pages 51 and 52. Section 5.2.4.2.5.1 on page 51 states the same requirement from the other direction, as a minimum of three test holes dug within the proposed installation area, and permits the Department to require additional tests. Written as a minimum rather than an equality for that reason. Section 5.2.4.2.5.6 on page 52 permits a hole to be excluded from the analysis if the licensed percolation tester determines it is uncharacteristic of the site, but requires it to be listed on the application, so a reviewer checking this should count holes listed rather than holes averaged.

**Remedy shown to the applicant**

Record the results of all three percolation test holes on the application.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 9. SEP-001-limiting-zone-below-trench-bottom

- Requirement: **>= 36 inches**
- Parameter checked: `limiting_zone_below_trench_bottom`
- Citation: **5.3.12.1.3, page 61**
- Severity if failed: return
- Applies when: `system_type` = conventional, gravity
- Verified: **False**

For a gravity trench or bed system, the limiting zone must be at least 3 feet below the bottom of the trench.

**Verbatim text from the regulation**

> The limiting zone shall be a minimum of three (3) feet below the bottom of the trench

**Where this sits:** 5 6.5.1.4 Design Engineer Report > 5.3 Permitting > 5.3.12 Conventional On-Site Wastewater Treatmen > 5.3.12.1 All Full Depth Gravity and Capping Fill

**Rest of the cited section**

> (3) feet below the bottom of the trench t 48 inches beneath the soil surface.

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read pages 60 and 61. Section 5.3.12.1 is titled "All Full Depth Gravity and Capping Fill Gravity Trench and Bed Treatment and Disposal Systems", which is the conventional gravity system this rule set targets. Threshold converted from the 3 feet in the text to 36 inches so it shares units with the other separation rule; a reviewer should confirm the unit conversion is acceptable or change the rule to feet. IMPORTANT: the quoted sentence continues past where this quote stops, and the continuation is not safely readable. The raw extraction is "a minimum of three (3) feet below the bottom of the trench t 48 inches beneath the soil surface", where "t" is a glyph PDFium failed to map. Comparing against the parallel wording in Section 5.3.12.5.3 on page 62, which reads "48 inches from original grade and three (3) feet below bottom of filter aggregate", the missing character is most likely "and" or a greater-than-or-equal sign, which would make a 48 inch depth below the soil surface a second and separate requirement. That second requirement was NOT promoted to a rule because the operator cannot be read from the text. A reviewer with the paper regulation should settle it and add the rule.

**Remedy shown to the applicant**

Raise the trench bottom or relocate the disposal area so at least 36 inches of soil separate the trench bottom from the limiting zone.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 10. SEP-002-conventional-limiting-zone-minimum-depth

- Requirement: **>= 20 inches**
- Parameter checked: `limiting_zone_depth`
- Citation: **5.2.4.2.4.2, page 51**
- Severity if failed: return
- Applies when: `system_type` = conventional, gravity
- Verified: **False**

A site whose limiting zone is shallower than 20 inches is unsuitable for a conventional system.

**Verbatim text from the regulation**

> If the limiting zone occurs at less than 20 inches from the surface, the site is unsuitable for a conventional on-site wastewater treatment and disposal system.

**Where this sits:** 5 6.5.1.4 Design Engineer Report > 5.2 Soil Investigations > 5.2.4 Soil Percolation Rate Determination > 5.2.4.2 Soil Percolation Test > 5.2.4.2.4 The depth of the percolation test holes

**Rest of the cited section**

> surface, the site is unsuitable for a conventional on-site wastewater treatment and disposal system. However, if replacing a failing or malfunctioning system, Section 5.2.4.2.4.1 should be used without regard for the 20 inch limiting condition. In situations where sand-lining through an impermeable or less permeable horizon within the top 48 inches, a percolation test should be performed within the soil zone which is controlling the water movement vertically and/or horizontally beneath the restrictive material to a depth of 60 inches.

**Cross references, resolved**

- Section 5.2.4.2.4: The depth of the percolation test holes shall not be determined until 
  > site evaluation is completed and a limiting zone, if any, is identified. The depth of the percolation test holes shall be as follows:

**Defined terms used in this section**

- "Disposal" (defined in 2.0)
- "Treatment" (defined in 2.0)

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read page 51. The sentence states the direction in words, so it does not depend on a symbol. The same section carries an explicit exception a reviewer must apply: when replacing a failing or malfunctioning system, Section 5.2.4.2.4.1 is used "without regard for the 20 inch limiting condition", so this rule must not fire on a repair or replacement application. applies_to cannot express that yet because it tests equality against facts and the condition needed is the absence of a replacement flag. Until the extractor supplies a construction type fact, a reviewer should read a failure of this rule on a replacement application as not applicable. This is the clearest case in the set where applies_to is too weak for the regulation, and it should be raised before certification.

**Remedy shown to the applicant**

The site is unsuitable for a conventional system at this limiting zone depth. Consider an alternative system type, or a replacement system design under the exception in Section 5.2.4.2.4.2.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 11. FLOW-001-residential-minimum-design-flow

- Requirement: **>= 240 gallons per day**
- Parameter checked: `design_flow`
- Citation: **5.3.3.3, page 56**
- Severity if failed: return
- Applies when: `use_type` = residential
- Verified: **False**

The design flow for a residential dwelling must be at least 240 gallons per day.

**Verbatim text from the regulation**

> The minimum design flow for any commercial property shall be 120 gallons per day and residential dwellings shall be 240 gallons per day.

**Where this sits:** 5 6.5.1.4 Design Engineer Report > 5.3 Permitting > 5.3.3 Wastewater Design Flow Rates

**Rest of the cited section**

> family, multiple family, manufactured homes, and apartments served by on-site wastewater treatment and disposal systems shall be 120 gallons per day per bedroom. The minimum design flow for any commercial property shall be 120 gallons per day and residential dwellings shall be 240 gallons per day. Credit for water conservation devices will be accounted for according to current Department guidelines.

**Defined terms used in this section**

- "Disposal" (defined in 2.0)
- "Treatment" (defined in 2.0)

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read page 56. The quoted sentence sets two different floors in one sentence, 120 gallons per day for commercial and 240 for residential, so applies_to restricts this rule to residential use and a separate rule would be needed for commercial. The 240 gallon floor interacts with the per bedroom figure in FLOW-002: a two bedroom dwelling at 120 gallons per bedroom reaches exactly 240, so the floor only binds on a one bedroom dwelling. Section 5.3.5.1 on page 57 allows a 25 percent reduction in design flow for water conservation, but Section 5.3.5.2 states that reduction is not permissible for new construction, so a reviewer checking a new build should not accept a reduced figure.

**Remedy shown to the applicant**

Size the system for at least 240 gallons per day, which is the floor for a residential dwelling regardless of bedroom count.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 12. FLOW-002-residential-flow-per-bedroom

- Requirement: **>= 120 gallons per day per bedroom**
- Parameter checked: `design_flow_per_bedroom`
- Citation: **5.3.3.3, page 56**
- Severity if failed: return
- Applies when: `use_type` = residential
- Verified: **False**

The design flow for a residential dwelling must be at least 120 gallons per day per bedroom.

**Verbatim text from the regulation**

> The design wastewater flow from residential dwellings, including single family, multiple family, manufactured homes, and apartments served by on-site wastewater treatment and disposal systems shall be 120 gallons per day per bedroom.

**Where this sits:** 5 6.5.1.4 Design Engineer Report > 5.3 Permitting > 5.3.3 Wastewater Design Flow Rates

**Rest of the cited section**

> family, multiple family, manufactured homes, and apartments served by on-site wastewater treatment and disposal systems shall be 120 gallons per day per bedroom. The minimum design flow for any commercial property shall be 120 gallons per day and residential dwellings shall be 240 gallons per day. Credit for water conservation devices will be accounted for according to current Department guidelines.

**Defined terms used in this section**

- "Disposal" (defined in 2.0)
- "Treatment" (defined in 2.0)

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read page 56. The regulation states this as an equality, "shall be 120 gallons per day per bedroom", not as a minimum. It is written here as a minimum because a system sized above the requirement is not a deficiency a reviewer would return, while one sized below it is. A reviewer should confirm that reading, because it is an interpretation and not what the text literally says. This rule needs a derived fact: design flow divided by bedroom count. If the extractor cannot read the bedroom count from the packet the rule returns UNKNOWN rather than passing, which is the intended behaviour.

**Remedy shown to the applicant**

Size the system for at least 120 gallons per day for each bedroom in the dwelling.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 13. SLOPE-001-gravity-bed-maximum-slope

- Requirement: **<= 2 percent**
- Parameter checked: `disposal_slope`
- Citation: **5.3.12.1.2, page 60**
- Severity if failed: return
- Applies when: `system_type` = conventional, gravity; `absorption_type` = bed
- Verified: **False**

A bed system may not be sited on a slope steeper than 2 percent.

**Verbatim text from the regulation**

> Bed systems cannot be sited on slopes > 2%, unless otherwise approved by the Department.

**Where this sits:** 5 6.5.1.4 Design Engineer Report > 5.3 Permitting > 5.3.12 Conventional On-Site Wastewater Treatmen > 5.3.12.1 All Full Depth Gravity and Capping Fill

**Rest of the cited section**

> otherwise approved by the Department. All systems must be constructed with level bottoms and shall incorporate construction procedures prohibiting equipment from entering the excavation. Trench systems on slopes in excess of

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read pages 60 and 61. The comparison direction is explicit in the text as a greater-than sign, so this passage is not affected by the missing glyph problem. The trailing clause "unless otherwise approved by the Department" is a real discretionary exception, so a failure here is a question for the reviewer rather than a settled deficiency, and the remedy is worded to say so. Severity is return rather than advisory because an application showing a bed on a 5 percent slope with no documented approval is incomplete as submitted. The same section allows trench systems from 0 to 15 percent and permits steeper than 15 percent only where a licensed Class C designer prepared the design, which is a separate rule not promoted here because the condition is a licence class rather than a measurement.

**Remedy shown to the applicant**

Relocate the bed to ground at 2 percent slope or flatter, change to a trench system, or document the Department approval permitting the steeper slope.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 14. SITE-001-site-evaluation-report-present

- Requirement: **no threshold (presence check)**
- Parameter checked: `site_evaluation_report`
- Citation: **5.2.1.1, page 43**
- Severity if failed: return
- Applies when: always, the requirement is unconditional
- Verified: **False**

A site evaluation report prepared by a Class D soil scientist must be obtained before a construction permit.

**Verbatim text from the regulation**

> Any person applying for a permit to install a new or replacement on-site wastewater treatment and disposal system shall first obtain a site evaluation report prepared by a Class D soil scientist.

**Where this sits:** 5 6.5.1.4 Design Engineer Report > 5.2 Soil Investigations > 5.2.1 Site Evaluation Procedures

**Rest of the cited section**

> permit for an on-site wastewater treatment and disposal system. Any person applying for a permit to install a new or replacement on-site wastewater treatment and disposal system shall first obtain a site evaluation report prepared by a Class D soil scientist. The Department shall only conduct site evaluations for Home Rehabilitation Loan Programs (HRLP), block grant households, State Revolving Fund (SRF) sites and other qualifying income programs with similar criteria.

**Defined terms used in this section**

- "Disposal" (defined in 2.0)
- "Treatment" (defined in 2.0)

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read page 43. This rule takes no threshold, so certifying it means confirming the requirement exists and that the packet is expected to contain the report, not confirming a number. Section 5.2.1.2 on page 43 lists what the report must contain at a minimum: approval pages, report pages, site drawing, soil profile notes, zoning verification form, and the appropriate fee, and each of those is a candidate rule of its own. The section carves out Department-conducted evaluations for Home Rehabilitation Loan Programs, block grant households, State Revolving Fund sites and similar income qualifying programs, which does not change whether a report is required but does change who prepares it.

**Remedy shown to the applicant**

Attach the site evaluation report prepared by a Class D soil scientist.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## 15. SITE-002-wells-within-150-feet-shown

- Requirement: **no threshold (presence check)**
- Parameter checked: `wells_within_150_feet_shown`
- Citation: **5.2.1.5, page 44**
- Severity if failed: return
- Applies when: always, the requirement is unconditional
- Verified: **False**

The site drawing must show every on-site and adjacent well within 150 feet of the approved soils area.

**Verbatim text from the regulation**

> Site drawings will show the location of all on-site and adjacent wells within 150 feet of the approved soils area.

**Where this sits:** 5 6.5.1.4 Design Engineer Report > 5.2 Soil Investigations > 5.2.1 Site Evaluation Procedures

**Rest of the cited section**

> 150 feet of the approved soils area. The following procedure shall be used in all cases when on-site or adjacent well(s) cannot be located. For instances where the on-site or adjacent well(s) are below ground and the homeowner or adjacent property owner states that the well is located in a certain area, this information shall suffice for verification of well location. Any well(s) that cannot be verified must be researched through the Water Supply Section of the Department. The search attempts to locate any well(s) that are near the affected parcel. If, after this search is completed, the well location(s) cannot be identified the Class D soil scientist can state “records were researched under this property owner’s name and no information was found”. The Department then sends a letter to the adjacent well owners notifying them of the need to locate their well(s) due to the future installat

**Defined terms used in this section**

- "Disposal" (defined in 2.0)
- "Treatment" (defined in 2.0)

Dependency check: every section and exhibit this rule depends on has been read.

**What was read, and what to watch for**

Read pages 44 and 45. Section 5.2.1.9.4 on page 45 states the same requirement in the list of report contents and adds that wells must be measured from two reference points or established survey control. The 150 foot radius here is a drawing requirement and is deliberately larger than the 100 foot isolation distance in ISO-001, so that a well which constrains the design cannot be omitted for being outside the setback. Section 5.2.1.5 also sets out what to do when a well cannot be located, including a Water Supply Section records search and a 15 day notice to adjacent owners, after which the system must be designed to maximise the distance from the property line. A packet using that path is not deficient, so this rule is satisfied by either the wells being shown or that documentation being present, which the present operator cannot express on its own. A reviewer should decide whether the extractor sets this fact for the documented-search case.

**Remedy shown to the applicant**

Mark every on-site and adjacent well within 150 feet of the approved soils area on the site drawing, or record that a well could not be located and the search that was performed.

**Certification**

- [ ] Quote matches the PDF at the cited page
- [ ] Threshold and units are correct
- [ ] `applies_to` matches the systems governed
- [ ] Caveats above are acceptable
- Checked by: ____________________  Date: ____________

---

## Candidates read and not promoted

A threshold nobody could confirm is a rejection, not a guess. These were
read and left out, with the reason. Several are promotable once the
extractor produces one more fact, or once someone checks the paper copy.

### Limiting zone at least 48 inches beneath the soil surface

- Source: 5.3.12.1.3, page 61
- Why not promoted: The operator is unreadable. The sentence extracts as 'a minimum of three (3) feet below the bottom of the trench t 48 inches beneath the soil surface', where 't' is a glyph PDFium failed to map. Comparing the parallel wording in 5.3.12.5.3 on page 62 suggests the missing character joins two separate requirements, but that is inference, not reading. The 3 feet below trench bottom half of the same sentence was promoted as SEP-001 because it is spelled out in words.

### Minimum design percolation rate of 20 minutes per inch

- Source: 5.3.2.1, page 55
- Why not promoted: Real requirement, wrong parameter. The text forbids designing with a rate below 20 minutes per inch, which constrains the design figure, not the measured site rate. Sections 5.3.2.2 through 5.3.2.5 confirm this by repeating 'minimum rate is 20 mpi for design' while still allowing faster soils, and 5.3.2.4 requires a pressurized system below 6 mpi rather than rejecting the site. Mapping this onto the measured perc rate would fail sandy sites the regulation permits.

### Minimum of three soil borings or two test pits per acre

- Source: 5.2.1.9.8, page 45
- Why not promoted: Two alternative satisfying conditions joined by 'or' cannot be one numeric comparison, and the engine has no operator for 'either A or B'. Promoting the borings half alone would fail a packet that correctly used test pits.

### Trench systems permitted on slopes steeper than 15 percent

- Source: 5.3.12.1.2, page 60
- Why not promoted: The condition is a licence class, not a measurement: steeper than 15 percent is allowed only where a licensed Class C designer prepared the design. The threshold is checkable but the exemption depends on a fact about the designer that the extractor does not yet produce, so the rule would fire on compliant applications.

### Public or industrial well isolation distance of 150 feet

- Source: Exhibit C note d, page 174
- Why not promoted: Confirmed in the source and worth promoting later, but it needs a fact distinguishing a public or industrial well from a domestic one, which the extractor does not produce. Shipping it without that fact would either never fire or fire on every domestic well. Recorded in ISO-001 notes so the reviewer sees the interaction.

### Assigned percolation rate floor of 60 or 75 minutes per inch

- Source: 5.2.1.3.1.4.1, page 44
- Why not promoted: The operator is unreadable, same glyph problem: 'For systems with a separation distance of  24 inches'. Which side of 24 inches selects the 75 mpi floor and which selects 60 cannot be read from the text, and the two branches give different answers.

## Coverage gap

570 sections in the regulation use obligation language (shall,
must, minimum) and are not cited by any rule. That is the backlog, and it
is the honest measure of how much of the regulation this tool does not yet
check. The first 40 are listed as a starting point for the next round.

| section | page | opening text |
| --- | --- | --- |
| 1.3 | 9 | These Regulations shall supersede and replace the Regulations Governing the |
| 2 | 2 | TABLE OF CONTENTS |
| 3.5 | 26 | If any part of these Regulations, or the application of any part thereof, is hel |
| 3.6 | 26 | These Regulations, being necessary for the health and welfare of the State and i |
| 3.7 | 26 | At the sole discretion of the Department, if the proposed operation of a system  |
| 3.10 | 26 | Discharge of untreated or partially treated wastewater or septic tank effluent d |
| 3.11 | 26 | Except where specifically allowed within these Regulations, no person shall conn |
| 3.15 | 27 | The Department shall impose, in any permit, standards for evaluating treatment |
| 3.22.1 | 27 | Certification by a registered professional engineer (Class C) that all new and |
| 3.23.1 | 27 | When the Department determines that construction of on-site wastewater |
| 3.24 | 27 | Whenever the preparation of reports or other documents required by these |
| 3.26.2 | 28 | The failure of the Department to enforce any of the provisions of these |
| 3.29.2.1 | 28 | Require a new application package and review fee in order to continue |
| 3.30 | 28 | All new and replacement systems permitted within 1,000 feet of the Chesapeake |
| 3.31.2 | 28 | Each system shall have adequate capacity to properly treat and dispose of the |
| 3.31.3 | 28 | A recorded utility easement is required whenever a system crosses a property |
| 3.31.6 | 29 | Whenever real property is recorded as two separate lots under common |
| 3.31.9.3 | 29 | For proposed subdivision or other developments with more than five (5) |
| 3.31.11 | 29 | When a central wastewater system is deemed both physically and legally |
| 3.31.13 | 29 | For all properties utilizing an OWTDS that are sold or otherwise transferred to |
| 3.31.15.1.1.1 | 30 | For single family residences, only the area within the property |
| 3.31.15.1.1.2 | 30 | For multiple family dwellings or where more than one (1) |
| 3.31.15.1.1.2.1 | 30 | For projects utilizing only a septic tank for treatment prior |
| 3.31.15.1.2 | 30 | For commercial facilities the maximum siting density shall be |
| 3.31.15.1.3 | 30 | In establishing maximum siting densities the Department may |
| 3.31.15.2.2 | 31 | At the time the permit is issued or feasibility study is approved, the |
| 3.31.16 | 31 | All new and replacement small systems requiring advanced treatment units |
| 3.32.1 | 31 | Whenever the preparation of reports or other documents required by these |
| 3.32.2 | 31 | For large systems which serve communities that experience a significant |
| 3.32.3 | 31 | Unless otherwise required by a permit the permittee and operator, if |
| 3.32.6.5.4 | 32 | In writing as soon as possible but within five (5) days of the date |
| 3.32.6.5.5 | 32 | In writing as soon as possible after the permittee becomes aware |
| 4.0 | 2 | Licenses |
| 4.4 | 33 | On-Site System Advisory Board (Board) approval to take an exam is valid for six  |
| 4.6 | 33 | In the event an applicant fails to receive a passing grade on the examination, h |
| 4.8.1 | 34 | Class D.1 is licensed to perform individual site evaluations for both new |
| 4.8.1.7 | 34 | Pass a field practicum prepared and administered by the Site |
| 4.8.3 | 35 | Class D.3 is licensed to perform all soils work licensed under Sections 4.8.1 |
| 4.9.1 | 35 | E.1 is licensed to install all conventional on-site wastewater treatment and |
| 4.9.1.2 | 35 | A minimum of two (2) years of experience under the guidance of an |

