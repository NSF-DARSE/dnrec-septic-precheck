# Decision: graph backend

Date: 2026-08-17
Outcome: networkx 3.6.1, in process, persisted as JSON at out/reg_graph.json.

## What was evaluated

Semantica 0.6.5 (MIT, pip install semantica) was evaluated as a graph-native
context and provenance layer. The attraction was PROV-O lineage on every fact:
this tool has to justify every flag to a state regulator, and a citation chain
that is a queryable object with recorded lineage is worth more than a string in a
YAML field.

Four questions were asked, and the code to answer them was run on this machine.

1. Does it install cleanly with no external service? PARTIAL. It installs, but it
   pulls 62 transitive packages including spacy, transformers, opencv-python,
   faiss-cpu and sentence-transformers, and it resolves numpy to 2.x. numpy 2.x
   breaks h5py and pandas 1.5.3, which are compiled against the numpy 1.x C API.
   That resolution is what broke the global interpreter on this machine and is why
   requirements.txt now pins numpy explicitly.

2. Can it ingest the regulation and produce nodes for numbered sections without a
   hand written parser? NO. ingest_file returns flat text with no section
   structure. The chunking module that would split it crashes on the numpy
   incompatibility above, and even working it splits on NLP sentence boundaries
   rather than dotted section numbering like 5.3.4.1. The custom section parser
   was needed either way.

3. Can it record a fact with PROV-O provenance and retrieve the lineage back?
   YES. ProvenanceManager with InMemoryStorage recorded an entity and returned a
   lineage chain with source document, checksum, and an integrity flag.

4. Can it traverse from a section to a section it cross references? NO. GraphStore
   supports neo4j, amazon_neptune, falkordb and apache_age. There is no in memory
   or networkx backend. create_node raises ProcessingError without a Neo4j server.

## Why networkx

The decision rule required all four to answer yes. Two answered no, one partially.
A graph of roughly 1500 nodes and 2100 edges belongs in a file, not behind a
server, and standing up a database contradicts the no external services
constraint.

The provenance module is the one piece worth revisiting. If rule citations need
recorded lineage later, Semantica's ProvenanceManager does that part well and
could be adopted without taking its graph store. The public functions in
src/septic/rules/graph.py were given backend independent signatures and return
shapes so that swap stays cheap.


---

# Decision: Bedrock Data Automation for site plan extraction

Date: 2026-08-18
Outcome: negative. Textract stays the default. BDA returns the same content
Textract does and none of the seven parameters the rules need from a site plan.

## What was tested

Page 6 of permit 281364, which is the scanned site plan that carries the
isolation distances six rules need. The question was whether Bedrock Data
Automation could read spatial relationships off a raster drawing that Textract
cannot measure.

The BDA response was diffed line by line against the Textract cache for the same
page. Both outputs are 117 lines. The differences are OCR noise only: minor
character substitutions, whitespace, and confidence scores. Neither output returns
any of the seven parameters the rules need:

    dist_disposal_to_well
    dist_disposal_to_watercourse
    dist_disposal_to_property_line
    dist_disposal_to_escarpment
    dist_tank_to_well
    dist_tank_to_watercourse
    disposal_slope

Both services read the text that is printed on the drawing (the title block, the
legend, the scale notation). Neither reads the distances that are dimensioned
between drawn features, because those distances are spatial relationships between
line segments relative to a scale bar, not printed text. No document extractor of
any kind reads them. Reading them requires geometric measurement of the drawing:
detecting the line features, computing pixel distances, and dividing by the scale
factor.

## What BDA did not do

BDA fabricated nothing. It did not hallucinate distances, invent parameters, or
return values the drawing does not state in text. That is the right failure mode
for a tool that shows findings to a state regulator.

## What the probe left behind

Nothing. The BDA call was a one-shot API probe. No pipeline was built, no
configuration was saved, and no BDA output is cached anywhere in this repository.
The Textract cache for permit 281364 is unchanged.

## Infrastructure note

The probe required upgrading boto3 from 1.35.x to 1.38.x for the
bedrock-data-automation-runtime client name. The earlier boto3 did not know that
service endpoint existed. The upgrade is in requirements.txt.

## Why Textract stays

BDA was evaluated because it advertises document understanding beyond OCR. On this
document, its output is equivalent to Textract's. The parameters the rules need
are not text extraction problems. They are measurement problems on a raster image,
and they remain the gap described in the README status section: 10 of the 15 rules
need a measurement that lives on a scanned drawing, and no document service reads
them. The path to those values runs through computer vision (line detection, scale
calibration, feature identification), not through a better document extractor.
