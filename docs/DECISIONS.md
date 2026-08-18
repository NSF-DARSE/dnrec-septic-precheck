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
