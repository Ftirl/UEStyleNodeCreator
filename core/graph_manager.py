"""UE Style Node Creator - Graph Manager"""

import os
import sd
from sd.api.sdbasetypes import float2
from sd.api.sdproperty import SDPropertyCategory

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    try:
        from PySide6 import QtTest
    except ImportError:
        QtTest = None
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    try:
        from PySide2 import QtTest
    except ImportError:
        QtTest = None


class GraphManager:

    @staticmethod
    def _get_ui_mgr():
        return sd.getContext().getSDApplication().getQtForPythonUIMgr()

    @classmethod
    def get_current_graph(cls):
        try:
            return cls._get_ui_mgr().getCurrentGraph()
        except BaseException:
            return None

    @classmethod
    def get_selected_nodes(cls):
        """Return unique selected graph nodes, excluding frames and UI objects."""
        ui_mgr = cls._get_ui_mgr()
        selection = []
        try:
            if hasattr(ui_mgr, "getCurrentGraphSelectedNodes"):
                selection = list(ui_mgr.getCurrentGraphSelectedNodes() or [])
        except BaseException:
            pass
        if not selection:
            try:
                if hasattr(ui_mgr, "getCurrentGraphSelection"):
                    selection = list(ui_mgr.getCurrentGraphSelection() or [])
            except BaseException:
                pass

        nodes = []
        seen = set()
        for item in selection:
            # Match NodePreset's proven node test. Designer's generic graph
            # selection may also contain frames, comments and other UI items.
            if not (hasattr(item, "getDefinition") or
                    hasattr(item, "getDefinitionId")):
                continue
            identity = None
            for getter_name in ("getIdentifier", "getUid"):
                getter = getattr(item, getter_name, None)
                if getter is None:
                    continue
                try:
                    value = getter()
                    if value is not None:
                        identity = (getter_name, str(value))
                        break
                except BaseException:
                    pass
            if identity is None:
                identity = ("python", id(item))
            if identity in seen:
                continue
            seen.add(identity)
            nodes.append(item)
        return nodes

    @classmethod
    def create_node(cls, node_uid, position=None, node_label=None,
                    node_database=None, **extra):
        graph = cls.get_current_graph()
        if graph is None:
            return None

        # Attempt 1: UID directly
        try:
            node = graph.newNode(node_uid)
            if node is not None:
                if position is not None:
                    cls.set_node_position(node, position)
                return node
        except BaseException:
            pass

        # Attempt 2: Special node types (Input Value / Instance)
        lower_uid = node_uid.lower()
        if "input_value" in lower_uid or "inputvalue" in lower_uid or "instance" in lower_uid:
            node = cls._create_special_node(graph, node_uid, node_label, position, **extra)
            if node is not None:
                return node

        # Attempt 3: Label lookup
        if node_database is not None:
            candidate = (node_database.find_by_label(node_label) if node_label else None)
            if candidate is None:
                candidate = node_database.find_by_uid_or_label(node_uid)
            if candidate is not None:
                real_uid = candidate["uid"]
                try:
                    node = graph.newNode(real_uid)
                    if node is not None:
                        if position is not None:
                            cls.set_node_position(node, position)
                        return node
                except BaseException:
                    pass
                if "input_value" in real_uid.lower() or "instance" in real_uid.lower():
                    node = cls._create_special_node(graph, real_uid, node_label, position, **extra)
                    if node is not None:
                        return node

        # Attempt 4: Scan definitions
        try:
            for d in graph.getNodeDefinitions():
                try:
                    if d.getId() == node_uid:
                        node = graph.newNode(node_uid)
                        if node is not None:
                            if position is not None:
                                cls.set_node_position(node, position)
                            return node
                        break
                except BaseException:
                    continue
        except BaseException:
            pass

        return None

    @classmethod
    def _create_special_node(cls, graph, node_uid, node_label, position,
                              instance_graph=None, instance_pkg=None,
                              instance_pkg_url=None, instance_url=None, **kw):
        lower_uid = node_uid.lower()
        is_instance = "instance" in lower_uid

        if is_instance:
            ref_resource = None
            dep_id = None
            if instance_url:
                if "?dependency=" in instance_url:
                    try:
                        dep_id = int(instance_url.split("?dependency=")[-1].split("&")[0])
                    except BaseException:
                        pass
                ref_resource = cls._find_graph_by_url(instance_url)

            if ref_resource is None and instance_pkg_url and instance_graph:
                try:
                    pkg = sd.getContext().getSDApplication().getPackageMgr().loadUserPackage(instance_pkg_url)
                    if pkg is not None:
                        for child in pkg.getChildrenResources(True):
                            try:
                                if child.getIdentifier() == instance_graph:
                                    ref_resource = child
                                    break
                            except BaseException:
                                continue
                except BaseException:
                    pass

            if ref_resource is None and instance_graph:
                ref_resource = cls._find_graph_resource(instance_graph, instance_pkg, dep_id)

            if ref_resource is not None:
                try:
                    node = graph.newInstanceNode(ref_resource)
                    if node is not None:
                        if position is not None:
                            cls.set_node_position(node, position)
                        return node
                except BaseException:
                    pass
                return None

            return None

        # Input Value node
        type_names = ["float1", "float2", "float3", "float4", "color",
                       "int1", "int2", "int3", "int4", "bool", "string"]
        sd_type = None
        if node_label:
            ll = node_label.lower().replace(" ", "").replace("_", "")
            for t in type_names:
                if t in ll:
                    sd_type = cls._find_type_in_graph(graph, t)
                    if sd_type is not None:
                        break
        if sd_type is None:
            for t in type_names:
                sd_type = cls._find_type_in_graph(graph, t)
                if sd_type is not None:
                    break
        if sd_type is not None:
            try:
                if hasattr(graph, "newInputValueNode"):
                    return graph.newInputValueNode(sd_type)
            except BaseException:
                pass
            try:
                return graph.newNode(node_uid, sd_type)
            except BaseException:
                pass
        return None

    @classmethod
    def _find_graph_by_url(cls, url):
        try:
            pkg_mgr = sd.getContext().getSDApplication().getPackageMgr()
            if hasattr(pkg_mgr, "getResourceFromUrl"):
                result = pkg_mgr.getResourceFromUrl(url)
                if result is not None:
                    return result
            if url.startswith("pkg:///"):
                p = url[len("pkg:///"):]
                gid = p.split("?")[0].split("/")[-1]
                dep_id = None
                if "?dependency=" in p:
                    try:
                        dep_id = int(p.split("?dependency=")[-1].split("&")[0])
                    except BaseException:
                        pass
                return cls._find_graph_resource(gid, None, dep_id)
        except BaseException:
            pass
        return None

    @classmethod
    def _find_graph_resource(cls, graph_identifier, package_path=None, dependency_id=None):
        try:
            pkg_mgr = sd.getContext().getSDApplication().getPackageMgr()
            all_pkgs = []
            try:
                all_pkgs.extend(pkg_mgr.getUserPackages())
            except BaseException:
                pass
            for attr in ("getPackages", "getLoadedPackages", "getAllPackages"):
                if hasattr(pkg_mgr, attr):
                    try:
                        for p in getattr(pkg_mgr, attr)():
                            if p not in all_pkgs:
                                all_pkgs.append(p)
                    except BaseException:
                        pass

            best = None
            for pkg in all_pkgs:
                for child in pkg.getChildrenResources(True):
                    try:
                        if child.getIdentifier() == graph_identifier:
                            cn = child.getClassName().lower() if hasattr(child, "getClassName") else ""
                            if "compgraph" not in cn and "sbscompgraph" not in cn:
                                continue
                            if dependency_id is not None and hasattr(pkg, "getUid"):
                                try:
                                    if pkg.getUid() == dependency_id:
                                        return child
                                except BaseException:
                                    pass
                            if best is None:
                                best = child
                    except BaseException:
                        continue
            return best
        except BaseException:
            pass
        return None

    @classmethod
    def _find_type_in_graph(cls, graph, type_name):
        wanted = type_name.lower()
        try:
            for node in graph.getNodes():
                for cat in (SDPropertyCategory.Input, SDPropertyCategory.Output,
                             SDPropertyCategory.Annotation):
                    try:
                        for prop in node.getProperties(cat):
                            for t in prop.getTypes():
                                tid = t.getId().lower() if hasattr(t, "getId") else str(t).lower()
                                if wanted in tid or tid == wanted:
                                    return t
                    except BaseException:
                        continue
        except BaseException:
            pass
        return None

    @staticmethod
    def get_node_position(node):
        try:
            pos = node.getPosition()
            try:
                return (pos.x, pos.y)
            except BaseException:
                pass
            try:
                return (pos[0], pos[1])
            except BaseException:
                pass
        except BaseException:
            pass
        return (0, 0)

    @staticmethod
    def set_node_position(node, pos):
        x, y = float(pos[0]), float(pos[1])
        try:
            node.setPosition(float2(x, y))
            return True
        except BaseException:
            pass
        try:
            node.setPosition((x, y))
            return True
        except BaseException:
            return False

    @classmethod
    def try_auto_connect(cls, new_node, selected_nodes=None):
        if selected_nodes is None:
            selected_nodes = cls.get_selected_nodes()
        if not selected_nodes:
            return 0
        graph = cls.get_current_graph()
        if graph is None:
            return 0
        try:
            new_inputs = [p for p in new_node.getProperties(SDPropertyCategory.Input)
                          if cls._is_connectable(p)]
        except BaseException:
            return 0
        connections = 0
        for src_node in selected_nodes:
            if src_node is new_node:
                continue
            try:
                src_outputs = [p for p in src_node.getProperties(SDPropertyCategory.Output)
                               if cls._is_connectable(p)]
            except BaseException:
                continue
            for dst_inp in list(new_inputs):
                for src_out in src_outputs:
                    if cls._types_compatible(cls._get_type_ids(src_out),
                                              cls._get_type_ids(dst_inp)):
                        try:
                            graph.connectNodes(src_node, src_out, new_node, dst_inp)
                            connections += 1
                            new_inputs.remove(dst_inp)
                            break
                        except BaseException:
                            pass
                if dst_inp not in new_inputs:
                    break
        return connections

    @classmethod
    def focus_node_properties(cls, node, simulate_click=False,
                              graph_view=None, click_global_pos=None,
                              fast_click=False):
        """Select *node* in the Graph View so Properties follows it.

        Designer exposes the current selection as a getter, but does not
        expose a stable public setter across the supported versions.  The
        Qt graph scene is therefore used as a compatibility fallback.  The
        node's scene bounding box comes from Designer's UI manager, so this
        does not depend on a hard-coded node size.
        """
        if node is None:
            return False

        try:
            ui_mgr = cls._get_ui_mgr()
            main_window = ui_mgr.getMainWindow()
            if graph_view is None:
                graph_view = cls._find_graph_view(main_window)
            if graph_view is None or graph_view.scene() is None:
                return False

            # Most reliable path: repeat the user's placement click at the
            # exact same screen position after Designer has rendered the node.
            if simulate_click and click_global_pos is not None:
                viewport = graph_view.viewport()
                if viewport is not None:
                    view_pos = viewport.mapFromGlobal(click_global_pos)
                    clicked = cls._click_graph_view_at_view_pos(graph_view, view_pos)
                    if clicked:
                        cls._raise_properties_dock(main_window)
                        # The deferred exact-position click is the path known
                        # to work in Designer. Avoid synchronous selection
                        # polling and several fallback clicks on the UI thread.
                        if fast_click:
                            return True
                        if cls._node_is_selected(ui_mgr, node):
                            return True

            # getGraphNodeBBox is the most reliable way to translate the SD
            # node into a point that belongs to its QGraphicsItem.
            bbox = None
            try:
                bbox_mgr = ui_mgr
                if (not hasattr(bbox_mgr, "getGraphViewIDAt") or
                        not hasattr(bbox_mgr, "getGraphNodeBBox")):
                    app = sd.getContext().getSDApplication()
                    if hasattr(app, "getUIMgr"):
                        bbox_mgr = app.getUIMgr()
                view_id = bbox_mgr.getGraphViewIDAt(0)
                bbox = bbox_mgr.getGraphNodeBBox(view_id, node)
            except BaseException:
                pass

            if bbox is not None:
                x, y, width, height = cls._read_bbox(bbox)
                if width > 0 and height > 0:
                    points = (
                        (0.50, 0.18),  # node header, avoids input/output pins
                        (0.50, 0.50),
                        (0.25, 0.30),
                        (0.75, 0.30),
                    )
                    for fx, fy in points:
                        scene_pos = QtCore.QPointF(x + width * fx, y + height * fy)
                        if simulate_click and cls._click_graph_view(
                                graph_view, scene_pos):
                            cls._raise_properties_dock(main_window)
                            if fast_click:
                                return True
                            if cls._node_is_selected(ui_mgr, node):
                                return True
                        if cls._select_scene_item(graph_view, scene_pos):
                            if cls._node_is_selected(ui_mgr, node):
                                cls._raise_properties_dock(main_window)
                                return True

            # A few SD builds expose an item at the node's center even when
            # getGraphNodeBBox is unavailable.
            pos = cls.get_node_position(node)
            scene_pos = QtCore.QPointF(pos[0], pos[1])
            if (simulate_click and cls._click_graph_view(graph_view, scene_pos) and
                    cls._node_is_selected(ui_mgr, node)):
                cls._raise_properties_dock(main_window)
                return True
            if (cls._select_scene_item(graph_view, scene_pos) and
                    cls._node_is_selected(ui_mgr, node)):
                cls._raise_properties_dock(main_window)
                return True
        except BaseException as e:
            print("[UEStyleNodeCreator] Could not focus node Properties: {}".format(e))
        return False

    @classmethod
    def is_node_selected(cls, node):
        """Public lightweight selection probe for deferred focus retries."""
        try:
            return cls._node_is_selected(cls._get_ui_mgr(), node)
        except BaseException:
            return False

    @staticmethod
    def _read_bbox(bbox):
        values = []
        for name in ("x", "y", "z", "w"):
            value = getattr(bbox, name, None)
            if callable(value):
                value = value()
            if value is None:
                try:
                    value = bbox[len(values)]
                except BaseException:
                    raise ValueError("Unsupported graph node bounding box")
            values.append(float(value))
        return tuple(values)

    @staticmethod
    def _find_graph_view(parent):
        if parent is None:
            return None
        try:
            views = parent.findChildren(QtWidgets.QGraphicsView)
        except BaseException:
            return None
        for view in views:
            name = (view.objectName() or "").lower()
            try:
                class_name = (view.metaObject().className() or "").lower()
            except BaseException:
                class_name = ""
            if any(word in name or word in class_name
                   for word in ("graph", "node", "canvas")):
                return view
        visible = [view for view in views if view.isVisible()]
        return max((visible if visible else views),
                   key=lambda view: view.width() * view.height(), default=None)

    @staticmethod
    def _select_scene_item(graph_view, scene_pos):
        """Select the selectable QGraphicsItem at a scene position."""
        scene = graph_view.scene()
        try:
            view_pos = graph_view.mapFromScene(scene_pos)
            items = scene.items(view_pos)
        except BaseException:
            return False

        for item in items:
            candidate = item
            while candidate is not None:
                try:
                    flags = candidate.flags()
                    try:
                        selectable_flag = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                    except AttributeError:
                        selectable_flag = QtWidgets.QGraphicsItem.ItemIsSelectable
                    selectable = bool(flags & selectable_flag)
                except BaseException:
                    selectable = False
                if selectable:
                    try:
                        scene.clearSelection()
                        candidate.setSelected(True)
                        return True
                    except BaseException:
                        break
                try:
                    candidate = candidate.parentItem()
                except BaseException:
                    candidate = None
        return False

    @staticmethod
    def _click_graph_view(graph_view, scene_pos):
        """Send a normal left-click to the Graph View at *scene_pos*."""
        try:
            view_pos = graph_view.mapFromScene(scene_pos)
            return GraphManager._click_graph_view_at_view_pos(graph_view, view_pos)
        except BaseException as e:
            print("[UEStyleNodeCreator] Could not map Graph View click: {}".format(e))
            return False

    @staticmethod
    def _click_graph_view_at_view_pos(graph_view, view_pos):
        """Send a real Qt left-click at a Graph View viewport position."""
        try:
            viewport = graph_view.viewport()
            if viewport is None:
                return False
            point_int = QtCore.QPoint(int(view_pos.x()), int(view_pos.y()))
            if not viewport.rect().contains(point_int):
                return False
            try:
                viewport.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
            except BaseException:
                try:
                    viewport.setFocus()
                except BaseException:
                    pass
            button = QtCore.Qt.MouseButton.LeftButton
            modifiers = QtCore.Qt.KeyboardModifier.NoModifier

            # QTest follows the same delivery path as a normal user click and
            # is more reliable for Designer's custom Graph View than manually
            # selecting a QGraphicsItem.
            try:
                if QtTest is None:
                    raise RuntimeError("QtTest is unavailable")
                QtTest.QTest.mouseClick(viewport, button, modifiers, point_int)
                return True
            except BaseException:
                pass

            point = QtCore.QPointF(point_int)
            buttons = QtCore.Qt.MouseButton.LeftButton
            press = QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseButtonPress,
                point, button, buttons, modifiers)
            release = QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseButtonRelease,
                point, button, QtCore.Qt.MouseButton.NoButton, modifiers)
            QtWidgets.QApplication.sendEvent(viewport, press)
            QtWidgets.QApplication.sendEvent(viewport, release)
            return True
        except BaseException as e:
            print("[UEStyleNodeCreator] Could not simulate Graph View click: {}".format(e))
            return False

    @staticmethod
    def _node_is_selected(ui_mgr, node):
        """Check whether Designer reports the created node as selected."""
        try:
            selected = ui_mgr.getCurrentGraphSelectedNodes()
        except BaseException:
            try:
                selected = ui_mgr.getCurrentGraphSelection()
            except BaseException:
                return False
        try:
            node_id = node.getIdentifier()
        except BaseException:
            node_id = None
        for selected_node in selected:
            if selected_node is node:
                return True
            if node_id is not None:
                try:
                    if selected_node.getIdentifier() == node_id:
                        return True
                except BaseException:
                    pass
        return False

    @staticmethod
    def _raise_properties_dock(main_window):
        """Bring Designer's Properties dock to the front when it is a dock."""
        if main_window is None:
            return
        try:
            docks = main_window.findChildren(QtWidgets.QDockWidget)
        except BaseException:
            return
        for dock in docks:
            title = (dock.windowTitle() or "").lower()
            name = (dock.objectName() or "").lower()
            if "propert" in title or "propert" in name or "attribute" in title:
                try:
                    dock.show()
                    dock.raise_()
                    dock.activateWindow()
                except BaseException:
                    pass
                return

    @staticmethod
    def _is_connectable(prop):
        try:
            return prop.isConnectable()
        except BaseException:
            return False

    @staticmethod
    def _get_type_ids(prop):
        ids = set()
        try:
            for t in prop.getTypes():
                try:
                    ids.add(t.getId().lower())
                except BaseException:
                    pass
        except BaseException:
            pass
        return ids

    @staticmethod
    def _types_compatible(a, b):
        return bool(a and b and a & b)
