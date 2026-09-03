"""
UE Style Node Creator
=====================

Substance 3D Designer 16.0.x plugin that implements a
UE-Material-Editor-style node creation workflow:

    Press shortcut key → click in graph → node placed

Target:  Substance 3D Designer 16.0.3+  (Python 3.13 + Qt 6.8 / PySide6)

Installation:
    Tools → Plugin Manager → Browse → select this file (UEStyleNodeCreator.py)

Entry points:
    initializeSDPlugin()    — called by Designer on load
    uninitializeSDPlugin()  — called by Designer on unload
"""

import os
import sys

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets

import sd

# ---------------------------------------------------------------------------
# Ensure the plugin directory is on sys.path so that `from core.xxx` works.
# SD adds this file's directory to sys.path when loading, but we also insert
# at position 0 as a safeguard in case another plugin shadows `core`.
# ---------------------------------------------------------------------------

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

# ---------------------------------------------------------------------------
# Force-clear stale submodule caches.
# Python's importlib.reload() does NOT cascade to submodules, so on a reload
# the old bytecode of core.* remains in sys.modules and causes ImportError
# when class names change between versions.
# ---------------------------------------------------------------------------
_SUB_MODULE_NAMES = [
    "core.ui",
    "core.i18n",
    "core.config",
    "core.create_mode",
    "core.graph_manager",
    "core.node_database",
    "core.shortcut_manager",
    "core.preset_module",
    "UEStyleNodeCreator_NodePresetBackend",
]
for _mod in _SUB_MODULE_NAMES:
    if _mod in sys.modules:
        del sys.modules[_mod]

from core.config import ConfigManager
from core.node_database import NodeDatabase
from core.shortcut_manager import ShortcutManager
from core.graph_manager import GraphManager
from core.create_mode import CreateModeController
from core.preset_module import NodePresetModule
from core.ui import ShortcutTableWidget, DOCK_ID

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_config = None
_node_database = None
_shortcut_manager = None
_graph_manager = None
_create_mode = None
_dock = None
_preset_module = None


# ---------------------------------------------------------------------------
# Plugin Lifecycle
# ---------------------------------------------------------------------------

def initializeSDPlugin():
    """Called by Substance Designer when the plugin is loaded."""
    global _config, _node_database, _shortcut_manager
    global _graph_manager, _create_mode, _dock, _preset_module

    print("[UEStyleNodeCreator] Initializing …")

    # 1. Configuration ------------------------------------------------
    _config = ConfigManager(PLUGIN_DIR)
    _config.load()

    # 2. Node database ------------------------------------------------
    _node_database = NodeDatabase()

    # 3. Shortcut manager ---------------------------------------------
    _shortcut_manager = ShortcutManager(_config)

    # 4. Graph manager ------------------------------------------------
    _graph_manager = GraphManager()

    # SBS node-group preset backend (loaded lazily from the NodePreset plugin).
    _preset_module = NodePresetModule(
        PLUGIN_DIR,
        _config.get_setting("preset_module_path", ""),
    )

    # Loading the plugin is also an explicit request to restore the persistent
    # SBS backend when node-group mode was left enabled.
    if _config.get_setting("preset_module_enabled", False):
        if not _preset_module.ensure_loaded(refresh_cache=True):
            print("[UEStyleNodeCreator] WARNING: {}".format(
                _preset_module.error))

    # 5. Create mode controller (the core UE experience) --------------
    _create_mode = CreateModeController(
        shortcut_manager=_shortcut_manager,
        graph_manager=_graph_manager,
        config=_config,
        node_database=_node_database,
        preset_module=_preset_module,
    )

    ok = _create_mode.initialize()
    if not ok:
        print("[UEStyleNodeCreator] WARNING: Create mode controller init failed.")
        print("[UEStyleNodeCreator] Shortcuts will NOT work.")

    # 6. Settings dock widget -----------------------------------------
    _create_dock()

    # Retry after the dock/event loop is ready. Package Manager can briefly be
    # unavailable while Designer is completing a plugin reload.
    if _config.get_setting("preset_module_enabled", False):
        QtCore.QTimer.singleShot(
            300,
            lambda: _preset_module and _preset_module.ensure_loaded(
                refresh_cache=False))

    print("[UEStyleNodeCreator] Ready.")
    _print_active_shortcuts()


def uninitializeSDPlugin():
    """Called by Substance Designer when the plugin is unloaded."""
    global _create_mode, _dock, _preset_module

    print("[UEStyleNodeCreator] Shutting down …")

    if _create_mode is not None:
        _create_mode.shutdown()

    if _preset_module is not None:
        _preset_module.shutdown()
        _preset_module = None

    if _dock is not None:
        try:
            _dock.close()
        except BaseException:
            pass
        _dock = None

    print("[UEStyleNodeCreator] Unloaded.")


# ---------------------------------------------------------------------------
# Dock Widget
# ---------------------------------------------------------------------------

def _create_dock():
    """Create the settings dock widget using the SD UI manager API."""
    global _dock

    try:
        app = sd.getContext().getSDApplication()
        ui_mgr = app.getQtForPythonUIMgr()

        _dock = ui_mgr.newDockWidget(
            identifier=DOCK_ID,
            title="UE Style Node Creator"
        )

        # Create the content widget
        def _on_reload():
            _print_active_shortcuts()

        widget = ShortcutTableWidget(
            config=_config,
            node_database=_node_database,
            shortcut_manager=_shortcut_manager,
            create_mode=_create_mode,
            preset_module=_preset_module,
            reload_callback=_on_reload,
        )
        widget.setMinimumSize(0, 0)
        try:
            _dock.setMinimumSize(0, 0)
        except BaseException:
            pass

        # Set content — SD's dock widget wraps the content
        if hasattr(_dock, "setWidget"):
            _dock.setWidget(widget)
        else:
            # Fallback for older API
            layout = QtWidgets.QVBoxLayout()
            layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(widget)
            _dock.setLayout(layout)

        widget.restore_saved_size(_dock)

        _dock.setVisible(True)
        try:
            _dock.show()
        except BaseException:
            pass
        # Dock managers may apply their own size during show(); restore once
        # more after that layout pass has completed.
        QtCore.QTimer.singleShot(100, lambda: widget.restore_saved_size(_dock))
        # Let Designer finish its dock layout before starting size tracking;
        # this prevents the initial layout pass from overwriting saved values.
        QtCore.QTimer.singleShot(600, widget.enable_window_size_tracking)

    except BaseException as e:
        print("[UEStyleNodeCreator] ERROR creating dock: {}".format(e))


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def _print_active_shortcuts():
    """Log the currently configured shortcuts for user awareness."""
    shortcuts = _shortcut_manager.get_all() if _shortcut_manager else {}
    if not shortcuts:
        print("[UEStyleNodeCreator] No shortcuts configured.")
        return

    print("[UEStyleNodeCreator] Active shortcuts:")
    for key, data in sorted(shortcuts.items()):
        print("  {:6s} → {}".format(
            "[" + key + "]",
            data.get("node_name", data.get("node_uid", "?"))
        ))
