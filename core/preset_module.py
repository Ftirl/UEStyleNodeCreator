"""NodePreset integration layer.

The regular creator stores node shortcuts in this plugin's config.json.  This
adapter deliberately keeps preset contents in NodePreset's persistent SBS
package and exposes only small CRUD/copy operations to the creator UI.
"""

import importlib.util
import json
import os
import sys
import time


class NodePresetModule:
    """Lazy wrapper around the sibling NodePreset plugin implementation."""

    # Use a private module name so Designer cannot reuse a NodePreset plugin
    # imported from the folder this project was copied from.
    MODULE_NAME = "UEStyleNodeCreator_NodePresetBackend"

    def __init__(self, plugin_dir, configured_path=""):
        self._plugin_dir = plugin_dir
        self._configured_path = configured_path or ""
        self._module = None
        self._error = ""
        self._owns_module = False
        self._package = None
        self._package_loaded_by_adapter = False
        self._sbs_path = os.path.join(self._plugin_dir, "NodePresets.sbs")

    def _candidate_paths(self):
        # The bundled backend beside this plugin is authoritative and makes
        # the whole folder portable. Configured/sibling paths are retained
        # only as compatibility fallbacks for older installations.
        paths = [os.path.join(self._plugin_dir, "NodePreset.py")]
        if self._configured_path:
            paths.append(self._configured_path)
        paths.append(os.path.join(
            os.path.dirname(self._plugin_dir), "NodePreset", "NodePreset.py"))
        return paths

    def _load(self):
        if self._module is not None:
            return self._module

        module_path = next(
            (os.path.abspath(path) for path in self._candidate_paths()
             if path and os.path.isfile(path)), None)
        if not module_path:
            self._error = "NodePreset.py was not found beside the current plugin."
            return None

        existing = sys.modules.get(self.MODULE_NAME)
        existing_path = os.path.abspath(
            getattr(existing, "__file__", "") or "") if existing else ""
        if (existing is not None and hasattr(existing, "copy_nodes_to_graph") and
                os.path.normcase(existing_path) == os.path.normcase(module_path)):
            self._module = existing
            return existing
        if existing is not None:
            # The plugin folder moved while Designer stayed open. Never reuse
            # the backend module whose __file__ still points at the old copy.
            sys.modules.pop(self.MODULE_NAME, None)

        try:
            spec = importlib.util.spec_from_file_location(self.MODULE_NAME, module_path)
            if spec is None or spec.loader is None:
                raise ImportError("Could not create a module spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[self.MODULE_NAME] = module
            spec.loader.exec_module(module)
            self._module = module
            self._owns_module = True
            self._error = ""
            return module
        except BaseException as exc:
            sys.modules.pop(self.MODULE_NAME, None)
            self._error = "{}: {}".format(type(exc).__name__, exc)
            print("[UEStyleNodeCreator] NodePreset module load failed: {}".format(self._error))
            return None

    @property
    def error(self):
        self._load()
        return self._error

    def is_available(self):
        return self._load() is not None

    def package_path(self):
        """The integrated module always owns an SBS beside this plugin."""
        return self._sbs_path

    @staticmethod
    def _package_is_usable(module, package):
        """Probe more than getFilePath(), which survives a manual UI close."""
        if package is None:
            return False
        try:
            package_path = package.getFilePath()
            # Designer can leave the Python wrapper alive after unloading the
            # package. Accessing resources is a reliable liveness check.
            list(package.getChildrenResources(True))
        except BaseException:
            return False
        try:
            managed = module.get_pkg_mgr().getUserPackageFromFilePath(package_path)
            if managed is None:
                return False
        except BaseException:
            # Some Designer versions do not expose this lookup reliably; the
            # resource probe above remains the compatibility fallback.
            pass
        return True

    def _unload_stale_moved_package(self, module, package_mgr, package_path):
        """Unload another loaded copy owned by this plugin's old folder."""
        try:
            packages = list(package_mgr.getUserPackages())
        except BaseException:
            return False
        target = os.path.normcase(os.path.abspath(package_path))
        unloaded = False
        for package in packages:
            try:
                loaded_path = os.path.abspath(package.getFilePath() or "")
            except BaseException:
                continue
            if (not loaded_path or
                    os.path.normcase(loaded_path) == target or
                    os.path.basename(loaded_path).lower() != "nodepresets.sbs"):
                continue
            # Only touch a stale package located beside another copy of this
            # exact plugin. A user's unrelated SBS with the same filename is
            # deliberately left alone.
            old_plugin = os.path.join(
                os.path.dirname(loaded_path), "UEStyleNodeCreator.py")
            if not os.path.isfile(old_plugin):
                continue
            try:
                package_mgr.unloadUserPackage(package)
                unloaded = True
                print(
                    "[UEStyleNodeCreator] Unloaded stale SBS from moved plugin: "
                    "{}".format(loaded_path))
            except BaseException as exc:
                print(
                    "[UEStyleNodeCreator] Could not unload stale SBS '{}': {}".format(
                        loaded_path, exc))
        return unloaded

    def _collapse_package_in_explorer(self, schedule_retries=False):
        """Collapse only the NodePresets package entry in Designer Explorer."""
        module = self._module
        if module is None:
            return False
        try:
            try:
                from PySide6 import QtCore, QtWidgets
            except ImportError:
                from PySide2 import QtCore, QtWidgets
            main_window = module.get_ui_mgr().getMainWindow()
        except BaseException:
            return False

        target_names = {"nodepresets", "nodepresets.sbs"}
        collapsed = False
        for tree in main_window.findChildren(QtWidgets.QTreeView):
            try:
                model = tree.model()
                if model is None:
                    continue
                visited = [0]

                def collapse_matching(parent=QtCore.QModelIndex(), depth=0):
                    nonlocal collapsed
                    if depth > 8 or visited[0] > 6000:
                        return
                    row_count = model.rowCount(parent)
                    for row in range(row_count):
                        if visited[0] > 6000:
                            return
                        visited[0] += 1
                        index = model.index(row, 0, parent)
                        if not index.isValid():
                            continue
                        text = str(model.data(
                            index, QtCore.Qt.ItemDataRole.DisplayRole) or "")
                        normalized = text.strip().lower().rstrip("*").strip()
                        if (normalized in target_names or
                                normalized.startswith("nodepresets.sbs ")):
                            tree.collapse(index)
                            collapsed = True
                            # The package itself is the target; do not walk
                            # through and accidentally affect its resources.
                            continue
                        if model.rowCount(index) > 0:
                            collapse_matching(index, depth + 1)

                collapse_matching()
            except BaseException:
                continue

        if schedule_retries:
            # Explorer updates asynchronously after loadUserPackage. Repeat
            # after its common layout/model refresh passes.
            for delay in (0, 180, 650, 1400):
                QtCore.QTimer.singleShot(
                    delay,
                    lambda adapter=self:
                        adapter._collapse_package_in_explorer(False))
        if collapsed and schedule_retries:
            print("[UEStyleNodeCreator] Collapsed NodePresets in Explorer.")
        return collapsed

    def _reload_package(self, module):
        """Force Designer to load the SBS again after it was manually closed."""
        package_path = self.package_path()
        if not os.path.exists(package_path):
            self._error = "SBS preset package does not exist: {}".format(package_path)
            return None
        self._package = None
        package_mgr = module.get_pkg_mgr()

        # A document/tab close can invalidate NodePreset's cached wrapper while
        # Package Manager still owns another live wrapper for the same file.
        # Reuse that instance before attempting a duplicate load.
        try:
            managed = package_mgr.getUserPackageFromFilePath(package_path)
        except BaseException:
            managed = None
        if self._package_is_usable(module, managed):
            package = managed
            loaded_by_adapter = False
        else:
            package = None
            loaded_by_adapter = True
        try:
            if package is None:
                package = package_mgr.loadUserPackage(package_path)
        except BaseException as exc:
            # Some SD versions report "already loaded" while still allowing
            # the live package to be resolved immediately afterwards.
            try:
                package = package_mgr.getUserPackageFromFilePath(package_path)
            except BaseException:
                package = None
            if (not self._package_is_usable(module, package) and
                    self._unload_stale_moved_package(
                        module, package_mgr, package_path)):
                try:
                    package = package_mgr.loadUserPackage(package_path)
                except BaseException as retry_exc:
                    exc = retry_exc
            if not self._package_is_usable(module, package):
                self._error = "SBS reload failed: {}".format(exc)
                print("[UEStyleNodeCreator] {}".format(self._error))
                return None
        if not self._package_is_usable(module, package):
            self._error = "Designer returned an unusable SBS package instance."
            return None
        self._package = package
        self._package_loaded_by_adapter = loaded_by_adapter
        print("[UEStyleNodeCreator] Reopened SBS preset package: {}".format(
            package_path))
        self._collapse_package_in_explorer(schedule_retries=True)
        self._error = ""
        return package

    def ensure_loaded(self, refresh_cache=False):
        """Explicitly ensure NodePresets.sbs is resident in Package Manager."""
        module = self._load()
        if module is None:
            return False
        if refresh_cache:
            package = self._reload_package(module)
        else:
            package = self._open_package(create=False)
        if package is None:
            if not self._error:
                self._error = "NodePresets.sbs could not be loaded."
            return False
        self._error = ""
        return True

    def _open_package(self, create=False):
        module = self._load()
        if module is None:
            return None
        package = self._package
        if package is not None and not self._package_is_usable(module, package):
            print("[UEStyleNodeCreator] Stale SBS package cache detected; reloading.")
            self._package = None
            package = None
        if package is None and os.path.exists(self.package_path()):
            package = self._reload_package(module)
        if package is None and create:
            try:
                package = module.get_pkg_mgr().newUserPackage()
                module.get_pkg_mgr().savePackageAs(package, self.package_path())
                self._package = package
                self._package_loaded_by_adapter = True
                self._error = ""
            except BaseException as exc:
                package = None
                self._error = "Could not create SBS preset package: {}".format(exc)
        return package

    @staticmethod
    def _delete_graph_in_package(module, package, graph_identifier):
        """Use NodePreset's original deleteGraph -> graph.delete fallback."""
        if package is None or not graph_identifier:
            return False
        graph = module.find_graph_by_name(package, graph_identifier)
        if graph is None:
            # Match the original plugin: an already absent graph is a
            # successful end state and must not block overwrite/cleanup.
            return True

        try:
            package.deleteGraph(graph)
            print("[UEStyleNodeCreator] Deleted SBS graph with package.deleteGraph: {}".format(
                graph_identifier))
            return True
        except BaseException as package_error:
            print("[UEStyleNodeCreator] package.deleteGraph failed for '{}': {}".format(
                graph_identifier, package_error))

        try:
            graph.delete()
            print("[UEStyleNodeCreator] Deleted SBS graph with graph.delete: {}".format(
                graph_identifier))
            return True
        except BaseException as graph_error:
            print("[UEStyleNodeCreator] graph.delete failed for '{}': {}".format(
                graph_identifier, graph_error))

        # Keep the broader compatibility attempts from the shared NodePreset
        # helper for Designer builds exposing a different package API.
        try:
            return bool(module.remove_graph_from_package_safe(package, graph))
        except BaseException:
            return False

    def list_presets(self):
        """Return SBS resources with UI metadata; no node payload enters JSON."""
        module = self._load()
        if module is None or not os.path.exists(self.package_path()):
            return []
        package = self._open_package(create=False)
        if package is None:
            return []

        result = []
        for info in module.list_preset_resource_infos(package):
            graph_id = info.get("id", "")
            graph = info.get("resource")
            try:
                node_count = len(list(graph.getNodes()))
            except BaseException:
                node_count = 0
            result.append({
                "preset_id": graph_id,
                "preset_name": module.get_preset_display_name_for_ui(graph_id, graph),
                "group": module.get_preset_ui_group(graph_id) or "",
                "kind": info.get("kind", ""),
                "node_count": node_count,
            })
        return sorted(result, key=lambda item: (
            item["group"].lower(), item["preset_name"].lower(), item["preset_id"].lower()))

    def save_selection(self, name, group="", overwrite=False):
        """Store selected nodes, their parameters and internal links in SBS."""
        module = self._load()
        if module is None:
            raise RuntimeError(self._error or "NodePreset module is unavailable")

        preset_name = str(name or "").strip()
        preset_group = str(group or "").strip()
        if not preset_name:
            raise ValueError("Preset name is required.")

        current_graph = module.get_current_graph()
        selected_nodes = module.get_selected_nodes()
        if current_graph is None:
            raise RuntimeError("No graph is currently open.")
        if not selected_nodes:
            raise RuntimeError("Select one or more graph nodes first.")

        package = self._open_package(create=True)
        if package is None:
            raise RuntimeError("Could not open or create NodePresets.sbs.")

        kind = module.get_preset_resource_kind(current_graph)
        graph_id = module.make_preset_graph_identifier(preset_name, preset_group, kind)
        existing = module.find_graph_by_name(package, graph_id)
        if existing is not None:
            if not overwrite:
                return {"exists": True, "preset_id": graph_id}
            if not self._delete_graph_in_package(module, package, graph_id):
                raise RuntimeError("Could not remove the existing SBS preset.")
            module.remove_preset_ui_meta(graph_id)

        preset_graph = module.create_preset_resource(package, graph_id, kind)
        module.set_graph_identifier_safe(preset_graph, graph_id)
        module.set_graph_display_name_annotation(preset_graph, preset_name)
        module.set_annotation_string(preset_graph, "preset_name", preset_name)
        module.set_annotation_string(preset_graph, "preset_kind", kind)
        module.set_annotation_string(preset_graph, "group", preset_group)
        module.set_preset_ui_meta(
            graph_or_name=graph_id,
            group=preset_group,
            display_name=preset_name,
            order=time.time(),
            kind=kind,
        )

        # This metadata is only a readable summary.  The authoritative node
        # values, property functions, dynamic types and links are copied into
        # the SBS graph by NodePreset.copy_nodes_to_graph below.
        try:
            params = module.extract_input_value_params_from_nodes(selected_nodes)
            serializable = [{
                "identifier": str(item.get("identifier", "")),
                "value": str(item.get("value", "")),
            } for item in (params or [])]
            if serializable:
                module.set_annotation_string(
                    preset_graph, "params_detail",
                    json.dumps(serializable, ensure_ascii=False))
        except BaseException:
            pass

        created = module.copy_nodes_to_graph(
            src_nodes=selected_nodes,
            dst_graph=preset_graph,
            offset_x=0,
            offset_y=0,
            src_graph=current_graph,
        )
        if not created:
            self._delete_graph_in_package(module, package, graph_id)
            module.remove_preset_ui_meta(graph_id)
            raise RuntimeError("No selected nodes could be stored in the SBS preset.")
        if not module.save_package(package, self.package_path()):
            raise RuntimeError("Saving NodePresets.sbs failed.")

        return {
            "exists": False,
            "preset_id": graph_id,
            "preset_name": preset_name,
            "group": preset_group,
            "kind": kind,
            "node_count": len(created),
        }

    def create_at(self, preset_id, graph_position):
        """Copy an SBS preset into the current graph centered at a click."""
        module = self._load()
        if module is None:
            raise RuntimeError(self._error or "NodePreset module is unavailable")
        target_graph = module.get_current_graph()
        if target_graph is None:
            raise RuntimeError("No graph is currently open.")
        package = self._open_package(create=False)
        if package is None:
            raise RuntimeError("NodePresets.sbs could not be opened.")
        preset_graph = module.find_graph_by_name(package, preset_id)
        if preset_graph is None:
            raise RuntimeError("SBS preset '{}' was not found.".format(preset_id))

        preset_kind = module.get_preset_ui_kind(preset_id, preset_graph)
        target_kind = module.get_preset_resource_kind(target_graph)
        if preset_kind != target_kind:
            raise RuntimeError("Preset graph type does not match the current graph.")

        src_nodes = list(preset_graph.getNodes())
        if not src_nodes:
            raise RuntimeError("The SBS preset contains no nodes.")
        center = module.get_nodes_bounding_box_center(src_nodes)
        x, y = graph_position
        offset_x = x - center[0] if center else x
        offset_y = y - center[1] if center else y

        def copy_and_center():
            created_nodes = module.copy_nodes_to_graph(
                src_nodes=src_nodes,
                dst_graph=target_graph,
                offset_x=offset_x,
                offset_y=offset_y,
                src_graph=preset_graph,
            ) or []

            # Correct from the positions actually produced by Designer. Some
            # node types normalize their position during creation, which can
            # make a source-based offset land near the graph origin after
            # zoom/pan. Keep this correction in the same undo transaction as
            # node/property/connection creation.
            actual_center = module.get_nodes_bounding_box_center(created_nodes)
            if actual_center:
                correction_x = x - actual_center[0]
                correction_y = y - actual_center[1]
                if abs(correction_x) > 0.01 or abs(correction_y) > 0.01:
                    for node in created_nodes:
                        node_x, node_y = module.get_node_position(node)
                        module.set_node_position(
                            node, node_x + correction_x, node_y + correction_y)
            return created_nodes

        # Direct SD graph API calls are only grouped into Designer's undo
        # history when an explicit history transaction is active. Treat one
        # preset insertion as one action so a single Ctrl+Z removes the whole
        # group, including its restored properties and connections.
        try:
            from sd.api import sdhistoryutils
        except ImportError:
            sdhistoryutils = None

        if sdhistoryutils is not None:
            display_name = module.get_preset_display_name_for_ui(
                preset_id, preset_graph) or preset_id
            with sdhistoryutils.SDHistoryUtils.UndoGroup(
                    "Create SBS Preset: {}".format(display_name)):
                created = copy_and_center()
        else:
            # Compatibility fallback for older Designer builds without the
            # history utility module. Creation still works as before.
            created = copy_and_center()
        return created

    def create_at_view_center(self, preset_id, graph_view=None):
        """Load a preset using the visible Graph View center (Load button)."""
        module = self._load()
        if module is None:
            raise RuntimeError(self._error or "NodePreset module is unavailable")
        position = None

        def center_of(view):
            try:
                if view is None or not view.isVisible() or view.scene() is None:
                    return None
                viewport = view.viewport()
                visible = view.mapToScene(viewport.rect()).boundingRect()
                if not visible.isEmpty():
                    return visible.center().x(), visible.center().y()
            except BaseException:
                return None
            return None

        position = center_of(graph_view)
        if position is None:
            # The controller's cached view can be stale after switching
            # graphs. Resolve the currently visible graph canvas again.
            try:
                from PySide6 import QtWidgets
            except ImportError:
                from PySide2 import QtWidgets
            try:
                main_window = module.get_ui_mgr().getMainWindow()
                views = [v for v in main_window.findChildren(QtWidgets.QGraphicsView)
                         if v.isVisible() and v.scene() is not None]
                named = [v for v in views if any(
                    part in ((v.objectName() or "") + " " +
                             (v.metaObject().className() or "")).lower()
                    for part in ("graph", "node", "canvas"))]
                candidates = named or views
                current_view = max(
                    candidates, key=lambda v: v.viewport().width() * v.viewport().height(),
                    default=None)
                position = center_of(current_view)
            except BaseException:
                position = None
        if position is None:
            raise RuntimeError("Could not locate the current Graph View viewport.")
        print("[UEStyleNodeCreator] Loading SBS preset '{}' at ({:.0f}, {:.0f})".format(
            preset_id, position[0], position[1]))
        return self.create_at(preset_id, position)

    def delete_preset(self, preset_id):
        module = self._load()
        package = self._open_package(create=False) if module else None
        if package is None:
            self._error = "NodePresets.sbs could not be opened for deletion."
            return False
        graph = module.find_graph_by_name(package, preset_id)
        if graph is None:
            self._error = "SBS preset '{}' was not found.".format(preset_id)
            return False
        if not self._delete_graph_in_package(module, package, preset_id):
            self._error = "Designer could not delete SBS graph '{}'.".format(preset_id)
            return False
        module.remove_preset_ui_meta(preset_id)
        if not module.save_package(package, self.package_path()):
            self._error = "The graph was deleted in memory, but saving NodePresets.sbs failed."
            return False
        self._error = ""
        return True

    def unload_package(self):
        """Unload this extension's SBS package without unloading NodePreset code."""
        module = self._module
        package = self._package
        unloaded = True
        if module is not None:
            try:
                package_mgr = module.get_pkg_mgr()
            except BaseException as exc:
                package_mgr = None
                unloaded = False
                self._error = "SBS unload failed: {}".format(exc)
            # The cached wrapper can be absent or stale after a manual document
            # close. Resolve the exact integrated SBS from Package Manager too.
            try:
                managed = (package_mgr.getUserPackageFromFilePath(
                    self.package_path()) if package_mgr is not None else None)
            except BaseException:
                managed = None
            if self._package_is_usable(module, managed):
                package = managed
            if package_mgr is not None and package is not None:
                try:
                    package_mgr.unloadUserPackage(package)
                    print("[UEStyleNodeCreator] Unloaded SBS preset package: {}".format(
                        self.package_path()))
                except BaseException as exc:
                    # An unusable wrapper means Designer already unloaded it.
                    if self._package_is_usable(module, package):
                        unloaded = False
                        self._error = "SBS unload failed: {}".format(exc)
        self._package = None
        self._package_loaded_by_adapter = False
        if unloaded:
            self._error = ""
        elif self._error:
            print("[UEStyleNodeCreator] {}".format(self._error))
        return unloaded

    def shutdown(self):
        self.unload_package()
