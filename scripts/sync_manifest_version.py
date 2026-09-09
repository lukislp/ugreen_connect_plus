#!/usr/bin/env python3
"""Sync the manifest's version field to the version being released.

Usage: sync_manifest_version.py <version>

Called from semantic-release's prepare step, so the version Home Assistant
shows on the integration's page never drifts from the tag it was installed
from. HACS reads one and the release names the other, and a mismatch is only
ever noticed by someone trying to work out which build they are running.
"""
import json
import re
import sys

MANIFEST_PATH = "custom_components/ugreen_connect_plus/manifest.json"


def main() -> None:
    version = sys.argv[1]
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        original = f.read()

    # Confirm "version" really is a top-level key before touching anything.
    json.loads(original)["version"]

    # Substituted rather than re-serialised, so the file keeps its own
    # formatting instead of being rewritten wholesale on every release.
    updated, count = re.subn(
        r'("version"\s*:\s*)"[^"]*"', rf'\g<1>"{version}"', original, count=1
    )
    if count != 1:
        raise SystemExit(f'no "version" field to update in {MANIFEST_PATH}')

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        f.write(updated)


if __name__ == "__main__":
    main()
