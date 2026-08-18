# retrieval

Embeddings and the local permit index.

Retrieval never decides an outcome. The verdict comes from the rule engine and
nothing here can change it. What retrieval supplies is precedent: permits with
comparable soil, system type and lot characteristics, and what happened to them.
That gives a reviewer something to compare against, but a similar permit being
approved is not evidence that this one complies.

Start here: `embed.py` builds embeddings using Bedrock Titan. `index.py` manages
the local JSON index scored with a dot product. `search.py` queries the index for
similar permits given a set of facts.
