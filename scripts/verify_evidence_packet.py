"""Offline payload checksum check; not a signature or evidence-validity check.

Usage: python scripts/verify_evidence_packet.py path/to/evidence.json
Exit 0: checksum matches; 1: mismatch; 2: malformed/unsupported packet.
"""
import hashlib
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    try:
        packet = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        payload = packet["payload"]
        if payload["schema"] != "visiontra.evidence-handoff/1" or packet["integrity"]["algorithm"] != "sha256":
            raise ValueError("Unsupported packet schema or checksum algorithm")
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        actual = hashlib.sha256(data).hexdigest()
        if actual != packet["integrity"]["sha256"]:
            print("MISMATCH: payload differs from the recorded checksum.")
            return 1
        print(f"MATCH: {actual}\nChecksum only; no claim of authorship, evidence truth or airworthiness.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"INVALID: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
