"""One-time, offline conversion of NASA's Global Hawk model into VisionTra's shipped aircraft asset.

    <throwaway venv with DracoPy 2.2.0 + numpy>/python scripts/assets/convert_global_hawk.py "<path>/Global Hawk.glb" apps/web/vendor/models/global_hawk.glb

Source: https://github.com/nasa/NASA-3D-Resources/tree/master/3D%20Models/Global%20Hawk ("Global Hawk.glb", 1,184,092 bytes,
sha256 75b62cc2fa101fc517b8be4ebe79a7cc5206bcce38efbf104dde7414c810b667). NASA media: not subject to copyright in the
United States; credit NASA; no NASA endorsement implied. The NASA insignia (meatball and logotype) are protected marks: they
exist only in the source's textures, so this conversion drops every texture, image and texture coordinate.

What the output contains: one mesh, one primitive per source material (positions, normals, uint32 indices), plain
pbrMetallicRoughness base colours, the source node rotation baked in (Y up), uint16 indices where they fit. No Draco,
no extensions, no images.
DracoPy is not a project dependency; it is used only here, from a throwaway environment.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

import DracoPy
import numpy as np

SOURCE_SHA256 = "75b62cc2fa101fc517b8be4ebe79a7cc5206bcce38efbf104dde7414c810b667"


def read_glb(p: Path):
    b = p.read_bytes()
    _, _, length = struct.unpack_from("<4sII", b, 0)
    off, ch = 12, {}
    while off < length:
        n, t = struct.unpack_from("<I4s", b, off)
        ch[t] = b[off + 8: off + 8 + n]; off += 8 + n
    return json.loads(ch[b"JSON"]), ch[b"BIN\x00"]


def quat_matrix(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if hashlib.sha256(src.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise SystemExit("source checksum mismatch: not the reviewed NASA file")
    g, binb = read_glb(src)
    node = g["nodes"][0]
    R = quat_matrix(node.get("rotation", [0, 0, 0, 1]))
    parts = []
    for pr in g["meshes"][0]["primitives"]:
        ext = pr["extensions"]["KHR_draco_mesh_compression"]
        bv = g["bufferViews"][ext["bufferView"]]
        m = DracoPy.decode(binb[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]])
        pos = np.asarray(m.points, np.float32).reshape(-1, 3) @ R.T.astype(np.float32)
        nrm = np.asarray(m.normals, np.float32).reshape(-1, 3) @ R.T.astype(np.float32) if m.normals is not None and len(m.normals) else None
        idx = np.asarray(m.faces, np.uint32).reshape(-1)
        mat = g["materials"][pr.get("material", 0)]
        parts.append({"name": mat.get("name", ""), "pos": pos, "nrm": nrm, "idx": idx,
                      "base": (mat.get("pbrMetallicRoughness") or {}).get("baseColorFactor")})
    allp = np.concatenate([p["pos"] for p in parts])
    lo, hi = allp.min(0), allp.max(0)
    centre = np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2], np.float32)   # centred in x/z, resting on y = 0
    scale = np.float32(1.0 / (hi[2] - lo[2]))                                           # fuselage length = 1 unit
    # assemble an uncompressed glTF
    blob, views, accs, prims, mats = bytearray(), [], [], [], []

    def add(arr, target, comp, typ, minmax=False):
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": arr.nbytes, "target": target})
        blob.extend(arr.tobytes())
        a = {"bufferView": len(views) - 1, "componentType": comp, "count": int(arr.shape[0]), "type": typ}
        if minmax:
            a["min"] = arr.min(0).tolist(); a["max"] = arr.max(0).tolist()
        accs.append(a)
        return len(accs) - 1

    for i, p in enumerate(parts):
        pos = ((p["pos"] - centre) * scale).astype(np.float32)
        attrs = {"POSITION": add(pos, 34962, 5126, "VEC3", True)}
        if p["nrm"] is not None:
            n = p["nrm"] / np.maximum(np.linalg.norm(p["nrm"], axis=1, keepdims=True), 1e-12)
            attrs["NORMAL"] = add(n.astype(np.float32), 34962, 5126, "VEC3")
        small = len(pos) <= 65535                       # every part fits 16-bit indices (lossless, about 220 KB smaller)
        ind = add(p["idx"].astype(np.uint16 if small else np.uint32).reshape(-1, 1), 34963, 5123 if small else 5125, "SCALAR")
        accs[ind]["type"] = "SCALAR"
        mats.append({"name": p["name"], "pbrMetallicRoughness": {"baseColorFactor": p["base"] or [0.8, 0.8, 0.8, 1], "metallicFactor": 0.0, "roughnessFactor": 0.6},
                     "doubleSided": True})
        prims.append({"attributes": attrs, "indices": ind, "material": i, "mode": 4})
    gltf = {"asset": {"version": "2.0", "generator": "VisionTra convert_global_hawk.py (DracoPy 2.2.0)",
                      "copyright": "NASA (Global Hawk, NASA 3D Resources). Not subject to copyright in the US. No NASA endorsement implied."},
            "extras": {"source": "https://github.com/nasa/NASA-3D-Resources/tree/master/3D%20Models/Global%20Hawk", "source_sha256": SOURCE_SHA256,
                       "units": "fuselage length = 1", "up": "+Y", "removed": "all textures, images and UVs (contained NASA insignia); Draco compression"},
            "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"name": "Global Hawk (NASA)", "mesh": 0}],
            "meshes": [{"name": "Global Hawk", "primitives": prims}], "materials": mats,
            "accessors": accs, "bufferViews": views, "buffers": [{"byteLength": len(blob)}]}
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\0" * (-len(blob) % 4)
    out = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(blob)) + struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    report = {"output": str(dst), "bytes": len(out), "sha256": hashlib.sha256(out).hexdigest(), "triangles": int(sum(len(p["idx"]) // 3 for p in parts)),
              "vertices": int(sum(len(p["pos"]) for p in parts)), "parts": [(p["name"], len(p["idx"]) // 3, p["base"]) for p in parts],
              "source_extent_after_rotation": (hi - lo).round(2).tolist(), "normals": all(p["nrm"] is not None for p in parts)}
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
