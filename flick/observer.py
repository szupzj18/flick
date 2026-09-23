"""Turns a raw idb accessibility tree into a small, decision-ready element table.

The raw axbridge tree is noisy: 100/132 nodes without labels on SwiftUI pages,
private view classes, duplicate shadow nodes for every cell, off-screen nodes,
scroll indicators. This module keeps what an action selector needs and nothing
else. Rules here are empirical, derived from fixtures captured on iOS 26
(Settings, Reminders editor, Safari).
"""

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field

from .device import IdbDevice, TransientTreeError

# Private implementation views that never carry a user intent themselves.
TYPE_BLACKLIST_SUBSTRINGS = (
    "ScrollIndicator",
    "Separator",
    "Decoration",
    "CellHostingView",
    "CellContentView",
    "InheritedView",
    "SystemBackground",
    "PopoverDimmingView",
    "TransitionView",
    "Background",
    "TouchPassThroughView",
    "DebugView",
    "IconImageView",
    "IconLayerView",
    "Legibility",
)

# Roles a human can act on / read state from.
INTERACTIVE_TYPES = {
    "Button",
    "Cell",
    "Link",
    "Icon",
    "Switch",
    "CheckBox",
    "Radio",
    "Tab",
    "MenuItem",
    "MenuItemRadio",
    "ComboBox",
    "TextInput",
    "TextField",
    "TextView",
    "SearchField",
    "SearchBox",
    "SpinButton",
    "Slider",
    "Adjustment",
    "PageIndicator",
    "SegmentedControl",
    "SegmentedControlButton",
}

TRAIT_INTERACTIVE = {
    "Button",
    "Link",
    "SearchField",
    "Checkbox",
    "Switch",
    "Radio",
    "KeyboardKey",
    "PlaysSound",
    "StartsMediaSession",
    "Adjustable",
}

TRAIT_TEXT = {"StaticText", "Header", "SummaryElement"}

LABEL_FALLBACK_KEYS = ("AXLabel", "title", "placeholder", "help", "role_description")

OFFSCREEN_TOLERANCE = 8  # points; half-clipped rows at the edge still count


@dataclass
class Element:
    index: str
    role: str
    label: str
    value: str
    frame: dict
    center: tuple[int, int]
    traits: list[str] = field(default_factory=list)
    uid: str | None = None
    enabled: bool = True
    operations: list[str] = field(default_factory=list)

    def to_choice(self) -> dict:
        return {
            "index": self.index,
            "role": self.role,
            "label": self.label[:120],
            "value": self.value[:120],
            "operations": self.operations,
        }


@dataclass
class Snapshot:
    elements: list[Element]
    page_text: str
    fingerprint: str
    front_pid: int | None
    front_app: str | None
    modal: dict | None
    screen: dict
    raw_count: int

    def as_state(self) -> dict:
        return {
            "front_app": self.front_app,
            "elements": [e.to_choice() for e in self.elements],
            "page_text": self.page_text[:6000],
        }


def _label(node: dict) -> str:
    for key in LABEL_FALLBACK_KEYS:
        v = node.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _is_blacklisted(node_type: str | None) -> bool:
    if not node_type:
        return True
    return any(bad in node_type for bad in TYPE_BLACKLIST_SUBSTRINGS)


def _on_screen(f: dict, screen: dict, tol: int = OFFSCREEN_TOLERANCE) -> bool:
    return (
        f["x"] + f["width"] > -tol
        and f["y"] + f["height"] > -tol
        and f["x"] < screen["width"] + tol
        and f["y"] < screen["height"] + tol
        and f["width"] >= 4
        and f["height"] >= 4
    )


def _center(f: dict) -> tuple[int, int]:
    return round(f["x"] + f["width"] / 2), round(f["y"] + f["height"] / 2)


def _operations(role: str, traits: list[str]) -> list[str]:
    ops = ["TAP"]
    if role in {"TextInput", "TextField", "TextView", "SearchField", "SearchBox", "ComboBox"} or "SearchField" in traits:
        ops.append("TYPE_TEXT")
    if "Adjustable" in traits or role in {"Slider", "Adjustment"}:
        ops.append("ADJUST")
    return ops


def _find_modal(nodes: list[dict]) -> dict | None:
    """Alert/Sheet containers, plus the dimming-view 'close popover' affordance."""
    for n in nodes:
        role = n.get("role") or ""
        ntype = n.get("type") or ""
        if role.endswith("Alert") or ntype in {"Alert", "Sheet", "ActionSheet"}:
            return {"label": _label(n), "frame": n.get("frame")}
    for n in nodes:
        if "PopoverDimmingView" in (n.get("type") or ""):
            return {"label": _label(n) or "dismiss_popover", "frame": n.get("frame")}
    return None


