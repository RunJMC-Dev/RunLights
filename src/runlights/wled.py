from __future__ import annotations

import dataclasses
import json
from typing import Optional, Tuple

import requests


@dataclasses.dataclass
class WLEDController:
    host: str
    port: int = 80


@dataclasses.dataclass
class WLEDPayload:
    on: Optional[bool] = None
    brightness: Optional[int] = None  # 0-255
    color: Optional[Tuple[int, int, int]] = None  # RGB tuple
    segment: Optional[int] = None
    transition_ms: Optional[int] = None


class WLEDError(Exception):
    pass


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) not in (6, 3):
        raise ValueError(f"Invalid hex color: {hex_color}")
    if len(hex_color) == 3:
        hex_color = "".join(ch * 2 for ch in hex_color)
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return r, g, b


def build_state_payload(payload: WLEDPayload) -> dict:
    body: dict = {}
    if payload.transition_ms is not None:
        body["tt"] = int(payload.transition_ms)
    if payload.segment is None:
        # Whole strip
        if payload.on is not None:
            body["on"] = payload.on
        if payload.brightness is not None:
            body["bri"] = max(0, min(255, int(payload.brightness)))
        if payload.color is not None:
            body["seg"] = [{"id": 0, "col": [list(payload.color), [0, 0, 0], [0, 0, 0]]}]
    else:
        seg = {"id": int(payload.segment)}
        if payload.on is not None:
            seg["on"] = payload.on
        if payload.brightness is not None:
            seg["bri"] = max(0, min(255, int(payload.brightness)))
        if payload.color is not None:
            seg["col"] = [list(payload.color), [0, 0, 0], [0, 0, 0]]
        body["seg"] = [seg]
    return body


def send_state(controller: WLEDController, payload: WLEDPayload, timeout: float = 2.0) -> dict:
    url = f"http://{controller.host}:{controller.port}/json/state"
    body = build_state_payload(payload)
    try:
        resp = requests.post(url, data=json.dumps(body), timeout=timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else {}
    except Exception as exc:
        raise WLEDError(f"WLED request failed: {exc}") from exc


def send_simple(
    host: str,
    port: int = 80,
    on: Optional[bool] = None,
    brightness: Optional[int] = None,
    color: Optional[str] = None,
    segment: Optional[int] = None,
    transition_ms: Optional[int] = None,
    timeout: float = 2.0,
) -> dict:
    """Convenience helper to send a basic state update."""
    rgb = _hex_to_rgb(color) if color else None
    ctrl = WLEDController(host=host, port=port)
    payload = WLEDPayload(on=on, brightness=brightness, color=rgb, segment=segment, transition_ms=transition_ms)
    return send_state(ctrl, payload, timeout=timeout)


def apply_fullfade(
    host: str,
    port: int,
    color_hex: str,
    health_pct: float,
    transition_ms: Optional[int] = None,
    timeout: float = 2.0,
) -> dict:
    """
    Fade the entire strip based on health percentage.

    - health_pct is clamped to 0-100.
    - brightness scales linearly 0-255 from health percentage.
    - color is applied as the primary color; no segments specified (whole strip).
    """
    pct = max(0.0, min(100.0, float(health_pct)))
    bri = int(255 * (pct / 100.0))
    return send_simple(
        host=host,
        port=port,
        on=True,
        brightness=bri,
        color=color_hex,
        segment=None,
        transition_ms=transition_ms,
        timeout=timeout,
    )
