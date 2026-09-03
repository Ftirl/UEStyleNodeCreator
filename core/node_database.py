"""
UE Style Node Creator - Node Database

Scans the current graph for available node definitions,
provides search and label-based lookup (robust across SD versions).
"""

import sd


class NodeDatabase:
    """Caches node definitions available in the current graph.

    Indexed by both UID (exact) and label (fuzzy) so that shortcuts
    can remain stable even when UIDs change between SD versions.
    """

    def __init__(self):
        self.nodes = {}        # uid  -> {uid, label, category}
        self._label_index = {}  # label_lower -> uid

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def scan_current_graph(self):
        """Scan the current graph for all available node definitions.

        Returns:
            dict: {node_uid: {uid, label, category}, ...}
        """
        graph = self._get_current_graph()
        if graph is None:
            return {}

        self.nodes.clear()
        self._label_index.clear()

        try:
            definitions = graph.getNodeDefinitions()
        except Exception as e:
            print("[UEStyleNodeCreator] getNodeDefinitions failed:", e)
            return {}

        for definition in definitions:
            try:
                uid = definition.getId()
            except Exception:
                continue

            try:
                label = definition.getLabel()
            except Exception:
                label = uid

            # Derive a category from the UID
            category = ""
            if "::" in uid:
                parts = uid.split("::")
                if len(parts) >= 2:
                    category = parts[-2]

            self.nodes[uid] = {
                "uid": uid,
                "label": label,
                "category": category,
            }

            # Build label index (lowercase for case-insensitive lookup)
            self._label_index[label.lower()] = uid

            # Also index common aliases (strip prefix/suffix variations)
            # e.g. "Blend" might be labeled "Blend Node" or "Blend (Compositing)"
            short_label = label.lower().split("(")[0].strip()
            if short_label and short_label not in self._label_index:
                self._label_index[short_label] = uid

        return self.nodes

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_node(self, uid):
        """Get a single node definition by exact UID."""
        return self.nodes.get(uid)

    def find_by_label(self, name):
        """Find a node definition by label (case-insensitive, fuzzy).

        Tries:
          1. Exact lowercase label match
          2. Label contains `name` as substring
          3. UID contains `name` as substring

        Returns:
            dict or None: {uid, label, category}
        """
        name_lower = name.lower().strip()

        # 1. Exact label match
        uid = self._label_index.get(name_lower)
        if uid and uid in self.nodes:
            return self.nodes[uid]

        # 2. Label substring match
        for label_lower, uid in self._label_index.items():
            if name_lower in label_lower:
                if uid in self.nodes:
                    return self.nodes[uid]

        # 3. UID substring match
        for uid, data in self.nodes.items():
            if name_lower in uid.lower():
                return data

        return None

    def find_by_uid_or_label(self, uid_or_label):
        """Resolve a node by UID first, then by label as fallback.

        This is the primary resolution method for shortcuts:
        config stores a UID, but if it's stale we fall back to label.

        Returns:
            dict or None
        """
        # Try exact UID first
        node = self.get_node(uid_or_label)
        if node is not None:
            return node

        # Fall back to label search
        return self.find_by_label(uid_or_label)

    def search(self, text):
        """Full-text search across UID and label + loaded package graphs.

        Returns:
            list: Matching node dicts (max 50).
        """
        text = text.lower()
        results = []

        # 1. Node definitions
        for uid, data in self.nodes.items():
            if text in uid.lower() or text in data["label"].lower():
                results.append(data)
                if len(results) >= 50:
                    return results

        # 2. Graphs from loaded packages (for instance nodes)
        try:
            app = sd.getContext().getSDApplication()
            pkg_mgr = app.getPackageMgr()
            seen = set()
            for pkg in pkg_mgr.getUserPackages():
                for child in pkg.getChildrenResources(True):
                    try:
                        cid = child.getIdentifier() or ""
                    except BaseException:
                        continue
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    if text in cid.lower():
                        url = ""
                        if hasattr(child, "getUrl"):
                            try:
                                url = child.getUrl() or ""
                            except BaseException:
                                pass
                        results.append({
                            "uid": "sbs::compositing::sbscompgraph_instance",
                            "label": cid,
                            "category": "instance",
                            "instance_url": url or "pkg:///{}?dependency=0".format(cid),
                            "instance_graph": cid,
                            "instance_pkg": child.getFilePath() if hasattr(child, "getFilePath") else "",
                        })
                        if len(results) >= 50:
                            return results
        except BaseException:
            pass

        return results

    def get_all(self):
        """Return all cached node definitions."""
        return dict(self.nodes)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _get_current_graph():
        try:
            app = sd.getContext().getSDApplication()
            ui_mgr = app.getQtForPythonUIMgr()
            return ui_mgr.getCurrentGraph()
        except Exception:
            return None