def clean(raw: list[dict], screen: dict | None = None) -> Snapshot:
    screen = screen or {"width": 402, "height": 874}

    # Frontmost app: Application nodes carry pid; the tree is dominated by one pid.
    pids: dict[int, int] = {}
    app_name = None
    for n in raw:
        pid = n.get("pid")
        if isinstance(pid, int):
            pids[pid] = pids.get(pid, 0) + 1
        if (n.get("role") == "AXApplication" or n.get("type") == "Application") and _label(n):
            app_name = _label(n)
    front_pid = max(pids, key=pids.get) if pids else None

    modal = _find_modal(raw)

    candidates: list[dict] = []
    text_nodes: list[dict] = []
    for n in raw:
        ntype = n.get("type")
        traits = n.get("traits") or []
        label = _label(n)
        frame = n.get("frame")
        if not frame:
            continue

        interactive = ntype in INTERACTIVE_TYPES or bool(TRAIT_INTERACTIVE & set(traits))
        is_text = ntype == "StaticText" or bool(TRAIT_TEXT & set(traits))

        if is_text and not interactive:
            if label and _on_screen(frame, screen):
                text_nodes.append(n)
            continue

        if not interactive or _is_blacklisted(ntype):
            continue
        # Unlabelled interactive controls are kept only with a stable identity;
        # they are usually container cells whose tappable child is also listed.
        if not label and not n.get("AXUniqueId"):
            continue
        if not _on_screen(frame, screen):
            continue
        candidates.append(n)

    # De-duplicate: a cell/button and its shadow StaticText/Image share a frame;
    # collapse entries with identical label+frame to the most interactive node.
    def rank(n: dict) -> int:
        t = n.get("type") or ""
        if t in {"Button", "Cell", "Link", "Switch", "Icon"}:
            return 3
        if t in INTERACTIVE_TYPES:
            return 2
        return 1

    candidates.sort(key=rank, reverse=True)
    seen: dict[tuple, dict] = {}
    for n in candidates:
        f = n["frame"]
        key = (
            _label(n),
            round(f["x"] / 4),
            round(f["y"] / 4),
            round(f["width"] / 4),
            round(f["height"] / 4),
        )
        if key not in seen:
            seen[key] = n

    deduped = sorted(seen.values(), key=lambda n: (n["frame"]["y"], n["frame"]["x"]))

    elements: list[Element] = []
    for i, n in enumerate(deduped, start=1):
        f = n["frame"]
        traits = n.get("traits") or []
        label = _label(n) or f"unlabelled_{n.get('type')}"
        elements.append(
            Element(
                index=str(i),
                role=n.get("type") or "Unknown",
                label=label,
                value=str(n.get("AXValue") or ""),
                frame={k: round(v, 1) for k, v in f.items()},
                center=_center(f),
                traits=[t for t in traits if t != "None"],
                uid=n.get("AXUniqueId"),
                enabled=bool(n.get("enabled", True)),
                operations=_operations(n.get("type") or "", traits),
            )
        )

    text_nodes.sort(key=lambda n: (n["frame"]["y"], n["frame"]["x"]))
    page_text = "\n".join(dict.fromkeys(_label(n) for n in text_nodes if _label(n)))

    basis = [
        {
            "r": e.role,
            "l": e.label,
            "v": e.value,
            "f": e.frame,
            "t": sorted(e.traits),
        }
        for e in elements
    ]
    fingerprint = hashlib.sha256(
        json.dumps({"pid": front_pid, "modal": modal, "elements": basis}, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:16]

    return Snapshot(
        elements=elements,
        page_text=page_text,
        fingerprint=fingerprint,
        front_pid=front_pid,
        front_app=app_name,
        modal=modal,
        screen=screen,
        raw_count=len(raw),
    )


def observe_stable(
    device: IdbDevice,
    *,
    stable_reads: int = 2,
    timeout_s: float = 12.0,
    settle_delay: float = 0.25,
) -> Snapshot:
    """Read until the tree stops moving.

    During app launches the element count changes for ~2.5 s and the axbridge
    guest can fail outright. We retry transient errors and require N identical
    fingerprints in a row, mirroring the browser agent's two-frame settle.
    """
    deadline = time.monotonic() + timeout_s
    last_fp = None
    stable = 0
    last_err = None
    while time.monotonic() < deadline:
        try:
            raw = device.describe_all_raw()
        except TransientTreeError as e:
            last_err = e
            time.sleep(settle_delay)
            continue
        snap = clean(raw)
        if snap.fingerprint == last_fp:
            stable += 1
            if stable >= stable_reads:
                return snap
        else:
            stable = 1
            last_fp = snap.fingerprint
        time.sleep(settle_delay)
    if last_err is not None and last_fp is None:
        raise last_err
    # Return the last reading even if stability was marginal.
    return snap


def relocalize(snap: Snapshot, label: str, frame: dict) -> Element | None:
    """Find the current-frame twin of a previously observed target.

    Identity is a weak fingerprint here (no persistent node handles like DOM):
    exact label match wins, then largest frame overlap among same-label nodes.
    """
    same = [e for e in snap.elements if e.label == label]
    if not same:
        same = [e for e in snap.elements if label in e.label or e.label in label]
    if not same:
        return None
    if len(same) == 1:
        return same[0]

    def overlap(a: dict, b: dict) -> float:
        ax2, ay2 = a["x"] + a["width"], a["y"] + a["height"]
        bx2, by2 = b["x"] + b["width"], b["y"] + b["height"]
        w = max(0.0, min(ax2, bx2) - max(a["x"], b["x"]))
        h = max(0.0, min(ay2, by2) - max(a["y"], b["y"]))
        return w * h

    return max(same, key=lambda e: overlap(e.frame, frame))


def snapshot_to_json(snap: Snapshot) -> dict:
    d = asdict(snap)
    return d
