"""Coordinate geometry for detection (AI_INFERENCE_ARCHITECTURE §5). Pure functions, unit-tested.

Frames: the *original* file (W0×H0) → the *oriented* image after EXIF orientation o (W×H; every
stored box is in this frame) → the *model input* (S_w×S_h, letterbox or stretch).
"""
from __future__ import annotations

ROTATED = {5, 6, 7, 8}   # EXIF orientations that swap width and height


def oriented_size(w0: int, h0: int, orientation: int = 1) -> tuple[int, int]:
    if orientation not in range(1, 9):
        raise ValueError("EXIF orientation must be 1..8")
    return (h0, w0) if orientation in ROTATED else (w0, h0)


def letterbox(w: int, h: int, sw: int, sh: int, mode: str = "letterbox") -> dict:
    if mode == "stretch":
        return {"mode": mode, "r_x": sw / w, "r_y": sh / h, "pad_x": 0.0, "pad_y": 0.0, "S_w": sw, "S_h": sh, "W": w, "H": h}
    r = min(sw / w, sh / h)
    return {"mode": "letterbox", "r_x": r, "r_y": r, "pad_x": (sw - round(w * r)) / 2, "pad_y": (sh - round(h * r)) / 2, "S_w": sw, "S_h": sh, "W": w, "H": h}


def to_input(box_xyxy, p: dict) -> list[float]:
    x1, y1, x2, y2 = box_xyxy
    return [x1 * p["r_x"] + p["pad_x"], y1 * p["r_y"] + p["pad_y"], x2 * p["r_x"] + p["pad_x"], y2 * p["r_y"] + p["pad_y"]]


def restore(box_input_xyxy, p: dict, min_px: float = 2.0) -> dict | None:
    """Input-pixel box → oriented-image box. Returns None for a degenerate (< min_px) box."""
    x1, y1, x2, y2 = box_input_xyxy
    raw = [(x1 - p["pad_x"]) / p["r_x"], (y1 - p["pad_y"]) / p["r_y"], (x2 - p["pad_x"]) / p["r_x"], (y2 - p["pad_y"]) / p["r_y"]]
    W, H = p["W"], p["H"]
    c = [min(max(raw[0], 0), W), min(max(raw[1], 0), H), min(max(raw[2], 0), W), min(max(raw[3], 0), H)]
    if c[2] - c[0] < min_px or c[3] - c[1] < min_px:
        return None
    return {"xyxy": c, "clipped": c != raw, "normalised": {"x": c[0] / W, "y": c[1] / H, "w": (c[2] - c[0]) / W, "h": (c[3] - c[1]) / H}}
