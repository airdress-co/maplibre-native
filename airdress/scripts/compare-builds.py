#!/usr/bin/env python3
"""Compares two independent builds of one release.

    airdress/scripts/compare-builds.py android DIR_A DIR_B
    airdress/scripts/compare-builds.py ios     DIR_A DIR_B

Each DIR holds the platform's one zip asset. What is compared:

- android: every file inside every AAR, JAR and the Maven repository by
  content, never the zips' own bytes or timestamps. A native library
  (.so) is compared byte for byte.
- ios: every file in the xcframework by content. A Mach-O binary is
  compared with its LC_UUID and its code signature blanked, the two
  fields a linker fills differently on every run.

A path that differs fails the comparison unless it matches a glob in
airdress/build-differences.txt, where every allowed difference is listed by
name with its cause. That list only shrinks.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_FILE = ROOT / "airdress" / "build-differences.txt"

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
LC_UUID = 0x1B
LC_CODE_SIGNATURE = 0x1D


def allowed_patterns() -> list[str]:
    if not ALLOWED_FILE.exists():
        return []
    return [line.strip() for line in ALLOWED_FILE.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def normalise_macho(data: bytes) -> bytes:
    """A Mach-O (thin or fat) with LC_UUID and the code signature zeroed."""
    if len(data) < 8:
        return data
    magic_be = struct.unpack(">I", data[:4])[0]
    if magic_be in (FAT_MAGIC, FAT_MAGIC_64):
        out = bytearray(data)
        n = struct.unpack(">I", data[4:8])[0]
        entry = 20 if magic_be == FAT_MAGIC else 32
        for i in range(n):
            off = 8 + i * entry
            if magic_be == FAT_MAGIC:
                _, _, offset, size, _ = struct.unpack(">iiIII", data[off:off + 20])
            else:
                _, _, offset, size, _, _ = struct.unpack(">iiQQII", data[off:off + 32])
            out[offset:offset + size] = normalise_macho(bytes(data[offset:offset + size]))
        return bytes(out)
    if struct.unpack("<I", data[:4])[0] != MH_MAGIC_64:
        return data
    out = bytearray(data)
    ncmds = struct.unpack("<I", data[16:20])[0]
    off = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack("<II", data[off:off + 8])
        if cmd == LC_UUID:
            out[off + 8:off + 24] = b"\0" * 16
        elif cmd == LC_CODE_SIGNATURE:
            dataoff, datasize = struct.unpack("<II", data[off + 8:off + 16])
            out[dataoff:dataoff + datasize] = b"\0" * datasize
        off += size
    return bytes(out)


def is_macho(data: bytes) -> bool:
    if len(data) < 4:
        return False
    return struct.unpack("<I", data[:4])[0] == MH_MAGIC_64 or struct.unpack(">I", data[:4])[0] in (
        FAT_MAGIC, FAT_MAGIC_64)


def digests(platform: str, zip_bytes: bytes, prefix: str = "") -> dict[str, str]:
    """path -> digest of normalised content, descending into nested archives."""
    result: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            data = z.read(info)
            path = prefix + info.filename
            if path.endswith((".aar", ".jar", ".zip")):
                try:
                    result.update(digests(platform, data, path + "!/"))
                    continue
                except zipfile.BadZipFile:
                    pass
            if platform == "ios" and is_macho(data):
                data = normalise_macho(data)
            result[path] = hashlib.sha256(data).hexdigest()
    return result


def one_zip(d: Path) -> Path:
    zips = sorted(d.glob("*.zip"))
    if len(zips) != 1:
        raise SystemExit(f"{d} holds {len(zips)} zips, not one")
    return zips[0]


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] not in ("android", "ios"):
        print(__doc__, file=sys.stderr)
        return 2
    platform, a, b = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    za, zb = one_zip(a), one_zip(b)
    if za.name != zb.name:
        print(f"the two builds name their asset differently: {za.name}, {zb.name}", file=sys.stderr)
        return 1
    da, db = digests(platform, za.read_bytes()), digests(platform, zb.read_bytes())
    patterns = allowed_patterns()
    differing = sorted(p for p in set(da) | set(db) if da.get(p) != db.get(p))
    failed = []
    for p in differing:
        allowed = any(fnmatch.fnmatchcase(p, pat) for pat in patterns)
        state = "missing in one" if p not in da or p not in db else "differs"
        print(f"{'allowed' if allowed else 'DIFFERS'}: {p} ({state})")
        if not allowed:
            failed.append(p)
    print(f"{platform}: {len(da)} files compared, {len(differing)} differ, {len(failed)} not allowed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
