# Neptune Analytics Knowledge Graph — Proof of Concept

## Overview

This directory contains tools for loading the Delaware septic regulation graph
into Amazon Neptune Analytics and verifying parity with the existing NetworkX
implementation.

**The existing NetworkX graph is not replaced.** Neptune is a parallel backend
for evaluation purposes only.

## Architecture

```
out/reg_graph.json  ──→  export.py  ──→  out/neptune_export/*.cypher
                                                  │
                                                  ▼
                                        cli_loader.py  ──→  Neptune Analytics
                                                  │
                                                  ▼
                                     cli_query_client.py  ──→  parity_check.py
                                                          ──→  demo_compare.py
```

## Files

| File | Purpose |
|------|---------|
| `export.py` | Converts `out/reg_graph.json` to openCypher MERGE statements |
| `cli_loader.py` | Sends openCypher statements to Neptune via AWS CLI |
| `loader.py` | Sends openCypher statements via boto3 (needs compatible creds) |
| `query_client.py` | Boto3-based client mirroring `graph.py` query functions |
| `cli_query_client.py` | AWS CLI-based client (works with Workshop Studio creds) |
| `parity_check.py` | Compares Neptune vs NetworkX: summary, context, unresolved, orphans |
| `demo_compare.py` | Compares context retrieval for all 15 rules across demo permits |
| `cleanup.py` | Disables deletion protection and deletes the graph |

## Prerequisites

Set the environment variable `NEPTUNE_GRAPH_ID` to your graph identifier:

```bash
export NEPTUNE_GRAPH_ID=<your-graph-id>
```

Requires the AWS CLI configured with a profile that has Neptune Graph
permissions in `us-west-2`.

```bash
aws sts get-caller-identity --region us-west-2
```

## Usage

### Export

```bash
python scripts/neptune/export.py
# Produces out/neptune_export/nodes.cypher and edges.cypher
```

### Load

```bash
python scripts/neptune/cli_loader.py --graph-id "$NEPTUNE_GRAPH_ID"
# Loads nodes then edges. MERGE-based, so re-runs are idempotent.
```

### Verify parity

```bash
python scripts/neptune/parity_check.py --graph-id "$NEPTUNE_GRAPH_ID"
# Compares counts, queries, and timings between NetworkX and Neptune.
```

### Demo comparison

```bash
python scripts/neptune/demo_compare.py --graph-id "$NEPTUNE_GRAPH_ID"
# Compares context retrieval for all 15 rule citations.
```

### Cleanup

```bash
# Dry run (shows what would happen):
python scripts/neptune/cleanup.py --graph-id "$NEPTUNE_GRAPH_ID"

# Actual deletion (requires explicit --confirm):
python scripts/neptune/cleanup.py --graph-id "$NEPTUNE_GRAPH_ID" --confirm
```

Cleanup steps:
1. Disables deletion protection
2. Deletes the graph (no snapshot)

## Cost

- **Service:** Neptune Analytics
- **Capacity:** 16 m-NCU (minimum)
- **Rate:** $0.105/m-NCU-hr × 16 = **$1.68/hour**
- **Replicas:** 0
- **Budget target:** Keep total under $10 (~6 hours max)

## Graph design

| Property | Value |
|----------|-------|
| Region | us-west-2 |
| Provisioned memory | 16 m-NCU (minimum) |
| Replicas | 0 |
| Public connectivity | true |
| Deletion protection | true (must disable before delete) |
| Authentication | IAM SigV4 |
| Query language | openCypher |

## Data model

### Node types

| Label | Count | Key properties |
|-------|-------|---------------|
| Section | 2,102 | node_id, number, title, page, text |
| Exhibit | 40 | node_id, letter, title, page, text |
| Definition | 19 | node_id, term, defined_in |
| Rule | 15 | node_id, rule_id, description, parameter, operator, severity, verified, citation_section, citation_page |

### Relationship types

| Type | Count | Pattern |
|------|-------|---------|
| CONTAINS | 2,050 | Section → Section |
| USES_TERM | 606 | Section → Definition |
| REFERENCES | 158 | Section → Section/Exhibit |
| EXCEPTION | 79 | Section → Section |
| DEFINES | 19 | Section → Definition |
| CITES | 15 | Rule → Section/Exhibit |

### Totals

- **Nodes:** 2,176
- **Edges:** 2,927

## Compatibility note

The project's pinned boto3 (1.34.162) does not support the `login_session`
credential mechanism used by Workshop Studio. The `cli_loader.py` and
`cli_query_client.py` work around this by shelling out to the AWS CLI.
The `loader.py` and `query_client.py` use boto3 directly and require a
compatible credential chain (env vars, instance role, or updated SDK).
