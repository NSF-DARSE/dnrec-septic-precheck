"""Cleanup script for Neptune Analytics graph.

Usage:
    python scripts/neptune/cleanup.py [--graph-id ID] [--confirm]

Deletion protection must be disabled before the graph can be deleted.
This script handles both steps.

Without --confirm, it shows what would be done without acting.
"""
from __future__ import annotations

import sys
import time

import boto3
from botocore.config import Config


import os

GRAPH_ID_DEFAULT = os.environ.get("NEPTUNE_GRAPH_ID", "")
REGION = os.environ.get("AWS_REGION", "us-west-2")


def get_client(region: str = REGION):
    config = Config(region_name=region, retries={"max_attempts": 3, "mode": "adaptive"})
    session = boto3.Session(region_name=region)
    return session.client("neptune-graph", config=config)


def cleanup(graph_id: str, confirm: bool = False, region: str = REGION):
    """Disable deletion protection and delete the graph."""
    client = get_client(region)

    # Check current state
    try:
        graph = client.get_graph(graphIdentifier=graph_id)
    except Exception as e:
        print(f"Graph {graph_id} not found or inaccessible: {e}")
        return

    status = graph.get("status")
    deletion_protection = graph.get("deletionProtection", False)

    print(f"Graph: {graph_id}")
    print(f"Name: {graph.get('name')}")
    print(f"Status: {status}")
    print(f"Deletion protection: {deletion_protection}")
    print(f"Created: {graph.get('createTime')}")
    print()

    if status != "AVAILABLE":
        print(f"Graph is not AVAILABLE (status={status}). Cannot delete.")
        return

    if not confirm:
        print("DRY RUN — what would happen:")
        if deletion_protection:
            print("  1. Disable deletion protection")
        print(f"  2. Delete graph {graph_id} (skip snapshot)")
        print()
        print("To execute, run with --confirm")
        return

    # Step 1: Disable deletion protection if enabled
    if deletion_protection:
        print("Disabling deletion protection...")
        client.update_graph(
            graphIdentifier=graph_id,
            deletionProtection=False,
        )
        # Wait a moment for the update to propagate
        time.sleep(3)
        print("  Done.")

    # Step 2: Delete the graph
    print(f"Deleting graph {graph_id} (no snapshot)...")
    client.delete_graph(
        graphIdentifier=graph_id,
        skipSnapshot=True,
    )
    print("  Delete initiated. Graph will be removed shortly.")
    print()
    print("Verify with:")
    print(f"  aws neptune-graph list-graphs --region {region}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cleanup Neptune Analytics graph")
    parser.add_argument("--graph-id", default=GRAPH_ID_DEFAULT)
    parser.add_argument("--confirm", action="store_true",
                        help="Actually delete (without this flag, dry-run only)")
    parser.add_argument("--region", default=REGION)
    args = parser.parse_args()

    cleanup(args.graph_id, confirm=args.confirm, region=args.region)


if __name__ == "__main__":
    main()
