import json
from pathlib import Path

import pytest

from flick.observer import clean, relocalize

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_settings_root_keeps_menu_cells_drops_noise():
    snap = clean(load("settings.json"))
    labels = [e.label for e in snap.elements]
    # The legacy ax backend missed these entirely; axbridge + cleaner must keep them.
    assert "通用" in labels
    assert "辅助功能" in labels
    # Settings rows are tappable.
    for e in snap.elements:
        if e.label in {"通用", "辅助功能"}:
            assert "TAP" in e.operations
            assert e.uid == "com.apple.settings.general" or e.uid
    # Private chrome is gone.
    assert all("ScrollIndicator" not in e.role for e in snap.elements)
    assert all("Separator" not in e.role for e in snap.elements)
    assert all("HostingView" not in e.role for e in snap.elements)
    # Everything is on-screen (off-screen rows beyond y=874 are filtered).
    assert all(e.frame["y"] < 874 for e in snap.elements)
    # Big reduction from 132 raw nodes.
    assert len(snap.elements) < 40
    assert snap.raw_count == 132


def test_reminders_editor_exposes_text_fields_with_type_op():
    snap = clean(load("reminders_edit.json"))
    by_label = {e.label: e for e in snap.elements}
    assert "标题" in by_label
    assert "备注" in by_label
    assert "TYPE_TEXT" in by_label["标题"].operations
    assert "TYPE_TEXT" in by_label["备注"].operations


def test_popover_dimmsing_is_modal_not_an_action_target():
    # The editor fixture has no modal; a synthetic dimming node must surface as modal.
    raw = load("reminders_edit.json") + [
        {
            "type": "_UIPopoverDimmingView",
            "role": "_UIPopoverDimmingView",
            "AXLabel": "关闭弹出式窗口",
            "AXUniqueId": "PopoverDismissRegion",
            "frame": {"x": 0, "y": 0, "width": 402, "height": 874},
            "traits": None,
            "AXValue": None,
            "enabled": None,
        }
    ]
    snap = clean(raw)
    assert snap.modal is not None
    assert "弹出" in snap.modal["label"]
    assert all("PopoverDimming" not in e.role for e in snap.elements)


def test_safari_web_content_survives():
    snap = clean(load("safari.json"))
    labels = " ".join(e.label for e in snap.elements)
    assert "Learn more" in labels
    assert all("ScrollIndicator" not in e.role for e in snap.elements)


def test_fingerprint_stable_across_identical_trees():
    a = clean(load("settings.json"))
    b = clean(load("settings.json"))
    assert a.fingerprint == b.fingerprint


def test_relocalize_prefers_same_frame_on_duplicate_labels():
    snap = clean(load("settings.json"))
    target = next(e for e in snap.elements if e.label == "通用")
    again = relocalize(snap, "通用", target.frame)
    assert again is not None
    assert again.center == target.center


def test_springboard_icons_are_exposed(tmp_path=None):
    snap = clean(load("springboard.json"))
    roles = [e.role for e in snap.elements]
    assert "Icon" in roles
    icons = [e for e in snap.elements if e.role == "Icon"]
    # Real icons have non-zero frames and labels; the 0x0 placeholder is dropped.
    assert all(e.frame["width"] >= 4 and e.frame["height"] >= 4 for e in icons)
    assert all(e.label for e in icons)
    # Widget internals share the icon label but must not create duplicates.
    assert all("TouchPassThrough" not in e.role and "DebugView" not in e.role for e in snap.elements)
    assert len(icons) >= 10


@pytest.mark.parametrize("fixture", ["settings.json", "reminders_edit.json", "safari.json"])
def test_indices_are_contiguous_and_tappable(fixture):
    snap = clean(load(fixture))
    assert [e.index for e in snap.elements] == [str(i) for i in range(1, len(snap.elements) + 1)]
    assert all(e.center[0] in range(403) and e.center[1] in range(875) for e in snap.elements)
