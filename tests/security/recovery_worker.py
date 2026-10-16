"""Isolated child process for real OS-lock and SQLite crash boundaries."""

from datetime import datetime, timezone
from pathlib import Path
import os
import sys

from scoperelay.storage.fixture_loader import EPOCH
from scoperelay.storage.writer_owner import ApplicationWriter, WriterBusy, writer_owner
from scoperelay.trusted.resource import (
    ProtectedCommunicationResource,
    ResourceCallerContext,
)
import scoperelay.trusted.resource as resource_module


def main() -> None:
    mode = sys.argv[1]
    path = Path(sys.argv[2])
    if mode in ("hold_lifecycle", "try_lifecycle"):
        try:
            with ApplicationWriter.start(path):
                print("OWNED", flush=True)
                if mode == "hold_lifecycle":
                    sys.stdin.readline()
        except WriterBusy:
            print("BUSY", flush=True)
        return
    if mode in ("hold_owner", "try_owner"):
        try:
            with writer_owner(path):
                print("OWNED", flush=True)
                if mode == "hold_owner":
                    sys.stdin.readline()
        except WriterBusy:
            print("BUSY", flush=True)
        return
    stage = {
        "crash_before_begin": "before_begin",
        "crash_after_queue": "after_queue",
        "crash_after_commit_intent": "after_commit_intent",
        "crash_after_commit": "after_commit",
    }[mode]

    def crash(current: str) -> None:
        if current == stage:
            print(f"REACHED:{stage}", flush=True)
            os._exit(77)

    resource_module._checkpoint = crash
    with ApplicationWriter.start(path) as owner:
        ProtectedCommunicationResource(path, owner).admit_material_action(
            "action-01",
            "material-01",
            None,
            ResourceCallerContext("ep-01", EPOCH),
            at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        )
    raise AssertionError("fault stage not reached")


if __name__ == "__main__":
    main()
