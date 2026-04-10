#!/usr/bin/env python3
"""
Local test script for masters-tournament plugin changes.

Run from the plugin directory:
    cd plugins/masters-tournament
    python test_local.py

Tests three things:
  1. _masters_thursday() date logic (no dependencies)
  2. get_detailed_phase() for various dates (no dependencies)
  3. Hole card visual rendering → saves PNG files you can open and inspect
     (requires: pillow)

Does NOT require a Pi, LED matrix, or network connection.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── path setup ─────────────────────────────────────────────────────────────────
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

# Stub out everything that would import rpi/ledmatrix core libs
from unittest.mock import MagicMock, patch

# Stub src.plugin_system so helpers can be imported standalone
import types
src_mod = types.ModuleType("src")
ps_mod  = types.ModuleType("src.plugin_system")
bp_mod  = types.ModuleType("src.plugin_system.base_plugin")

class _BasePlugin:
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        self.plugin_id = plugin_id
        self.config    = config
        self.enabled   = config.get("enabled", True)
        self.logger    = MagicMock()
    def on_config_change(self, new_config):
        self.config = new_config or {}
    def cleanup(self): pass
    def get_info(self): return {}

class _VegasDisplayMode:
    SCROLL = "scroll"

bp_mod.BasePlugin       = _BasePlugin
bp_mod.VegasDisplayMode = _VegasDisplayMode
ps_mod.base_plugin      = bp_mod
src_mod.plugin_system   = ps_mod
sys.modules["src"]                        = src_mod
sys.modules["src.plugin_system"]          = ps_mod
sys.modules["src.plugin_system.base_plugin"] = bp_mod

# ─────────────────────────────────────────────────────────────────────────────

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures = []

def check(label, got, expected):
    ok = got == expected
    status = PASS if ok else FAIL
    print(f"  {status}  {label}")
    if not ok:
        print(f"        got      : {got!r}")
        print(f"        expected : {expected!r}")
        _failures.append(label)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. _masters_thursday date logic
# ═══════════════════════════════════════════════════════════════════════════════
print("\n-- 1. _masters_thursday() ---------------------------------------------")
from masters_helpers import _masters_thursday

KNOWN = {
    2022: (4,  7),
    2023: (4,  6),
    2024: (4, 11),
    2025: (4, 10),
    2026: (4,  9),
    2027: (4,  8),
    2028: (4,  6),
}
for year, (month, day) in KNOWN.items():
    thu = _masters_thursday(year)
    check(f"{year} -> April {day}", (thu.month, thu.day), (month, day))

# ═══════════════════════════════════════════════════════════════════════════════
# 2. get_detailed_phase() — fallback path (no ESPN dates supplied)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n-- 2. get_detailed_phase() fallback -----------------------------------")
from masters_helpers import get_detailed_phase

_EDT = timezone(timedelta(hours=-4))

def edt(year, month, day, hour=12):
    return datetime(year, month, day, hour, 0, 0, tzinfo=_EDT)

# 2026 Masters: Thu Apr 9 – Sun Apr 12
check("2026 Apr 8 (Wed practice)",   get_detailed_phase(edt(2026, 4, 8)),  "practice")
check("2026 Apr 9 10am (live)",      get_detailed_phase(edt(2026, 4, 9, 10)), "tournament-live")
check("2026 Apr 9 6am (morning)",    get_detailed_phase(edt(2026, 4, 9,  6)), "tournament-morning")
check("2026 Apr 9 20h (evening)",    get_detailed_phase(edt(2026, 4, 9, 20)), "tournament-evening")
check("2026 Apr 12 10am (live)",     get_detailed_phase(edt(2026, 4, 12, 10)), "tournament-live")
check("2026 Apr 13 noon (post)",     get_detailed_phase(edt(2026, 4, 13, 12)), "post-tournament")
check("2026 Apr 1 (pre-tournament)", get_detailed_phase(edt(2026, 4,  1)), "pre-tournament")
check("2026 Jan 1 (off-season)",     get_detailed_phase(edt(2026, 1,  1)), "off-season")

# 2023 Masters: Thu Apr 6 – Sun Apr 9 (was broken with old hardcoded Apr 10-13)
check("2023 Apr 6 10am (live)",      get_detailed_phase(edt(2023, 4, 6, 10)), "tournament-live")
check("2023 Apr 5 (practice)",       get_detailed_phase(edt(2023, 4, 5)),  "practice")
check("2023 Apr 4 (practice)",       get_detailed_phase(edt(2023, 4, 4)),  "practice")

# 2024 Masters: Thu Apr 11 – Sun Apr 14
check("2024 Apr 11 10am (live)",     get_detailed_phase(edt(2024, 4, 11, 10)), "tournament-live")
check("2024 Apr 4 (pre-tournament)", get_detailed_phase(edt(2024, 4, 4)),  "pre-tournament")

# ═══════════════════════════════════════════════════════════════════════════════
# 3. _build_enabled_modes() fun_facts config filtering
# ═══════════════════════════════════════════════════════════════════════════════
print("\n-- 3. fun_facts config filtering --------------------------------------")

# Minimal stubs for MastersTournamentPlugin init
import logging
logging.disable(logging.CRITICAL)   # silence all logging during test

# Stub heavy imports that plugin __init__ pulls in
for mod_name in ["masters_data", "masters_renderer", "masters_renderer_enhanced",
                 "logo_loader", "requests"]:
    sys.modules.setdefault(mod_name, MagicMock())

from manager import MastersTournamentPlugin

def _make_plugin(config):
    dm = MagicMock()
    dm.matrix.width  = 64
    dm.matrix.height = 32
    cm = MagicMock()
    cm.get = MagicMock(return_value=None)
    cm.set = MagicMock()
    pm = MagicMock()
    p = MastersTournamentPlugin.__new__(MastersTournamentPlugin)
    p.plugin_id        = "masters-tournament"
    p.config           = config
    p.enabled          = True
    p.logger           = MagicMock()
    p.display_width    = 64
    p.display_height   = 32
    p._tournament_meta = None
    return p

def modes_include_fun_facts(config):
    p = _make_plugin(config)
    # Force off-season phase so fun_facts is always a candidate mode;
    # this test is about config-filtering logic, not phase selection.
    import manager as _manager_mod
    with patch.object(_manager_mod, "get_detailed_phase", return_value="off-season"):
        modes = p._build_enabled_modes()
    return "masters_fun_facts" in modes

check("no display_modes config -> fun_facts enabled (default)",
      modes_include_fun_facts({}), True)

check("display_modes.fun_facts={'enabled': true} -> enabled",
      modes_include_fun_facts({"display_modes": {"fun_facts": {"enabled": True}}}), True)

check("display_modes.fun_facts={'enabled': false} -> disabled",
      modes_include_fun_facts({"display_modes": {"fun_facts": {"enabled": False}}}), False)

check("display_modes.fun_facts=True (bare bool) -> enabled",
      modes_include_fun_facts({"display_modes": {"fun_facts": True}}), True)

check("display_modes.fun_facts=False (bare bool) -> disabled",
      modes_include_fun_facts({"display_modes": {"fun_facts": False}}), False)

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Hole card visual rendering → PNG files
# ═══════════════════════════════════════════════════════════════════════════════
print("\n-- 4. Hole card visual rendering --------------------------------------")

try:
    from PIL import Image
    from masters_helpers import get_hole_info

    # Minimal logo_loader stub that returns no images (pure text layout)
    class _FakeLoader:
        def get_hole_image(self, *a, **kw): return None
        def get_player_headshot(self, *a, **kw): return None
        def get_green_jacket_icon(self, *a, **kw): return None
        def clear_cache(self): pass

    # Import renderers after stubs are in place
    from masters_renderer import MastersRenderer
    from masters_renderer_enhanced import MastersRendererEnhanced

    OUT_DIR = HERE / "test_renders"
    OUT_DIR.mkdir(exist_ok=True)

    SIZES = [
        ("64x32_small",   64,  32, MastersRenderer),
        ("128x64_large", 128,  64, MastersRendererEnhanced),
        ("192x48_wide",  192,  48, MastersRendererEnhanced),
    ]
    TEST_HOLES = [1, 12, 13, 18]   # regular + Amen Corner + final

    for size_name, w, h, RendererCls in SIZES:
        loader = _FakeLoader()
        r = RendererCls(w, h, {}, loader, MagicMock())
        for hole in TEST_HOLES:
            img = r.render_hole_card(hole)
            if img:
                # Scale up 4× so it's easy to view on screen
                scaled = img.resize((w * 4, h * 4), Image.NEAREST)
                fname = OUT_DIR / f"hole_{hole:02d}_{size_name}.png"
                scaled.save(fname)
                print(f"  {PASS}  saved {fname.relative_to(HERE)}")
            else:
                print(f"  {FAIL}  render_hole_card({hole}) returned None on {size_name}")
                _failures.append(f"render hole {hole} on {size_name}")

except Exception as e:
    print(f"  {FAIL}  rendering crashed: {e}")
    import traceback; traceback.print_exc()
    _failures.append("rendering")

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print()
if _failures:
    print(f"{'-'*60}")
    print(f"FAILED ({len(_failures)}): {', '.join(_failures)}")
    sys.exit(1)
else:
    print(f"{'-'*60}")
    print("All checks passed.")
    print("\nOpen plugins/masters-tournament/test_renders/*.png to inspect the hole card layout.")
