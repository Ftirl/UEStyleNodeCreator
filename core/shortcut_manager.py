"""
UE Style Node Creator - Shortcut Manager

Manages the mapping between keyboard keys and node types.
Delegates persistence to ConfigManager.
"""


class ShortcutManager:
    """In-memory shortcut registry backed by ConfigManager."""

    def __init__(self, config):
        self._config = config
        self._shortcuts = {}  # key -> {node_uid, node_name}
        self._node_shortcuts = {}
        self._preset_shortcuts = {}
        self.reload()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reload(self):
        """Reload JSON shortcuts and optionally merge SBS preset shortcuts."""
        self._node_shortcuts = {}
        for storage_key, entry in self._config.get_shortcuts().items():
            normalized = dict(entry)
            normalized["entry_type"] = "node"
            key = self._config.shortcut_logical_key(storage_key, normalized)
            normalized["mouse_button"] = self._normalize_mouse_button(
                self._config.shortcut_mouse_button(
                    storage_key, normalized, "left"), "left")
            normalized["shortcut_key"] = key
            self._node_shortcuts[(key, normalized["mouse_button"])] = normalized
        self._preset_shortcuts = {}
        if self.preset_mode_enabled():
            raw = self._config.get_preset_shortcuts()
            for storage_key, entry in raw.items():
                normalized = dict(entry)
                normalized["entry_type"] = "preset"
                key = self._config.shortcut_logical_key(storage_key, normalized)
                normalized["mouse_button"] = self._normalize_mouse_button(
                    self._config.shortcut_mouse_button(
                        storage_key, normalized, "right"), "right")
                normalized["shortcut_key"] = key
                normalized["node_name"] = normalized.get(
                    "preset_name", normalized.get("preset_id", "Preset"))
                self._preset_shortcuts[(key, normalized["mouse_button"])] = normalized

        # Compatibility/diagnostic view keyed by the complete gesture.
        self._shortcuts = {}
        for (key, button), entry in self._preset_shortcuts.items():
            self._shortcuts["{}|{}".format(key, button)] = entry
        for (key, button), entry in self._node_shortcuts.items():
            self._shortcuts["{}|{}".format(key, button)] = entry

    def preset_mode_enabled(self):
        return bool(self._config.get_setting("preset_module_enabled", False))

    def set_preset_mode_enabled(self, enabled):
        self._config.set_setting("preset_module_enabled", bool(enabled))
        self.reload()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def register(self, key, node_uid, node_name, **extra):
        """Register a new shortcut. Returns False if conflict."""
        key = key.upper()
        button = self._normalize_mouse_button(
            extra.get("mouse_button"), "left")
        extra["mouse_button"] = button
        if (key, button) in self._node_shortcuts:
            return False

        entry = {"node_uid": node_uid, "node_name": node_name}
        entry.update(extra)
        self._node_shortcuts[(key, button)] = entry
        self._shortcuts["{}|{}".format(key, button)] = entry
        self._config.set_shortcut(key, node_uid, node_name, **extra)
        return True

    def register_preset(self, key, preset_id, preset_name, **extra):
        key = key.upper()
        button = self._normalize_mouse_button(
            extra.get("mouse_button"), "right")
        extra["mouse_button"] = button
        if (key, button) in self._preset_shortcuts:
            return False
        entry = {
            "preset_id": preset_id,
            "preset_name": preset_name,
            "node_name": preset_name,
            "entry_type": "preset",
        }
        entry.update(extra)
        self._preset_shortcuts[(key, button)] = entry
        self._shortcuts["{}|{}".format(key, button)] = entry
        self._config.set_preset_shortcut(key, preset_id, preset_name, **extra)
        return True

    def remove(self, key, entry_type="node", mouse_button=None):
        """Remove a shortcut by key."""
        key = key.upper()
        shortcuts = (self._preset_shortcuts
                     if entry_type == "preset" else self._node_shortcuts)
        if mouse_button is None:
            matches = [gesture for gesture in shortcuts if gesture[0] == key]
        else:
            default = "right" if entry_type == "preset" else "left"
            matches = [(key, self._normalize_mouse_button(mouse_button, default))]
            matches = [gesture for gesture in matches if gesture in shortcuts]
        if not matches:
            return False
        for _, button in matches:
            if entry_type == "preset":
                self._config.remove_preset_shortcut(key, button)
            else:
                self._config.remove_shortcut(key, button)
        self.reload()
        return True

    def get_node_for_key(self, key):
        """Look up the node info for a pressed key.

        Returns:
            dict or None: {node_uid, node_name}
        """
        key = key.upper()
        return (self._node_shortcuts.get((key, "left")) or
                self._node_shortcuts.get((key, "right")) or
                self._preset_shortcuts.get((key, "left")) or
                self._preset_shortcuts.get((key, "right")))

    def get_targets_for_key(self, key):
        """Return entries routed by each entry's configurable mouse button."""
        key = key.upper()
        targets = {"left": None, "right": None}
        for button in ("left", "right"):
            node = self._node_shortcuts.get((key, button))
            preset = self._preset_shortcuts.get((key, button))
            # A same-gesture cross-type collision can only come from a legacy
            # or hand-edited config. JSON retains the historical precedence.
            targets[button] = node or preset
            if node is not None and preset is not None:
                print(
                    "[UEStyleNodeCreator] Gesture conflict [{} + {} click]; "
                    "using '{}'.".format(
                        key, button, node.get("node_name", "JSON node")))
        if targets["left"] is None and targets["right"] is None:
            return None
        return targets

    @staticmethod
    def _normalize_mouse_button(value, default):
        value = str(value or "").strip().lower()
        return value if value in ("left", "right") else default

    def get_gesture_conflict(self, key, mouse_button, entry_type):
        """Return the opposite storage entry occupying the same gesture."""
        key = key.upper()
        mouse_button = self._normalize_mouse_button(mouse_button, "left")
        if entry_type == "preset":
            other = self._node_shortcuts.get((key, mouse_button))
        else:
            other = self._preset_shortcuts.get((key, mouse_button))
        return other

    def get_entry(self, key, mouse_button, entry_type="node"):
        default = "right" if entry_type == "preset" else "left"
        gesture = (key.upper(), self._normalize_mouse_button(
            mouse_button, default))
        shortcuts = (self._preset_shortcuts
                     if entry_type == "preset" else self._node_shortcuts)
        return shortcuts.get(gesture)

    def get_all(self):
        """Return all shortcuts as {key: {node_uid, node_name}}."""
        return dict(self._shortcuts)

    def has_conflict(self, key, entry_type="node", mouse_button=None):
        """Check for a conflict within one mouse-button mapping domain."""
        shortcuts = (self._preset_shortcuts
                     if entry_type == "preset" else self._node_shortcuts)
        key = key.upper()
        if mouse_button is None:
            return any(gesture[0] == key for gesture in shortcuts)
        default = "right" if entry_type == "preset" else "left"
        button = self._normalize_mouse_button(mouse_button, default)
        return (key, button) in shortcuts
