"""
UE Style Node Creator - Configuration Manager

Reads and writes config.json for shortcut mappings and settings.
"""

import os
import json


class ConfigManager:
    """Manages plugin configuration persistence."""

    DEFAULT_SETTINGS = {
        "create_mode": "key_then_click",
        "language": "en",
        "enable_auto_connect": True,
        "show_preview": True,
        "indicator_color": "#288CFF",
        "indicator_size": 108,
        "indicator_shape": "orb",
        "table_alternate_color": "",
        "hold_delay_ms": 400,
        "selection_shortcut": "D",
        "open_properties_after_create": False,
        "preset_module_enabled": False,
        "preset_module_path": "",
        "preset_unload_delay_ms": 1500,
        "node_library_expanded": False,
        "preset_library_expanded": True,
        "collapsed_preset_groups": [],
        "window_width": 0,
        "window_height": 0,
    }

    def __init__(self, plugin_dir):
        self.plugin_dir = plugin_dir
        self.file_path = os.path.join(plugin_dir, "config.json")
        self.data = {
            "settings": dict(self.DEFAULT_SETTINGS),
            "shortcuts": {},
            "preset_shortcuts": {},
        }

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def load(self):
        """Load config from disk. Creates default if missing."""
        if not os.path.exists(self.file_path):
            self.save()
            return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)

            # Merge settings (preserve defaults for missing keys)
            if "settings" in loaded and isinstance(loaded["settings"], dict):
                for k, v in self.DEFAULT_SETTINGS.items():
                    loaded["settings"].setdefault(k, v)
                self.data["settings"] = loaded["settings"]

            if "shortcuts" in loaded and isinstance(loaded["shortcuts"], dict):
                self.data["shortcuts"] = loaded["shortcuts"]

            if "preset_shortcuts" in loaded and isinstance(loaded["preset_shortcuts"], dict):
                self.data["preset_shortcuts"] = loaded["preset_shortcuts"]

        except Exception as e:
            print("[UEStyleNodeCreator] Config load error:", e)

    def save(self):
        """Persist current config to disk."""
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print("[UEStyleNodeCreator] Config save error:", e)

    # ------------------------------------------------------------------
    # Settings access
    # ------------------------------------------------------------------

    def get_setting(self, key, default=None):
        return self.data.get("settings", {}).get(key, default)

    def set_setting(self, key, value):
        self.data.setdefault("settings", {})[key] = value
        self.save()

    # ------------------------------------------------------------------
    # Shortcut access
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_mouse_button(value, default="left"):
        value = str(value or "").strip().lower()
        return value if value in ("left", "right") else default

    @classmethod
    def shortcut_storage_key(cls, key, mouse_button):
        """Return a stable config key for one keyboard+mouse gesture."""
        key = str(key or "").strip().upper()
        button = cls.normalize_mouse_button(mouse_button)
        return "{}|{}".format(key, button)

    @classmethod
    def shortcut_logical_key(cls, storage_key, entry=None):
        """Read the keyboard part from new or legacy shortcut records."""
        entry = entry or {}
        explicit = str(entry.get("shortcut_key", "") or "").strip().upper()
        if explicit:
            return explicit
        raw = str(storage_key or "").strip()
        if "|" in raw:
            key_part, suffix = raw.rsplit("|", 1)
            if suffix.lower() in ("left", "right"):
                return key_part.upper()
        return raw.upper()

    @classmethod
    def shortcut_mouse_button(cls, storage_key, entry=None, default="left"):
        entry = entry or {}
        value = entry.get("mouse_button")
        if value:
            return cls.normalize_mouse_button(value, default)
        raw = str(storage_key or "")
        if "|" in raw:
            suffix = raw.rsplit("|", 1)[1].lower()
            if suffix in ("left", "right"):
                return suffix
        return default

    @classmethod
    def _find_shortcut_storage_key(cls, shortcuts, key, mouse_button, default):
        logical_key = str(key or "").strip().upper()
        button = cls.normalize_mouse_button(mouse_button, default)
        for storage_key, entry in shortcuts.items():
            if (cls.shortcut_logical_key(storage_key, entry) == logical_key and
                    cls.shortcut_mouse_button(
                        storage_key, entry, default) == button):
                return storage_key
        return None

    def get_shortcuts(self):
        """Return a copy of all shortcut mappings: {key: {node_uid, node_name}}."""
        return dict(self.data.get("shortcuts", {}))

    def get_shortcut(self, key, mouse_button=None):
        """Return the node info for a single key, or None."""
        shortcuts = self.data.get("shortcuts", {})
        if mouse_button is None:
            for storage_key, entry in shortcuts.items():
                if self.shortcut_logical_key(storage_key, entry) == key.upper():
                    return entry
            return None
        storage_key = self._find_shortcut_storage_key(
            shortcuts, key, mouse_button, "left")
        return shortcuts.get(storage_key) if storage_key else None

    def set_shortcut(self, key, node_uid, node_name, **extra):
        """Add or update a shortcut mapping. Extra kwargs stored as additional fields."""
        key = key.upper()
        mouse_button = self.normalize_mouse_button(
            extra.get("mouse_button"), "left")
        extra["mouse_button"] = mouse_button
        entry = {"node_uid": node_uid, "node_name": node_name}
        entry.update(extra)
        entry["shortcut_key"] = key
        shortcuts = self.data.setdefault("shortcuts", {})
        old_storage_key = self._find_shortcut_storage_key(
            shortcuts, key, mouse_button, "left")
        if old_storage_key is not None:
            del shortcuts[old_storage_key]
        shortcuts[self.shortcut_storage_key(key, mouse_button)] = entry
        self.save()

    def remove_shortcut(self, key, mouse_button=None):
        """Remove a shortcut mapping."""
        shortcuts = self.data.setdefault("shortcuts", {})
        if mouse_button is None and key in shortcuts:
            keys_to_remove = [key]
        else:
            logical_key = str(key or "").strip().upper()
            keys_to_remove = [
                storage_key for storage_key, entry in shortcuts.items()
                if self.shortcut_logical_key(storage_key, entry) == logical_key and
                (mouse_button is None or self.shortcut_mouse_button(
                    storage_key, entry, "left") ==
                 self.normalize_mouse_button(mouse_button, "left"))]
        if keys_to_remove:
            for storage_key in keys_to_remove:
                shortcuts.pop(storage_key, None)
            self.save()

    def clear_shortcuts(self):
        """Remove all shortcut mappings."""
        self.data["shortcuts"] = {}
        self.save()

    # ------------------------------------------------------------------
    # SBS preset shortcut references
    # ------------------------------------------------------------------

    def get_preset_shortcuts(self):
        """Return key-to-preset references. Preset node data remains in SBS."""
        return dict(self.data.get("preset_shortcuts", {}))

    def set_preset_shortcut(self, key, preset_id, preset_name, **extra):
        key = key.upper()
        mouse_button = self.normalize_mouse_button(
            extra.get("mouse_button"), "right")
        extra["mouse_button"] = mouse_button
        entry = {
            "preset_id": preset_id,
            "preset_name": preset_name,
            "entry_type": "preset",
        }
        entry.update(extra)
        entry["shortcut_key"] = key
        shortcuts = self.data.setdefault("preset_shortcuts", {})
        old_storage_key = self._find_shortcut_storage_key(
            shortcuts, key, mouse_button, "right")
        if old_storage_key is not None:
            del shortcuts[old_storage_key]
        shortcuts[self.shortcut_storage_key(key, mouse_button)] = entry
        self.save()

    def remove_preset_shortcut(self, key, mouse_button=None):
        shortcuts = self.data.setdefault("preset_shortcuts", {})
        if mouse_button is None and key in shortcuts:
            keys_to_remove = [key]
        else:
            logical_key = str(key or "").strip().upper()
            keys_to_remove = [
                storage_key for storage_key, entry in shortcuts.items()
                if self.shortcut_logical_key(storage_key, entry) == logical_key and
                (mouse_button is None or self.shortcut_mouse_button(
                    storage_key, entry, "right") ==
                 self.normalize_mouse_button(mouse_button, "right"))]
        if keys_to_remove:
            for storage_key in keys_to_remove:
                shortcuts.pop(storage_key, None)
            self.save()

    def clear_preset_shortcuts(self):
        self.data["preset_shortcuts"] = {}
        self.save()
