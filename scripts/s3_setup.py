"""Create the permit bucket if absent and apply the baseline hardening."""
import sys

import _bootstrap  # noqa: F401

from septic import config
from septic.harvest.s3sink import S3Sink


def main() -> int:
    session = config.session()
    identity = session.client("sts").get_caller_identity()
    print(f"identity {identity['Arn']}")

    sink = S3Sink(client=session.client("s3"))
    for note in sink.ensure_bucket():
        print(note)

    objects = sink.inventory()
    print(f"objects present: {len(objects)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
