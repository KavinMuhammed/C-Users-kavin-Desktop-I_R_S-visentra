"""Render review contact sheets (JPEG mosaics with file labels) from raw images. Read-only on raw data.

    python scripts/datasets/generate_contact_sheets.py --list <tiles.csv> --name <sheet-prefix> [--cols 4] [--tile 300] [--per-sheet 16]

<tiles.csv> columns: file_id (VT-DS-xxx:<path under raw>), caption (optional), row (optional; tiles with the same
row value are kept on one line, e.g. a candidate duplicate pair), box (optional "x y w h" in normalised YOLO
coordinates: the tile then shows a crop around that object with the box outlined, for label spot checks). Sheets are written to
datasets/interim/<PREP>/contact_sheets/<name>_NN.jpg with an index CSV listing which file is in which cell.
Contact sheets contain dataset imagery and are git-ignored; regenerate them from the raw data.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_common as pc  # noqa: E402


def raw_path(file_id: str) -> Path:
    ds, rel = file_id.split(":", 1)
    p = pc.RAW / ds / rel
    if ".." in Path(rel).parts:
        raise pc.PrepError(f"bad file id {file_id}")
    return p


def render(items: list[dict], out: Path, cols: int, tile: int) -> None:
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default()
    cap_h = 30
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile, rows * (tile + cap_h)), "white")
    d = ImageDraw.Draw(sheet)
    for k, it in enumerate(items):
        if it is None:
            continue
        x, y = (k % cols) * tile, (k // cols) * (tile + cap_h)
        try:
            with Image.open(raw_path(it["file_id"])) as im:
                im = im.convert("RGB")
                if it.get("box"):
                    bx, by, bw, bh = (float(v) for v in it["box"].split())
                    W, H = im.size
                    half = max(48, 1.0 * max(bw * W, bh * H))  # context of one box size around the object
                    cx, cy = bx * W, by * H
                    l, t = max(0, int(cx - half)), max(0, int(cy - half))
                    r_, b_ = min(W, int(cx + half)), min(H, int(cy + half))
                    ImageDraw.Draw(im).rectangle([cx - bw * W / 2, cy - bh * H / 2, cx + bw * W / 2, cy + bh * H / 2], outline="red", width=2)
                    im = im.crop((l, t, r_, b_))
                im.thumbnail((tile - 4, tile - 4))
                sheet.paste(im, (x + (tile - im.width) // 2, y + (tile - im.height) // 2))
        except OSError as e:
            d.text((x + 6, y + tile // 2), f"UNREADABLE {type(e).__name__}", fill="red", font=font)
        d.rectangle([x, y, x + tile - 1, y + tile - 1], outline="black")
        d.text((x + 3, y + tile + 2), it["label"][:48], fill="black", font=font)
        d.text((x + 3, y + tile + 15), it.get("caption", "")[:48], fill="blue", font=font)
    pc.guard(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)


def main() -> int:
    cfg = pc.load_config()
    lst = pc.arg("--list")
    name = pc.arg("--name")
    if not lst or not name:
        raise pc.PrepError("usage: --list <tiles.csv> --name <prefix> [--cols N] [--tile PX] [--per-sheet N]")
    cols, tile, per = int(pc.arg("--cols", 4)), int(pc.arg("--tile", 300)), int(pc.arg("--per-sheet", 16))
    tiles = pc.read_csv(pc.ROOT / lst if not Path(lst).is_absolute() else Path(lst))
    # lay out: keep tiles of one "row" value together on a line
    cells: list[dict | None] = []
    last_row = None
    size = {}
    for t in tiles:
        if t.get("row"):
            size[t["row"]] = size.get(t["row"], 0) + 1
    for t in tiles:
        r = t.get("row")
        if r and r != last_row and len(cells) % cols and cols - len(cells) % cols < size[r]:
            cells += [None] * (cols - len(cells) % cols)  # start a new line only if the group would not fit
        last_row = r
        cells.append(t)
    sdir = pc.out_dir("interim", cfg) / "contact_sheets"
    index = []
    for s in range(0, len(cells), per):
        chunk = cells[s:s + per]
        out = sdir / f"{name}_{s // per + 1:02d}.jpg"
        for k, it in enumerate(chunk):
            if it:
                it["label"] = f"[{k + 1}] {it['file_id'].split('/')[-1]}"
                index.append({"sheet": out.name, "cell": k + 1, "file_id": it["file_id"], "caption": it.get("caption", "")})
        render(chunk, out, cols, tile)
    pc.write_csv(sdir / f"{name}_index.csv", index, ["sheet", "cell", "file_id", "caption"])
    pc.log("contact_sheets", cfg, name=name, tiles=len(tiles), sheets=(len(cells) + per - 1) // per)
    print(f"{(len(cells) + per - 1) // per} sheet(s) for {len(tiles)} images -> {sdir.relative_to(pc.ROOT)}/{name}_*.jpg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
