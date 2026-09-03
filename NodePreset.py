# =============================================================================
# Node Preset Manager - Streamlined Version
# =============================================================================

import os
import time  # 添加这一行
import sd
from sd.api.sdproperty import SDPropertyCategory

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui

DOCK_ID = "NodePresets_Dock_01"

PARAMS_JSON_PREFIX = "__NODE_PRESET_PARAMS_JSON__:"

# =============================================================================
# Basic Helpers
# =============================================================================

def safe_name(name):
    """安全的名称处理（保留中文和其他 Unicode 字符用于显示）"""
    name = name.strip()
    # 只过滤掉文件系统不安全的字符
    unsafe_chars = '<>:"/\\|?*\x00'
    return "".join(c for c in name if c not in unsafe_chars)

def make_graph_identifier(name):
    """生成 graph identifier。

    当前 SD 版本支持中文 identifier，所以预设名和 graph identifier 保持一致。
    这里只过滤明显不安全的字符。
    """
    name = safe_name(name)

    if not name:
        return "preset"

    # Graph identifier 不建议包含换行、制表符
    name = name.replace("\n", " ").replace("\r", " ").replace("\t", " ")

    # 合并多余空格
    name = " ".join(name.split())

    return name


PRESET_KIND_COMP_GRAPH = "comp_graph"
PRESET_KIND_FUNCTION_GRAPH = "function_graph"
FUNCTION_GRAPH_IDENTIFIER_SUFFIX = "__fn"


def get_resource_class_name(resource):
    """安全获取 SD resource class 名"""
    if not resource:
        return ""

    try:
        return resource.getClassName()
    except BaseException:
        pass

    try:
        return resource.__class__.__name__
    except BaseException:
        return ""


def get_preset_resource_kind(resource):
    """判断当前 graph/resource 是普通 graph 还是 function graph"""
    cls_name = get_resource_class_name(resource).lower()

    if "compgraph" in cls_name or "sdsbscompgraph" in cls_name:
        return PRESET_KIND_COMP_GRAPH

    if "function" in cls_name or "sbsfunction" in cls_name:
        return PRESET_KIND_FUNCTION_GRAPH

    return PRESET_KIND_COMP_GRAPH


def make_preset_graph_identifier(preset_name, group_name=None, kind=None):
    """根据预设名、分组、类型生成 package 内 resource identifier。

    普通 graph:
        无分组：PresetName
        有分组：PresetName-GroupName

    Function graph:
        无分组：PresetName__fn
        有分组：PresetName-GroupName__fn
    """
    preset_name = make_graph_identifier(preset_name)
    group_name = safe_name(group_name or "").strip()

    if not preset_name:
        preset_name = "preset"

    effective_kind = kind or PRESET_KIND_COMP_GRAPH

    if group_name:
        group_name = make_graph_identifier(group_name)
        identifier = "{}-{}".format(preset_name, group_name)
    else:
        identifier = preset_name

    if effective_kind == PRESET_KIND_FUNCTION_GRAPH:
        if not identifier.endswith(FUNCTION_GRAPH_IDENTIFIER_SUFFIX):
            identifier = "{}{}".format(identifier, FUNCTION_GRAPH_IDENTIFIER_SUFFIX)

    return identifier


def get_preset_base_name_from_graph_id(graph_id):
    """从 graph id 推测基础预设名。"""
    base_name, _, _ = split_preset_graph_identifier(graph_id)
    return base_name


def split_preset_graph_identifier(graph_id):
    """从 identifier 拆出 base_name, group_name, kind。

    普通：
        Noise-Color -> Noise, Color, comp_graph

    Function：
        Noise-Color__fn -> Noise, Color, function_graph
    """
    if not graph_id:
        return "", "", PRESET_KIND_COMP_GRAPH

    s = str(graph_id).strip()

    kind = PRESET_KIND_COMP_GRAPH

    if s.endswith(FUNCTION_GRAPH_IDENTIFIER_SUFFIX):
        kind = PRESET_KIND_FUNCTION_GRAPH
        s = s[:-len(FUNCTION_GRAPH_IDENTIFIER_SUFFIX)]

    if "-" not in s:
        return s, "", kind

    base_name, group_name = s.rsplit("-", 1)

    return base_name.strip(), group_name.strip(), kind


def get_preset_kind_from_graph_id(graph_id):
    """从 graph id 获取 kind"""
    _, _, kind = split_preset_graph_identifier(graph_id)
    return kind


def get_preset_display_name_for_ui(graph_id, graph=None):
    """UI 始终显示基础预设名，不显示 -分组名。"""
    try:
        meta = get_preset_ui_meta(graph_id)
        display = str(meta.get("display_name", "") or "").strip()
        if display:
            return display
    except BaseException:
        pass

    if graph:
        try:
            v = graph.getAnnotationPropertyValueFromId("preset_name")
            if v:
                display = sdvalue_to_python(v)
                if display:
                    return str(display)
        except BaseException:
            pass

    return get_preset_base_name_from_graph_id(graph_id)


def set_graph_identifier_safe(graph, identifier):
    """安全设置 graph identifier"""
    if not graph or not identifier:
        return False

    try:
        graph.setIdentifier(identifier)
        return True
    except BaseException as e:
        # print("[NodePresets] setIdentifier failed:", e)
        return False


def set_graph_display_name_annotation(graph, display_name):
    """保存 UI 显示名到 annotation，不依赖 setLabel"""
    if not graph:
        return False

    display_name = str(display_name or "").strip()

    try:
        from sd.api.sdvaluestring import SDValueString

        graph.setAnnotationPropertyValueFromId(
            "preset_name",
            SDValueString.sNew(display_name)
        )

        return True

    except BaseException as e:
        # print("[NodePresets] set preset_name annotation failed:", e)
        return False


def get_preset_group_from_graph_id(graph_id):
    """从 graph identifier fallback 获取 group（不依赖 UI meta）"""
    if not graph_id:
        return ""
    s = str(graph_id).strip()
    if "-" not in s:
        return ""
    _, group_name = s.rsplit("-", 1)
    return group_name.strip()


def repair_ui_meta_from_graph_ids(graph_ids, resource_map=None):
    """根据 graph identifier 自动修复 UI meta"""
    if not graph_ids:
        return False

    data = load_ui_meta()
    presets = data.setdefault("presets", {})

    changed = False
    existing = set(graph_ids or [])

    for key in list(presets.keys()):
        if key not in existing:
            del presets[key]
            changed = True

    for graph_id in graph_ids:
        base_name, group_name, kind_from_id = split_preset_graph_identifier(graph_id)

        item = presets.setdefault(graph_id, {})

        if not str(item.get("display_name", "") or "").strip() and base_name:
            item["display_name"] = base_name
            changed = True

        if not str(item.get("group", "") or "").strip() and group_name:
            item["group"] = group_name
            changed = True

        if not str(item.get("kind", "") or "").strip():
            if resource_map and graph_id in resource_map:
                item["kind"] = get_preset_resource_kind(resource_map[graph_id])
            else:
                item["kind"] = kind_from_id
            changed = True

        presets[graph_id] = item

    if changed:
        save_ui_meta(data)

    return changed


def ask_delete_group_mode(parent, group_name, affected_count):
    """询问删除分组模式。

    返回:
        "keep"   保留预设
        "delete" 全删除
        None     取消
    """
    msg = QtWidgets.QMessageBox(parent)
    msg.setWindowTitle("Delete Group")
    msg.setIcon(QtWidgets.QMessageBox.Warning)

    msg.setText(
        "确定要删除分组：{}\n\n"
        "该分组下有 {} 个预设。请选择处理方式：".format(
            group_name,
            affected_count
        )
    )

    keep_btn = msg.addButton(
        "保留预设",
        QtWidgets.QMessageBox.AcceptRole
    )

    delete_btn = msg.addButton(
        "全删除",
        QtWidgets.QMessageBox.DestructiveRole
    )

    cancel_btn = msg.addButton(
        "取消",
        QtWidgets.QMessageBox.RejectRole
    )

    msg.setDefaultButton(keep_btn)

    try:
        msg.exec_()
    except AttributeError:
        msg.exec()

    clicked = msg.clickedButton()

    if clicked == keep_btn:
        return "keep"

    if clicked == delete_btn:
        return "delete"

    return None


def ask_overwrite_preset(parent, preset_name, group_name, graph_identifier):
    """询问是否覆盖已有预设"""
    group_display = group_name if group_name else "Ungrouped"

    reply = QtWidgets.QMessageBox.question(
        parent,
        "Preset Exists",
        "预设已存在：\n\n"
        "预设名：{}\n"
        "分组：{}\n"
        "Graph：{}\n\n"
        "是否覆盖旧预设？".format(
            preset_name,
            group_display,
            graph_identifier
        ),
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No
    )

    return reply == QtWidgets.QMessageBox.Yes


def get_app():
    return sd.getContext().getSDApplication()

def get_ui_mgr():
    return get_app().getQtForPythonUIMgr()

def get_pkg_mgr():
    return get_app().getPackageMgr()

def get_plugin_dir():
    # This backend is bundled by UEStyleNodeCreator and may be loaded under a
    # private module name. Always resolve SBS/UI metadata beside this file so
    # copying the plugin folder cannot retain the original absolute path.
    return os.path.dirname(os.path.abspath(__file__))

def get_package_path():
    return os.path.join(get_plugin_dir(), "NodePresets.sbs")

def get_package_name():
    return "NodePresets.sbs"

def get_current_graph():
    try:
        return get_ui_mgr().getCurrentGraph()
    except Exception:
        return None

def get_selected_nodes():
    ui_mgr = get_ui_mgr()
    if not ui_mgr:
        return []
    
    try:
        if hasattr(ui_mgr, "getCurrentGraphSelectedNodes"):
            selection = ui_mgr.getCurrentGraphSelectedNodes()
        else:
            selection = ui_mgr.getCurrentGraphSelection()
    except Exception:
        return []
    
    return [item for item in selection if hasattr(item, "getDefinition") or hasattr(item, "getDefinitionId")]

def get_node_def_id(node):
    if not node:
        return None
    try:
        if hasattr(node, "getDefinitionId"):
            def_id = node.getDefinitionId()
            if def_id:
                return def_id
    except Exception:
        pass
    try:
        definition = node.getDefinition()
        if definition:
            return definition.getId()
    except Exception:
        pass
    return None

def get_node_position(node):
    try:
        pos = node.getPosition()
        try:
            return pos.x, pos.y
        except Exception:
            return pos[0], pos[1]
    except Exception:
        return 0, 0

def set_node_position(node, x, y):
    try:
        from sd.api.sdbasetypes import float2
        node.setPosition(float2(x, y))
    except Exception:
        try:
            node.setPosition((x, y))
        except Exception:
            pass

def get_nodes_bounding_box_center(nodes):
    """Calculate the center point of a set of nodes' bounding box.

    Returns:
        (center_x, center_y) or None if no nodes.
    """
    if not nodes:
        return None

    nodes_list = list(nodes)
    if not nodes_list:
        return None

    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')

    for node in nodes_list:
        x, y = get_node_position(node)
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x)
        max_y = max(max_y, y)

    if min_x == float('inf'):
        return None

    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0

    return center_x, center_y


def get_current_graph_view_center():
    """Try to get the center of the user's current graph view.

    First attempts to read the visible area from the graph view widget via Qt.
    Falls back to the center of existing nodes in the current graph.
    Falls back to (0, 0) if the graph is empty.
    """
    # Attempt 1: Try to get the visible center from the graph view widget
    try:
        ui_mgr = get_ui_mgr()
        main_window = ui_mgr.getMainWindow()

        # Search for QGraphicsView widgets (the graph view)
        for view in main_window.findChildren(QtWidgets.QGraphicsView):
            try:
                # Only match the graph view, not other graphics views
                scene = view.scene()
                if scene is None:
                    continue
                # Get the visible rect center in scene coordinates
                visible_rect = view.mapToScene(view.viewport().rect()).boundingRect()
                if not visible_rect.isEmpty():
                    center_x = visible_rect.center().x()
                    center_y = visible_rect.center().y()
                    print("[NodePresets] Graph view visible center: ({:.0f}, {:.0f})".format(center_x, center_y))
                    return center_x, center_y
            except BaseException:
                continue
    except BaseException as e:
        print("[NodePresets] Could not get graph view widget:", e)

    # Attempt 2: Use the center of existing nodes in the current graph
    graph = get_current_graph()
    if graph:
        try:
            nodes = list(graph.getNodes())
            if nodes:
                center = get_nodes_bounding_box_center(nodes)
                if center:
                    print("[NodePresets] Using current graph nodes center: ({:.0f}, {:.0f})".format(*center))
                    return center
        except BaseException:
            pass

    # Attempt 3: Default to (0, 0)
    print("[NodePresets] No reference point found, using (0, 0)")
    return 0, 0

def copy_node_label(src, dst):
    try:
        label = src.getLabel()
        if label:
            dst.setLabel(label)
    except Exception:
        pass

def sdvalue_to_python(value):
    if value is None:
        return None
    try:
        if hasattr(value, "get"):
            return value.get()
    except BaseException:
        pass
    return value

def debug_value(value):
    try:
        return value.get() if value and hasattr(value, "get") else value
    except BaseException:
        return value


def get_annotation_string(obj, ann_id, default=None):
    """安全读取 annotation string"""
    if not obj or not ann_id:
        return default
    try:
        v = obj.getAnnotationPropertyValueFromId(ann_id)
        if v is None:
            return default
        pv = sdvalue_to_python(v)
        if pv is None:
            return default
        return str(pv)
    except BaseException:
        return default


def set_annotation_string(obj, ann_id, text):
    """安全写入 annotation string"""
    if not obj or not ann_id or text is None:
        return False
    try:
        from sd.api.sdvaluestring import SDValueString
        obj.setAnnotationPropertyValueFromId(ann_id, SDValueString.sNew(str(text)))
        return True
    except BaseException as e:
        # print("[NodePresets] set_annotation_string failed {}: {}".format(ann_id, e))
        return False


def get_graph_display_name(graph):
    """获取预设显示名"""
    if not graph:
        return ""
    name = get_annotation_string(graph, "preset_name", None)
    if name:
        return name
    try:
        return graph.getIdentifier()
    except BaseException:
        return ""


def get_graph_group(graph):
    """获取预设分组"""
    group = get_annotation_string(graph, "group", "")
    group = group.strip() if group else ""
    return group or None


def get_graph_identifier(graph):
    try:
        return graph.getIdentifier()
    except BaseException:
        return None
    
# =============================================================================
# UI Meta Helpers - UI only, does not affect .sbs package
# =============================================================================

UI_META_FILE_NAME = "NodePresets_ui.json"


def get_ui_meta_path():
    """UI 层配置文件路径，不写入 sbs package"""
    return os.path.join(get_plugin_dir(), UI_META_FILE_NAME)


def load_ui_meta():
    """读取 UI 配置"""
    import json

    path = get_ui_meta_path()

    default_data = {
        "version": 1,
        "presets": {}
    }

    if not os.path.exists(path):
        return default_data

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return default_data

        if "presets" not in data or not isinstance(data["presets"], dict):
            data["presets"] = {}

        if "version" not in data:
            data["version"] = 1

        return data

    except Exception as e:
        # print("[NodePresets] load_ui_meta failed:", e)
        return default_data


def save_ui_meta(data):
    """保存 UI 配置"""
    import json

    path = get_ui_meta_path()
    folder = os.path.dirname(path)

    try:
        if not os.path.exists(folder):
            os.makedirs(folder)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return True

    except Exception as e:
        # print("[NodePresets] save_ui_meta failed:", e)
        return False


def get_graph_identifier_safe(graph_or_name):
    """获取 graph identifier"""
    if not graph_or_name:
        return None

    if isinstance(graph_or_name, str):
        return graph_or_name

    try:
        return graph_or_name.getIdentifier()
    except BaseException:
        return None


def get_preset_ui_meta(graph_or_name):
    """获取单个 preset 的 UI meta"""
    graph_id = get_graph_identifier_safe(graph_or_name)
    if not graph_id:
        return {}

    data = load_ui_meta()
    return data.get("presets", {}).get(graph_id, {}) or {}


def get_preset_ui_group(graph_or_name):
    """获取 UI 层分组，优先从 UI meta 读，fallback 到 graph identifier 后缀"""
    meta = get_preset_ui_meta(graph_or_name)
    group = meta.get("group", "")
    group = str(group).strip() if group else ""
    if group:
        return group
    # fallback：从 graph identifier 的 suffix 提取
    graph_id = get_graph_identifier_safe(graph_or_name)
    if graph_id:
        fallback = get_preset_group_from_graph_id(graph_id)
        if fallback:
            return fallback
    return None


def set_preset_ui_meta(graph_or_name, group=None, display_name=None, order=None, kind=None):
    """设置 UI 层 meta，不影响 package"""
    graph_id = get_graph_identifier_safe(graph_or_name)
    if not graph_id:
        return False

    data = load_ui_meta()

    presets = data.setdefault("presets", {})
    item = presets.setdefault(graph_id, {})

    if group is not None:
        item["group"] = str(group).strip()

    if display_name is not None:
        item["display_name"] = str(display_name).strip()

    if kind is not None:
        item["kind"] = str(kind).strip()

    if order is not None:
        item["order"] = order
    elif "order" not in item:
        item["order"] = time.time()

    return save_ui_meta(data)


def get_preset_ui_kind(graph_id, resource=None):
    """获取 preset 类型"""
    try:
        meta = get_preset_ui_meta(graph_id)
        kind = str(meta.get("kind", "") or "").strip()
        if kind:
            return kind
    except BaseException:
        pass

    if resource:
        try:
            return get_preset_resource_kind(resource)
        except BaseException:
            pass

    return get_preset_kind_from_graph_id(graph_id)


def remove_preset_ui_meta(graph_or_name):
    """删除 preset 对应的 UI meta"""
    graph_id = get_graph_identifier_safe(graph_or_name)
    if not graph_id:
        return False

    data = load_ui_meta()
    presets = data.setdefault("presets", {})

    if graph_id in presets:
        del presets[graph_id]
        return save_ui_meta(data)

    return True


def rename_preset_ui_meta(old_graph_id, new_graph_id, display_name=None):
    """重命名 preset 时迁移 UI meta。"""
    if not old_graph_id or not new_graph_id:
        return False

    try:
        data = load_ui_meta()
    except Exception:
        return False

    presets = data.setdefault("presets", {})

    old_item = presets.get(old_graph_id, {}) or {}
    new_item = presets.get(new_graph_id, {}) or {}

    merged = {}
    merged.update(old_item)
    merged.update(new_item)

    if display_name is not None:
        merged["display_name"] = str(display_name).strip()

    presets[new_graph_id] = merged

    if old_graph_id != new_graph_id and old_graph_id in presets:
        del presets[old_graph_id]

    try:
        return save_ui_meta(data)
    except Exception as e:
        # print("[NodePresets] rename_preset_ui_meta save failed:", e)
        return False


def cleanup_ui_meta_existing_graphs(existing_graph_ids):
    """清理已经不存在的 graph 对应的 UI meta"""
    data = load_ui_meta()
    presets = data.setdefault("presets", {})

    existing = set(existing_graph_ids or [])
    changed = False

    for graph_id in list(presets.keys()):
        if graph_id not in existing:
            del presets[graph_id]
            changed = True

    if changed:
        save_ui_meta(data)


def cleanup_ui_meta_groups(existing_graph_ids=None):
    """清理 UI meta 中的无效项和空白分组。

    不影响 package。
    只清理 NodePresets_ui.json。
    """
    data = load_ui_meta()
    presets = data.setdefault("presets", {})

    changed = False

    existing = set(existing_graph_ids or [])

    for graph_id in list(presets.keys()):
        if existing and graph_id not in existing:
            del presets[graph_id]
            changed = True
            continue

        meta = presets.get(graph_id, {}) or {}

        group = str(meta.get("group", "") or "").strip()
        if meta.get("group", "") != group:
            meta["group"] = group
            changed = True

        presets[graph_id] = meta

    if changed:
        save_ui_meta(data)

    return changed


def get_used_ui_groups(existing_graph_ids=None):
    """获取当前仍被 preset 使用的 UI 分组"""
    data = load_ui_meta()
    presets = data.get("presets", {}) or {}

    existing = set(existing_graph_ids or [])
    groups = set()

    for graph_id, meta in presets.items():
        if existing and graph_id not in existing:
            continue

        group = str((meta or {}).get("group", "") or "").strip()
        if group:
            groups.add(group)

    return sorted(groups)


def delete_ui_group(group_name, existing_graph_ids=None):
    """删除 UI 分组。

    不删除 preset。
    只是把该 group 下的 preset 改成未分组。
    """
    group_name = str(group_name or "").strip()

    if not group_name:
        return False

    data = load_ui_meta()
    presets = data.setdefault("presets", {})

    existing = set(existing_graph_ids or [])
    changed = False
    affected = 0

    for graph_id, meta in presets.items():
        if existing and graph_id not in existing:
            continue

        meta = meta or {}
        group = str(meta.get("group", "") or "").strip()

        if group == group_name:
            meta["group"] = ""
            presets[graph_id] = meta
            changed = True
            affected += 1

    if changed:
        save_ui_meta(data)

    # print("[NodePresets] Deleted UI group '{}', affected presets: {}".format(group_name, affected))

    return changed

# =============================================================================
# Package Helpers
# =============================================================================

# 全局缓存
_cached_package = None
_cached_package_path = None
_user_opened_package = False  # 标记是否是用户手动打开的

def test_create_package():
    """创建空的 package 文件"""
    package_path = get_package_path()
    plugin_dir = get_plugin_dir()
    
    if not os.path.exists(plugin_dir):
        os.makedirs(plugin_dir)
    
    try:
        pkg_mgr = get_pkg_mgr()
        
        print("[NodePresets] Creating new package")
        start_time = time.time()
        
        pkg = pkg_mgr.newUserPackage()
        print("[NodePresets] ⏱ newUserPackage took: {:.3f}s".format(time.time() - start_time))
        
        if not pkg:
            print("[NodePresets] Failed to create package")
            return False
        
        # 保存到文件
        start_time = time.time()
        pkg_mgr.savePackageAs(pkg, package_path)
        print("[NodePresets] ⏱ savePackageAs took: {:.3f}s".format(time.time() - start_time))
        print("[NodePresets] Package saved:", package_path)
        
        # 立即卸载
        try:
            start_time = time.time()
            pkg_mgr.unloadUserPackage(pkg)
            print("[NodePresets] ⏱ unloadUserPackage took: {:.3f}s".format(time.time() - start_time))
        except Exception as e:
            print("[NodePresets] unloadUserPackage warning:", e)
        
        return True
        
    except Exception as e:
        print("[NodePresets] Package creation failed:", e)
        return False

def is_package_open_in_ui(package_path):
    """检查 package 是否已在 UI 中打开"""
    try:
        pkg_mgr = get_pkg_mgr()
        
        try:
            user_packages = pkg_mgr.getUserPackages()
            for pkg in user_packages:
                try:
                    pkg_path = pkg.getFilePath()
                    if pkg_path and os.path.normpath(pkg_path) == os.path.normpath(package_path):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        
        return False
        
    except Exception:
        return False

def load_or_create_package():
    """加载 package（使用缓存，不卸载）"""
    global _cached_package, _cached_package_path, _user_opened_package
    
    package_path = get_package_path()
    
    if not os.path.exists(package_path):
        return None
    
    # 检查缓存是否有效
    if _cached_package and _cached_package_path == package_path:
        try:
            # 验证缓存的 package 是否仍然有效
            _cached_package.getFilePath()
            print("[NodePresets] ✓ Using cached package (no load needed)")
            return _cached_package
        except Exception:
            print("[NodePresets] Cached package invalid, reloading")
            _cached_package = None
            _cached_package_path = None
    
    try:
        pkg_mgr = get_pkg_mgr()
        
        # 检查是否已经在 UI 中打开（用户手动打开的）
        start_time = time.time()
        is_open = is_package_open_in_ui(package_path)
        print("[NodePresets] ⏱ is_package_open_in_ui check took: {:.3f}s".format(time.time() - start_time))
        
        if is_open:
            print("[NodePresets] Package open in UI, using UI instance")
            _user_opened_package = True
            try:
                pkg = pkg_mgr.getUserPackageFromFilePath(package_path)
                _cached_package = pkg
                _cached_package_path = package_path
                return pkg
            except Exception:
                pass
        
        # 加载新的 package
        print("[NodePresets] Loading package...")
        start_time = time.time()
        pkg = pkg_mgr.loadUserPackage(package_path)
        print("[NodePresets] ⏱ loadUserPackage took: {:.3f}s".format(time.time() - start_time))
        
        if pkg:
            # 缓存 package，不卸载
            _cached_package = pkg
            _cached_package_path = package_path
            _user_opened_package = False
            print("[NodePresets] Package loaded and cached (will stay in memory)")
            return pkg
        
    except Exception as e:
        print("[NodePresets] Package load failed:", e)
    
    return None

def save_package(pkg, package_path=None):
    """保存 package"""
    if not pkg:
        return False
    if not package_path:
        package_path = get_package_path()
    
    try:
        start_time = time.time()
        get_pkg_mgr().savePackageAs(pkg, package_path)
        print("[NodePresets] ⏱ savePackageAs took: {:.3f}s".format(time.time() - start_time))
        return True
    except Exception as e:
        print("[NodePresets] savePackageAs failed:", e)
        return False

def close_package(pkg):
    """关闭 package（不实际卸载，保持缓存）"""
    # 不卸载，保持在内存中以提高性能
    # 只有在插件卸载时才真正卸载
    print("[NodePresets] ✓ Package kept in cache (no unload)")
    pass


def remove_graph_from_package_safe(pkg, graph):
    """从 package 中删除 graph，调试版"""
    if not pkg or not graph:
        print("[NodePresets] remove_graph_from_package_safe: pkg or graph is None")
        return False

    graph_id = None
    try:
        graph_id = graph.getIdentifier()
    except BaseException as e:
        print("[NodePresets] get graph identifier failed:", e)

    print("[NodePresets] Try remove graph:", graph_id)
    print("[NodePresets] pkg type:", type(pkg))
    print("[NodePresets] graph type:", type(graph))

    # 打印 pkg 可用方法，方便确认当前 SD API 到底叫什么
    try:
        methods = [m for m in dir(pkg) if "remove" in m.lower() or "delete" in m.lower()]
        print("[NodePresets] package remove/delete methods:", methods)
    except BaseException as e:
        print("[NodePresets] dir(pkg) failed:", e)

    # 方式 1
    try:
        if hasattr(pkg, "deleteGraph"):
            print("[NodePresets] trying pkg.deleteGraph(graph)")
            pkg.deleteGraph(graph)
            print("[NodePresets] deleteGraph success:", graph_id)
            return True
    except BaseException as e:
        print("[NodePresets] pkg.deleteGraph failed:", repr(e))

    # 方式 2
    try:
        if hasattr(pkg, "removeResource"):
            print("[NodePresets] trying pkg.removeResource(graph)")
            pkg.removeResource(graph)
            print("[NodePresets] removeResource success:", graph_id)
            return True
    except BaseException as e:
        print("[NodePresets] pkg.removeResource failed:", repr(e))

    # 方式 3
    try:
        if hasattr(pkg, "deleteResource"):
            print("[NodePresets] trying pkg.deleteResource(graph)")
            pkg.deleteResource(graph)
            print("[NodePresets] deleteResource success:", graph_id)
            return True
    except BaseException as e:
        print("[NodePresets] pkg.deleteResource failed:", repr(e))

    # 方式 4
    try:
        if hasattr(pkg, "removeChildResource"):
            print("[NodePresets] trying pkg.removeChildResource(graph)")
            pkg.removeChildResource(graph)
            print("[NodePresets] removeChildResource success:", graph_id)
            return True
    except BaseException as e:
        print("[NodePresets] pkg.removeChildResource failed:", repr(e))

    print("[NodePresets] Failed to remove graph:", graph_id)
    return False


def invalidate_package_cache():
    """使缓存失效并卸载（仅在插件卸载时调用）"""
    global _cached_package, _cached_package_path, _user_opened_package
    
    if _cached_package and not _user_opened_package:
        try:
            print("[NodePresets] Invalidating cache and unloading...")
            start_time = time.time()
            pkg_mgr = get_pkg_mgr()
            pkg_mgr.unloadUserPackage(_cached_package)
            print("[NodePresets] ⏱ unloadUserPackage took: {:.3f}s".format(time.time() - start_time))
        except Exception as e:
            print("[NodePresets] Failed to unload cached package:", e)
    
    _cached_package = None
    _cached_package_path = None
    _user_opened_package = False

def list_graph_names(pkg):
    """兼容旧代码：返回所有 preset resource id，包括 comp graph 和 function graph"""
    return [info["id"] for info in list_preset_resource_infos(pkg)]

def find_graph_by_name(pkg, name):
    """通过 identifier 或 preset_name annotation 查找 graph"""
    if not pkg or not name:
        return None

    target = str(name).strip()
    target_identifier = make_graph_identifier(target)

    try:
        children = pkg.getChildrenResources(True)
        for child in children:
            try:
                cid = child.getIdentifier()
            except BaseException:
                cid = None

            # 1. identifier 精确匹配
            if cid == target:
                return child

            # 2. 根据显示名生成的 identifier 匹配
            if cid == target_identifier:
                return child

            # 3. annotation 里的显示名匹配
            display_name = get_annotation_string(child, "preset_name", None)
            if display_name and display_name == target:
                return child

    except Exception:
        pass

    return None

def create_comp_graph(pkg, graph_name):
    try:
        from sd.api.sbs.sdsbscompgraph import SDSBSCompGraph
    except Exception:
        from sd.api import SDSBSCompGraph

    graph = SDSBSCompGraph.sNew(pkg)
    safe_identifier = make_graph_identifier(graph_name)

    set_graph_identifier_safe(graph, safe_identifier)
    set_graph_display_name_annotation(graph, safe_identifier)

    return graph


def create_function_graph(pkg, graph_name):
    """创建 function graph preset resource"""
    graph = None

    try:
        from sd.api.sbs.sdsbsfunctiongraph import SDSBSFunctionGraph
        graph = SDSBSFunctionGraph.sNew(pkg)
    except BaseException as e:
        print("[NodePresets] import SDSBSFunctionGraph path 1 failed:", e)

    if graph is None:
        try:
            from sd.api import SDSBSFunctionGraph
            graph = SDSBSFunctionGraph.sNew(pkg)
        except BaseException as e:
            print("[NodePresets] import SDSBSFunctionGraph path 2 failed:", e)

    if graph is None:
        raise RuntimeError("当前 SD API 未找到 SDSBSFunctionGraph，无法创建 Function 预设。")

    graph_identifier = make_graph_identifier(graph_name)

    set_graph_identifier_safe(graph, graph_identifier)
    set_graph_display_name_annotation(graph, graph_identifier)

    return graph


def create_preset_resource(pkg, graph_identifier, kind):
    """根据 kind 创建对应类型的 preset resource"""
    if kind == PRESET_KIND_FUNCTION_GRAPH:
        return create_function_graph(pkg, graph_identifier)

    return create_comp_graph(pkg, graph_identifier)


def is_preset_resource(resource):
    """判断是否是可作为 preset 的 resource"""
    if not resource:
        return False

    cls_name = get_resource_class_name(resource).lower()

    if "compgraph" in cls_name:
        return True

    if "function" in cls_name:
        return True

    return False


def list_preset_resource_infos(pkg):
    """列出 package 中所有 preset resource。

    返回:
        [
            {
                "id": identifier,
                "kind": comp_graph / function_graph,
                "resource": resource
            }
        ]
    """
    infos = []

    if not pkg:
        return infos

    try:
        children = pkg.getChildrenResources(True)

        for child in children:
            if not is_preset_resource(child):
                continue

            try:
                identifier = child.getIdentifier()
            except BaseException:
                identifier = None

            if not identifier:
                continue

            kind = get_preset_resource_kind(child)

            infos.append({
                "id": identifier,
                "kind": kind,
                "resource": child
            })

    except Exception as e:
        print("[NodePresets] list_preset_resource_infos failed:", e)

    return infos


def find_graph_by_name(pkg, name, allow_annotation_fallback=False):
    """通过 identifier 查找 preset resource。

    现在同时支持 comp graph 和 function graph。
    """
    if not pkg or not name:
        return None

    target = str(name).strip()

    try:
        for info in list_preset_resource_infos(pkg):
            if info["id"] == target:
                return info["resource"]

        if allow_annotation_fallback:
            for info in list_preset_resource_infos(pkg):
                child = info["resource"]

                try:
                    v = child.getAnnotationPropertyValueFromId("preset_name")
                    if v:
                        display_name = sdvalue_to_python(v)
                        if display_name and str(display_name).strip() == target:
                            return child
                except BaseException:
                    pass

    except Exception as e:
        print("[NodePresets] find_graph_by_name failed:", e)

    return None

# =============================================================================
# Type Helpers
# =============================================================================

def normalize_type_id(type_id):
    if not type_id:
        return None
    t = str(type_id).lower()
    mapping = {
        "float1": "float",
        "integer1": "int",
        "int1": "int",
        "boolean": "bool",
        "sdvaluefloat": "float",
        "sdvaluebool": "bool",
        "sdvalueint": "int",
    }
    return mapping.get(t, t)

def get_prop_first_type_id(prop):
    if not prop:
        return None
    try:
        types = prop.getTypes()
        if types:
            try:
                return normalize_type_id(types[0].getId())
            except BaseException:
                return normalize_type_id(str(types[0]))
    except BaseException:
        pass
    return None

def get_prop_first_type(prop):
    if not prop:
        return None
    try:
        types = prop.getTypes()
        if types:
            return types[0]
    except BaseException:
        pass
    try:
        t = prop.getType()
        if t:
            return t
    except BaseException:
        pass
    return None

def find_sdtype_object_by_id(nodes, type_id):
    wanted = normalize_type_id(type_id)
    if not wanted:
        return None
    
    for node in nodes or []:
        if not node:
            continue
        for cat in [SDPropertyCategory.Input, SDPropertyCategory.Output, SDPropertyCategory.Annotation]:
            try:
                props = node.getProperties(cat)
                for prop in props:
                    try:
                        types = prop.getTypes()
                        for t in types:
                            try:
                                tid = normalize_type_id(t.getId())
                            except BaseException:
                                tid = normalize_type_id(str(t))
                            if tid == wanted:
                                return t
                    except BaseException:
                        pass
            except BaseException:
                pass
    return None

def get_type_id_from_function_get_def(def_id):
    if not def_id:
        return None
    d = def_id.lower()
    
    if "get_bool" in d:
        return "bool"
    if "get_float1" in d:
        return "float"
    if "get_float2" in d:
        return "float2"
    if "get_float3" in d:
        return "float3"
    if "get_float4" in d:
        return "float4"
    if "get_integer1" in d or "get_int1" in d:
        return "int"
    if "get_integer2" in d or "get_int2" in d:
        return "int2"
    if "get_integer3" in d or "get_int3" in d:
        return "int3"
    if "get_integer4" in d or "get_int4" in d:
        return "int4"
    return None

def is_builtin_function_variable(name):
    if not name:
        return True
    builtins = {"$pos", "$size", "$sizelog2", "$pixelsize", "$normal", "$randomseed", "$time"}
    return name in builtins

def create_default_sdvalue_for_type(type_id):
    if not type_id:
        return None
    
    t = normalize_type_id(type_id)
    
    try:
        from sd.api.sdvaluebool import SDValueBool
        from sd.api.sdvalueint import SDValueInt
        from sd.api.sdvaluefloat import SDValueFloat
        from sd.api.sdvaluefloat2 import SDValueFloat2
        from sd.api.sdvaluefloat3 import SDValueFloat3
        from sd.api.sdvaluefloat4 import SDValueFloat4
        from sd.api.sdvaluestring import SDValueString
        
        defaults = {
            "bool": lambda: SDValueBool.sNew(False),
            "int": lambda: SDValueInt.sNew(0),
            "float": lambda: SDValueFloat.sNew(0.0),
            "float2": lambda: SDValueFloat2.sNew(0.0, 0.0),
            "float3": lambda: SDValueFloat3.sNew(0.0, 0.0, 0.0),
            "float4": lambda: SDValueFloat4.sNew(0.0, 0.0, 0.0, 0.0),
            "string": lambda: SDValueString.sNew(""),
        }
        
        creator = defaults.get(t)
        if creator:
            return creator()
    except BaseException:
        pass
    
    return None

# =============================================================================
# Property Value Helpers
# =============================================================================

def get_property_value_safe(node, prop, cat=None):
    if not node or not prop:
        return None
    
    try:
        prop_id = prop.getId()
    except BaseException:
        prop_id = None
    
    value = None
    
    if cat == SDPropertyCategory.Input and prop_id:
        try:
            value = node.getInputPropertyValueFromId(prop_id)
        except BaseException:
            pass
    
    if value is None and cat == SDPropertyCategory.Annotation and prop_id:
        try:
            value = node.getAnnotationPropertyValueFromId(prop_id)
        except BaseException:
            pass
    
    if value is None:
        try:
            value = node.getPropertyValue(prop)
        except BaseException:
            pass
    
    return value

def get_function_property_value_safe(node, prop):
    if not node or not prop:
        return None
    
    try:
        return node.getPropertyValue(prop)
    except BaseException:
        pass
    
    try:
        prop_id = prop.getId()
        if prop_id:
            return node.getInputPropertyValueFromId(prop_id)
    except BaseException:
        pass
    
    return None

def set_input_value_safe(node, prop_id, value):
    if not node or not prop_id or value is None:
        return False
    
    try:
        node.setInputPropertyValueFromId(prop_id, value)
        return True
    except BaseException:
        pass
    
    try:
        prop = node.getPropertyFromId(prop_id, SDPropertyCategory.Input)
        if prop:
            node.setPropertyValue(prop, value)
            return True
    except BaseException:
        pass
    
    return False

def set_function_input_value_safe(node, prop_id, value):
    if not node or not prop_id or value is None:
        return False
    
    try:
        prop = node.getPropertyFromId(prop_id, SDPropertyCategory.Input)
        if prop:
            node.setPropertyValue(prop, value)
            return True
    except BaseException:
        pass
    
    try:
        node.setInputPropertyValueFromId(prop_id, value)
        return True
    except BaseException:
        pass
    
    return False

def set_property_value_safe(node, prop, prop_id, cat, value):
    if not node or not prop_id or value is None:
        return False
    
    if cat == SDPropertyCategory.Input:
        return set_input_value_safe(node, prop_id, value)
    
    if cat == SDPropertyCategory.Annotation:
        try:
            node.setAnnotationPropertyValueFromId(prop_id, value)
            return True
        except BaseException:
            pass
        
        try:
            if prop:
                node.setPropertyValue(prop, value)
                return True
        except BaseException:
            pass
        
        return False
    
    try:
        if prop:
            node.setPropertyValue(prop, value)
            return True
    except BaseException:
        pass
    
    return False

def get_node_annotation_value(node, prop_id):
    if not node or not prop_id:
        return None
    
    try:
        value = node.getAnnotationPropertyValueFromId(prop_id)
        if value is not None:
            return sdvalue_to_python(value)
    except BaseException:
        pass
    
    try:
        prop = node.getPropertyFromId(prop_id, SDPropertyCategory.Annotation)
        if prop:
            value = node.getPropertyValue(prop)
            return sdvalue_to_python(value)
    except BaseException:
        pass
    
    return None

# =============================================================================
# Input Value Node Helpers
# =============================================================================

def is_input_value_node(node):
    def_id = get_node_def_id(node) or ""
    return "sbs::compositing::input_value" in def_id.lower()

def get_input_value_identifier(node):
    """获取 Input Value 节点的公开 identifier，增强版"""
    if not is_input_value_node(node):
        return None

    # 常见 annotation id
    candidates = [
        "identifier", "Identifier",
        "id", "ID",
        "uid", "UID",
        "input", "Input",
        "input_identifier",
        "InputIdentifier",
        "name", "Name"
    ]

    for cid in candidates:
        v = get_node_annotation_value(node, cid)
        if v is not None:
            s = str(v).strip()
            if s:
                return s

    # fallback 1: label
    try:
        label = node.getLabel()
        if label:
            label = str(label).strip()
            if label:
                return label
    except BaseException:
        pass

    # fallback 2: identifier
    try:
        nid = node.getIdentifier()
        if nid:
            return str(nid).strip()
    except BaseException:
        pass

    return None

def get_input_value_output_value_and_type(node):
    """读取 Input Value 节点的 Output 当前值和类型"""
    if not is_input_value_node(node):
        return None, None
    
    try:
        output_props = node.getProperties(SDPropertyCategory.Output)
        for p in output_props:
            value = get_property_value_safe(node, p, SDPropertyCategory.Output)
            type_id = get_prop_first_type_id(p)
            if type_id:
                return value, normalize_type_id(type_id)
    except BaseException:
        pass
    
    return None, None

def infer_input_value_type_from_node(node, type_search_nodes=None, all_nodes=None):
    """从 Input Value 节点推断类型"""
    if not node:
        return None
    
    # 1. 从 Output 类型推断（最可靠）
    try:
        output_props = node.getProperties(SDPropertyCategory.Output)
        for p in output_props:
            type_id = get_prop_first_type_id(p)
            if type_id:
                t = normalize_type_id(type_id)
                if t and t.lower() not in ["sdtypetexture", "texture"]:
                    print("[NodePresets] Inferred Input Value type from output:", t)
                    return t
    except BaseException:
        pass
    
    # 2. 从 annotation 推断
    for ann_id in ["type", "Type", "valuetype", "value_type", "editor", "Editor"]:
        v = get_node_annotation_value(node, ann_id)
        if v:
            s = str(v).lower()
            if "bool" in s:
                return "bool"
            if "float4" in s:
                return "float4"
            if "float3" in s:
                return "float3"
            if "float2" in s:
                return "float2"
            if "float" in s:
                return "float"
            if "int4" in s:
                return "int4"
            if "int3" in s:
                return "int3"
            if "int2" in s:
                return "int2"
            if "int" in s or "integer" in s:
                return "int"
    
    # 3. 从连接推断
    type_id = infer_input_value_type_from_connections(node, all_nodes or type_search_nodes or [])
    if type_id:
        return normalize_type_id(type_id)
    
    return None

def infer_input_value_type_from_connections(input_node, all_nodes):
    """从 Input Value 节点的输出连接推断类型"""
    if not input_node or not all_nodes:
        return None
    
    try:
        input_node_id = input_node.getIdentifier()
    except BaseException:
        input_node_id = None
    
    for dst_node in all_nodes:
        if not dst_node:
            continue
        
        try:
            dst_input_props = dst_node.getProperties(SDPropertyCategory.Input)
        except BaseException:
            continue
        
        for dst_input_prop in dst_input_props:
            try:
                if not dst_input_prop.isConnectable():
                    continue
            except BaseException:
                continue
            
            try:
                conns = dst_node.getPropertyConnections(dst_input_prop)
            except BaseException:
                continue
            
            for conn in conns:
                try:
                    src_node = conn.getInputPropertyNode()
                except BaseException:
                    continue
                
                if not src_node:
                    continue
                
                # 检查是否是同一个节点
                is_same = False
                try:
                    is_same = (src_node.getIdentifier() == input_node_id)
                except BaseException:
                    is_same = (src_node is input_node)
                
                if not is_same:
                    continue
                
                # 获取目标端口类型
                try:
                    target_prop = conn.getOutputProperty()
                except BaseException:
                    target_prop = dst_input_prop
                
                type_id = get_prop_first_type_id(target_prop)
                if type_id:
                    print("[NodePresets] Inferred Input Value type from connection:", type_id)
                    return type_id
    
    return None

def copy_input_value_node_data(src_node, dst_node):
    """复制 Input Value 节点数据"""
    if not is_input_value_node(src_node) or not is_input_value_node(dst_node):
        return
    
    print("[NodePresets] Copying Input Value node data")
    
    # 1. 复制 Annotation 属性
    try:
        annot_props = src_node.getProperties(SDPropertyCategory.Annotation)
        for src_prop in annot_props:
            try:
                prop_id = src_prop.getId()
                if not prop_id:
                    continue
                
                value = get_property_value_safe(src_node, src_prop, SDPropertyCategory.Annotation)
                if value is None:
                    continue
                
                dst_prop = dst_node.getPropertyFromId(prop_id, SDPropertyCategory.Annotation)
                set_property_value_safe(dst_node, dst_prop, prop_id, SDPropertyCategory.Annotation, value)
            except BaseException:
                pass
    except BaseException:
        pass
    
    # 2. 复制 Input 属性
    try:
        input_props = src_node.getProperties(SDPropertyCategory.Input)
        for src_prop in input_props:
            try:
                prop_id = src_prop.getId()
                if not prop_id:
                    continue
                
                if src_prop.isReadOnly():
                    continue
                
                value = get_property_value_safe(src_node, src_prop, SDPropertyCategory.Input)
                if value is None:
                    continue
                
                dst_prop = dst_node.getPropertyFromId(prop_id, SDPropertyCategory.Input)
                set_property_value_safe(dst_node, dst_prop, prop_id, SDPropertyCategory.Input, value)
            except BaseException:
                pass
    except BaseException:
        pass
    
    # 3. 复制 Output 值
    src_value, src_value_type = get_input_value_output_value_and_type(src_node)
    if src_value is not None:
        try:
            input_props = dst_node.getProperties(SDPropertyCategory.Input)
            for prop in input_props:
                try:
                    prop_id = prop.getId()
                    if prop_id and "value" in prop_id.lower():
                        set_input_value_safe(dst_node, prop_id, src_value)
                        print("[NodePresets] Set input value via:", prop_id)
                        break
                except BaseException:
                    pass
        except BaseException:
            pass
    
    # 4. 再次复制 annotation 确保不被覆盖
    copy_annotation_properties(src_node, dst_node)

    # 5. 复制 inheritance method（Input Value 节点也可能有 Base Parameters）
    try:
        input_props = src_node.getProperties(SDPropertyCategory.Input)
        for src_prop in input_props:
            try:
                prop_id = src_prop.getId()
                if not prop_id:
                    continue

                dst_prop = dst_node.getPropertyFromId(prop_id, SDPropertyCategory.Input)
                if not dst_prop:
                    continue

                copy_property_inheritance_method_safe(
                    src_node=src_node,
                    dst_node=dst_node,
                    src_prop=src_prop,
                    dst_prop=dst_prop,
                    prop_id=prop_id
                )
            except BaseException:
                pass
    except BaseException:
        pass

# =============================================================================
# Input Value Params Display
# =============================================================================

def extract_input_value_params_from_nodes(nodes):
    """从节点列表中提取所有 Input Value 节点的参数信息，稳定排序，避免漏显示"""
    params = []
    seen = set()

    input_nodes = []
    for node in nodes or []:
        if is_input_value_node(node):
            x, y = get_node_position(node)
            input_nodes.append((x, y, node))

    # 按节点位置排序，UI 上顺序更稳定
    input_nodes.sort(key=lambda item: (item[1], item[0]))

    for _, _, node in input_nodes:
        ident = get_input_value_identifier(node)
        if not ident:
            print("[NodePresets] Input Value skipped: no identifier")
            continue

        # 如果有重复 identifier，不直接跳过，而是追加序号，避免 UI 少一个
        display_ident = ident
        if display_ident in seen:
            idx = 2
            while "{}_{}".format(ident, idx) in seen:
                idx += 1
            display_ident = "{}_{}".format(ident, idx)

        seen.add(display_ident)

        actual_value, value_type = get_input_value_output_value_and_type(node)
        formatted_value = format_value_for_display(actual_value)

        params.append({
            'identifier': display_ident,
            'value': formatted_value,
            'raw_value': actual_value,
            'type': value_type or ""
        })

    print("[NodePresets] extract_input_value_params_from_nodes result:", params)
    return params

def _format_number_for_ui(v):
    try:
        f = float(v)
        s = "{:.6f}".format(f).rstrip("0").rstrip(".")
        return s if s else "0"
    except BaseException:
        return str(v)


def _parse_xyzw_string_to_tuple_text(text):
    """
    把这种字符串：
        x: 1024.000000, y: 0.630000
        x: 1.000000, y: 0.640000, z: 0.290000, w: 0.850000

    转成：
        (1024, 0.63)
        (1, 0.64, 0.29, 0.85)
    """
    if text is None:
        return None

    import re

    s = str(text)

    matches = re.findall(
        r"\b([xyzw])\s*:\s*(-?\d+(?:\.\d+)?)",
        s,
        re.IGNORECASE
    )

    if not matches:
        return None

    order = ["x", "y", "z", "w"]
    values = {}

    for axis, value in matches:
        values[axis.lower()] = value

    result = []
    for axis in order:
        if axis in values:
            result.append(_format_number_for_ui(values[axis]))

    if not result:
        return None

    return "({})".format(", ".join(result))


def format_value_for_display(value):
    """格式化值用于显示"""
    if value is None:
        return "None"

    py_value = sdvalue_to_python(value)

    if py_value is None:
        return "None"

    if isinstance(py_value, bool):
        return str(py_value)

    if isinstance(py_value, (int, float)):
        return "({})".format(_format_number_for_ui(py_value))

    if isinstance(py_value, (tuple, list)):
        return "({})".format(", ".join(_format_number_for_ui(v) for v in py_value))

    # 关键：处理 Substance 返回的 x: y: z: w: 字符串
    vector_text = _parse_xyzw_string_to_tuple_text(py_value)
    if vector_text:
        return vector_text

    s = str(py_value)

    vector_text = _parse_xyzw_string_to_tuple_text(s)
    if vector_text:
        return vector_text

    if len(s) > 30:
        return s[:30] + "..."

    return s

def format_input_value_params_summary(params):
    """格式化 Input Value 参数摘要"""
    if not params:
        return ""

    parts = []
    for p in params[:3]:
        parts.append("{} {}".format(p['identifier'], p['value']))

    if len(params) > 3:
        parts.append("+{}".format(len(params) - 3))

    return " ".join(parts)

def format_input_value_params_full(params):
    """格式化完整的 Input Value 参数列表（简化版本）"""
    if not params:
        return ""
    
    lines = []
    for p in params:
        lines.append("  • {}: {}".format(p['identifier'], p['value']))
    
    return "\n".join(lines)

def parse_params_summary_to_list(params_summary):
    """解析旧格式参数摘要，兼容 x/y/z/w 格式"""
    if not params_summary:
        return []

    print("[NodePresets] Parsing params_summary:", params_summary)

    text = str(params_summary).strip()

    # 如果 description 里实际存的是 JSON
    if text.startswith(PARAMS_JSON_PREFIX):
        text = text[len(PARAMS_JSON_PREFIX):].strip()

    if text.startswith("["):
        try:
            import json
            data = json.loads(text)
            result = []
            for item in data:
                if isinstance(item, dict):
                    result.append({
                        "identifier": str(item.get("identifier", "")),
                        "value": str(item.get("value", ""))
                    })
            return result
        except BaseException as e:
            print("[NodePresets] JSON parse in description failed:", e)

    tokens = text.split()
    result = []

    i = 0
    n = len(tokens)

    while i < n:
        identifier = tokens[i].strip()

        if not identifier or identifier.startswith("+"):
            break

        i += 1

        if i >= n:
            result.append({
                "identifier": identifier,
                "value": ""
            })
            break

        # 情况 1：括号值，例如 (1024, 0.63)
        if tokens[i].startswith("("):
            value_parts = []

            while i < n:
                value_parts.append(tokens[i])
                if tokens[i].endswith(")"):
                    i += 1
                    break
                i += 1

            value = " ".join(value_parts)

            result.append({
                "identifier": identifier,
                "value": value
            })
            continue

        # 情况 2：x: 1024, y: 0.63, z:..., w:...
        if tokens[i].lower().rstrip(":") in ["x", "y", "z", "w"]:
            values = []
            axes = ["x", "y", "z", "w"]

            while i < n:
                axis_token = tokens[i].lower().rstrip(":")
                if axis_token not in axes:
                    break

                i += 1

                if i >= n:
                    break

                number_token = tokens[i].strip().rstrip(",")
                values.append(_format_number_for_ui(number_token))

                i += 1

            value = "({})".format(", ".join(values))

            result.append({
                "identifier": identifier,
                "value": value
            })
            continue

        # 情况 3：普通单值，例如 False / True / None
        value = tokens[i].strip()
        i += 1

        result.append({
            "identifier": identifier,
            "value": value
        })

    print("[NodePresets] Parsed params:", result)
    return result

# =============================================================================
# Graph Input Helpers
# =============================================================================

def graph_has_property_api(graph):
    if not graph:
        return False
    return (hasattr(graph, "newProperty") and 
            hasattr(graph, "getProperties") and 
            hasattr(graph, "getPropertyFromId"))

def get_graph_property_annotation_value(graph, prop, ann_id):
    """从 Graph Property 读取 annotation"""
    if not graph or not prop or not ann_id:
        return None
    
    try:
        prop_id = prop.getId()
    except BaseException:
        prop_id = None
    
    # 1. 从 SDProperty 自己的 annotation API
    try:
        if hasattr(prop, "getAnnotationPropertyValueFromId"):
            value = prop.getAnnotationPropertyValueFromId(ann_id)
            if value is not None:
                return sdvalue_to_python(value)
    except BaseException:
        pass
    
    # 2. 从 graph 上的 getter 变体
    getter_candidates = [
        "getPropertyAnnotationValueFromId",
        "getPropertyAnnotationPropertyValueFromId",
        "getInputPropertyAnnotationValueFromId",
        "getAnnotationPropertyValueFromId",
    ]
    
    for getter_name in getter_candidates:
        if not hasattr(graph, getter_name):
            continue
        
        getter = getattr(graph, getter_name)
        call_variants = [
            (prop, ann_id),
            (prop_id, ann_id),
            (prop, ann_id, SDPropertyCategory.Input),
            (prop_id, ann_id, SDPropertyCategory.Input),
        ]
        
        for args in call_variants:
            try:
                value = getter(*args)
                if value is not None:
                    return sdvalue_to_python(value)
            except BaseException:
                pass
    
    return None

def get_graph_input_public_identifier(graph, prop):
    """获取 Graph Input 的 public identifier"""
    if not graph or not prop:
        return None
    
    for ann_id in ["identifier", "Identifier", "id", "uid"]:
        v = get_graph_property_annotation_value(graph, prop, ann_id)
        if v:
            return str(v)
    
    try:
        return prop.getId()
    except BaseException:
        return None

def find_graph_input_property(graph, prop_id):
    """在 Graph Input Category 中查找指定 property"""
    if not graph or not prop_id:
        return None
    
    target_names = {
        str(prop_id),
        str(prop_id).lstrip("#"),
        "#" + str(prop_id).lstrip("#"),
    }
    
    # 1. 直接 getPropertyFromId
    for cid in target_names:
        try:
            prop = graph.getPropertyFromId(cid, SDPropertyCategory.Input)
            if prop:
                return prop
        except BaseException:
            pass
    
    # 2. 遍历 Graph Input Properties
    try:
        props = list(graph.getProperties(SDPropertyCategory.Input))
    except BaseException:
        props = []
    
    for prop in props:
        try:
            pid = prop.getId()
        except BaseException:
            pid = None
        
        public_id = get_graph_input_public_identifier(graph, prop)
        
        candidates = set()
        if pid:
            candidates.add(str(pid))
            candidates.add(str(pid).lstrip("#"))
            candidates.add("#" + str(pid).lstrip("#"))
        
        if public_id:
            candidates.add(str(public_id))
            candidates.add(str(public_id).lstrip("#"))
            candidates.add("#" + str(public_id).lstrip("#"))
        
        if candidates.intersection(target_names):
            return prop
    
    return None

def try_get_property_annotation_value(src_graph, src_prop, ann_id):
    """尝试获取 property annotation 值"""
    if not src_graph or not src_prop or not ann_id:
        return None
    
    try:
        prop_id = src_prop.getId()
    except BaseException:
        prop_id = None
    
    # 从 property 自己
    try:
        if hasattr(src_prop, "getAnnotationPropertyValueFromId"):
            value = src_prop.getAnnotationPropertyValueFromId(ann_id)
            if value is not None:
                return value
    except BaseException:
        pass
    
    # 从 graph
    getter_candidates = [
        "getPropertyAnnotationValueFromId",
        "getPropertyAnnotationPropertyValueFromId",
        "getInputPropertyAnnotationValueFromId",
        "getAnnotationPropertyValueFromId",
    ]
    
    for getter_name in getter_candidates:
        if not hasattr(src_graph, getter_name):
            continue
        
        getter = getattr(src_graph, getter_name)
        call_variants = [
            (src_prop, ann_id),
            (prop_id, ann_id),
            (src_prop, ann_id, SDPropertyCategory.Input),
            (prop_id, ann_id, SDPropertyCategory.Input),
        ]
        
        for args in call_variants:
            try:
                value = getter(*args)
                if value is not None:
                    return value
            except BaseException:
                pass
    
    return None

def try_set_property_annotation_value(dst_graph, dst_prop, ann_id, value):
    """尝试设置 property annotation 值"""
    if not dst_graph or not dst_prop or not ann_id or value is None:
        return False
    
    try:
        prop_id = dst_prop.getId()
    except BaseException:
        prop_id = None
    
    # 从 property 自己
    try:
        if hasattr(dst_prop, "setAnnotationPropertyValueFromId"):
            dst_prop.setAnnotationPropertyValueFromId(ann_id, value)
            return True
    except BaseException:
        pass
    
    # 从 graph
    setter_candidates = [
        "setPropertyAnnotationValueFromId",
        "setPropertyAnnotationPropertyValueFromId",
        "setInputPropertyAnnotationValueFromId",
        "setAnnotationPropertyValueFromId",
    ]
    
    for setter_name in setter_candidates:
        if not hasattr(dst_graph, setter_name):
            continue
        
        setter = getattr(dst_graph, setter_name)
        call_variants = [
            (dst_prop, ann_id, value),
            (prop_id, ann_id, value),
            (dst_prop, ann_id, SDPropertyCategory.Input, value),
            (prop_id, ann_id, SDPropertyCategory.Input, value),
        ]
        
        for args in call_variants:
            try:
                setter(*args)
                return True
            except BaseException:
                pass
    
    return False

def copy_graph_property_default_value(src_graph, dst_graph, src_prop, dst_prop):
    """复制 Graph Input 默认值"""
    if not src_graph or not dst_graph or not src_prop or not dst_prop:
        return False
    
    try:
        prop_id = src_prop.getId()
    except BaseException:
        prop_id = None
    
    value = None
    
    try:
        value = src_graph.getPropertyValue(src_prop)
    except BaseException:
        pass
    
    if value is None and prop_id:
        try:
            value = src_graph.getInputPropertyValueFromId(prop_id)
        except BaseException:
            pass
    
    if value is None:
        return False
    
    try:
        dst_graph.setPropertyValue(dst_prop, value)
        print("[NodePresets] Copied graph input default:", prop_id, debug_value(value))
        return True
    except BaseException:
        pass
    
    if prop_id:
        try:
            dst_graph.setInputPropertyValueFromId(prop_id, value)
            print("[NodePresets] Copied graph input default by id:", prop_id, debug_value(value))
            return True
        except BaseException:
            pass
    
    return False

GRAPH_INPUT_SPECIFIC_PARAMETER_IDS = {
    "identifier": ["identifier", "Identifier", "id", "uid"],
    "label": ["label", "Label", "gui_label", "guilabel"],
    "group": ["group", "Group", "gui_group", "guigroup"],
    "description": ["description", "Description", "desc", "tooltip", "ToolTip"],
    "editor": ["editor", "Editor", "widget", "Widget", "defaultwidget", "default_widget"],
    "default": ["default", "Default", "default_value", "defaultvalue", "DefaultValue"],
    "label_true": ["label_true", "labeltrue", "LabelTrue", "true_label", "truelabel", "TrueLabel"],
    "label_false": ["label_false", "labelfalse", "LabelFalse", "false_label", "falselabel", "FalseLabel"],
    "userdata": ["userdata", "user_data", "UserData", "userData"],
    "visible_if": ["visible_if", "visibleif", "VisibleIf", "visibleIf"],
    "usages": ["usages", "usage", "Usages", "Usage"],
    "min": ["min", "Min", "minvalue", "min_value", "MinValue"],
    "max": ["max", "Max", "maxvalue", "max_value", "MaxValue"],
    "clamp": ["clamp", "Clamp"],
}

def flatten_specific_parameter_ids():
    ids = []
    for _, values in GRAPH_INPUT_SPECIFIC_PARAMETER_IDS.items():
        for v in values:
            if v not in ids:
                ids.append(v)
    return ids

def copy_graph_property_specific_annotations(src_graph, dst_graph, src_prop, dst_prop):
    """复制 Graph Input 的 specific parameters"""
    if not src_graph or not dst_graph or not src_prop or not dst_prop:
        return
    
    ann_ids = flatten_specific_parameter_ids()
    
    for ann_id in ann_ids:
        value = try_get_property_annotation_value(src_graph, src_prop, ann_id)
        if value is None:
            continue
        
        ok = try_set_property_annotation_value(dst_graph, dst_prop, ann_id, value)
        if ok:
            print("[NodePresets] Copied graph input annotation {}: {}".format(ann_id, debug_value(value)))

def copy_graph_input_specific_parameters(src_graph, dst_graph, src_prop, dst_prop):
    """复制 Graph Input 的所有 specific parameters"""
    if not src_graph or not dst_graph or not src_prop or not dst_prop:
        return
    
    try:
        prop_id = src_prop.getId()
    except BaseException:
        prop_id = "?"
    
    print("[NodePresets] Copying graph input specific parameters:", prop_id)
    
    copy_graph_property_default_value(src_graph, dst_graph, src_prop, dst_prop)
    copy_graph_property_specific_annotations(src_graph, dst_graph, src_prop, dst_prop)

def create_graph_input_property(graph, prop_id, type_id=None, type_search_nodes=None, src_prop=None):
    """创建 Graph Input Property"""
    if not graph or not prop_id:
        return False
    
    if not graph_has_property_api(graph):
        print("[NodePresets] Graph has no property API, cannot create graph input:", prop_id)
        return False
    
    expected = normalize_type_id(type_id) if type_id else None
    
    # 检查是否已存在
    try:
        existing = graph.getPropertyFromId(prop_id, SDPropertyCategory.Input)
    except BaseException:
        existing = None
    
    if existing:
        current_type = get_prop_first_type_id(existing)
        print("[NodePresets] Graph input exists: {} type={}".format(prop_id, current_type))
        
        if not expected or current_type == expected:
            return True
        
        # 类型不匹配，删除重建
        print("[NodePresets] Graph input type mismatch: {} current={} expected={}".format(
            prop_id, current_type, expected))
        
        try:
            if hasattr(graph, "deleteProperty"):
                graph.deleteProperty(existing)
                print("[NodePresets] Deleted graph input for recreate:", prop_id)
            else:
                return False
        except BaseException as e:
            print("[NodePresets] Delete graph input failed {}: {}".format(prop_id, e))
            return False
    
    # 获取 SDType 对象
    sd_type = None
    
    if src_prop:
        sd_type = get_prop_first_type(src_prop)
        if not expected:
            expected = get_prop_first_type_id(src_prop)
    
    if not sd_type and expected:
        sd_type = find_sdtype_object_by_id(type_search_nodes or [], expected)
    
    if not sd_type:
        print("[NodePresets] Cannot find SDType for graph input:", prop_id, expected)
        return False
    
    print("[NodePresets] Creating graph input property: {} type={}".format(prop_id, expected))
    
    # 尝试创建
    variants = [
        (prop_id, sd_type, SDPropertyCategory.Input),
        (prop_id, SDPropertyCategory.Input, sd_type),
    ]
    
    for args in variants:
        try:
            result = graph.newProperty(*args)
            print("[NodePresets] graph.newProperty{} -> {}".format(args, result))
            
            # 验证创建成功
            try:
                created = graph.getPropertyFromId(prop_id, SDPropertyCategory.Input)
            except BaseException:
                created = None
            
            if created:
                created_type = get_prop_first_type_id(created)
                print("[NodePresets] Verified graph input: {} type={}".format(prop_id, created_type))
                
                if not expected or created_type == expected:
                    return True
        
        except BaseException as e:
            print("[NodePresets] graph.newProperty{} failed: {}".format(args, e))
    
    print("[NodePresets] FAILED to create graph input:", prop_id, expected)
    return False

def create_or_copy_graph_input_from_source(src_graph, dst_graph, prop_id, type_search_nodes=None):
    """从源 Graph 创建并复制 Graph Input"""
    if not src_graph or not dst_graph or not prop_id:
        return False
    
    src_prop = find_graph_input_property(src_graph, prop_id)
    if not src_prop:
        print("[NodePresets] Source graph input not found:", prop_id)
        return False
    
    try:
        real_prop_id = src_prop.getId()
    except BaseException:
        real_prop_id = prop_id
    
    type_id = get_prop_first_type_id(src_prop)
    if not type_id:
        print("[NodePresets] Source graph input has no type:", real_prop_id)
        return False
    
    print("[NodePresets] Create/copy graph input from source: {} type={}".format(real_prop_id, type_id))
    
    # 创建 Graph Input
    ok = create_graph_input_property(
        graph=dst_graph,
        prop_id=real_prop_id,
        type_id=type_id,
        type_search_nodes=type_search_nodes,
        src_prop=src_prop
    )
    
    if not ok:
        print("[NodePresets] Create graph input failed:", real_prop_id)
        return False
    
    # 获取目标 property
    dst_prop = find_graph_input_property(dst_graph, real_prop_id)
    if not dst_prop:
        print("[NodePresets] Target graph input not found after create:", real_prop_id)
        return False
    
    # 复制 specific parameters
    copy_graph_input_specific_parameters(
        src_graph=src_graph,
        dst_graph=dst_graph,
        src_prop=src_prop,
        dst_prop=dst_prop
    )
    
    return True

def delete_graph_input_property_graph_if_exists(graph, prop_id):
    """删除 Graph Input 可能带有的 property graph（function graph）
    Input Value 不需要 function graph"""
    if not graph or not prop_id:
        return
    
    prop = find_graph_input_property(graph, prop_id)
    if not prop:
        return
    
    try:
        prop_graph = graph.getPropertyGraph(prop)
    except BaseException:
        prop_graph = None
    
    if prop_graph:
        try:
            if hasattr(graph, "deleteProperty"):
                graph.deleteProperty(prop)
                print("[NodePresets] Deleted graph input property graph:", prop_id)
        except BaseException as e:
            print("[NodePresets] Delete graph input property graph failed:", e)

# =============================================================================
# Function Graph Variable Inference
# =============================================================================

def get_function_constant_string(node):
    """从 Function 节点获取常量字符串值"""
    try:
        prop = node.getPropertyFromId("__constant__", SDPropertyCategory.Input)
    except BaseException:
        prop = None
    
    if not prop:
        return None
    
    value = get_function_property_value_safe(node, prop)
    
    try:
        return value.get() if value and hasattr(value, "get") else value
    except BaseException:
        return value 
    
def collect_get_variable_types_from_function_graph(func_graph):
    """从 Function Graph 中收集 Get 变量的类型"""
    result = {}
    set_names = set()
    
    if not func_graph:
        return result
    
    try:
        nodes = list(func_graph.getNodes())
    except BaseException:
        return result
    
    # 先收集所有 Set 变量名（这些是局部变量，不需要外部输入）
    for node in nodes:
        def_id = get_node_def_id(node) or ""
        if "sbs::function::set" not in def_id.lower():
            continue
        
        name = get_function_constant_string(node)
        if name:
            set_names.add(name)
    
    # 收集 Get 变量类型（排除 Set 的局部变量）
    for node in nodes:
        def_id = get_node_def_id(node) or ""
        if "sbs::function::get" not in def_id.lower():
            continue
        
        type_id = get_type_id_from_function_get_def(def_id)
        if not type_id:
            continue
        
        var_name = get_function_constant_string(node)
        if not var_name:
            continue
        
        if is_builtin_function_variable(var_name):
            continue
        
        if var_name in set_names:
            continue
        
        if var_name in result and result[var_name] != type_id:
            print("[NodePresets] WARNING variable type conflict: {} old={} new={}".format(
                var_name, result[var_name], type_id))
        
        result[var_name] = type_id
    
    return result  

def collect_dynamic_input_type_hints_from_node(node):
    """从节点的 property graph 中收集动态输入类型提示"""
    hints = {}
    
    if not node:
        return hints
    
    try:
        props = node.getProperties(SDPropertyCategory.Input)
    except BaseException:
        props = []
    
    for prop in props:
        try:
            prop_id = prop.getId()
        except BaseException:
            continue
        
        try:
            prop_graph = node.getPropertyGraph(prop)
        except BaseException:
            prop_graph = None
        
        if not prop_graph:
            continue
        
        graph_hints = collect_get_variable_types_from_function_graph(prop_graph)
        
        if graph_hints:
            print("[NodePresets] Dynamic input type hints from {}: {}".format(prop_id, graph_hints))
            hints.update(graph_hints)
    
    return hints             

def collect_graph_input_hints_from_nodes(nodes):
    """从多个节点收集 graph input 类型提示"""
    hints = {}
    
    for node in nodes or []:
        node_hints = collect_dynamic_input_type_hints_from_node(node)
        
        for name, type_id in node_hints.items():
            if not name:
                continue
            
            if name.startswith("#"):
                continue
            
            if is_builtin_function_variable(name):
                continue
            
            if name in hints and hints[name] != type_id:
                print("[NodePresets] WARNING graph input type conflict: {} old={} new={}".format(
                    name, hints[name], type_id))
            
            hints[name] = type_id
    
    return hints

def same_sd_object(a, b):
    """判断两个 SD 对象是否相同"""
    if not a or not b:
        return False
    
    try:
        return a.getIdentifier() == b.getIdentifier()
    except BaseException:
        return a is b

def ensure_graph_inputs_from_function_hints_only(src_nodes, dst_graph, src_graph=None, type_search_nodes=None):
    """只处理 Function Graph 中 Get 非 # 变量
    例如：time11 -> float
    不处理 Input Value 节点 identifier（tesss, offf）"""
    
    hints = collect_graph_input_hints_from_nodes(src_nodes)
    
    if not hints:
        return
    
    print("[NodePresets] Ensure graph inputs from function hints only:", hints)
    
    for name, type_id in hints.items():
        if not name:
            continue
        
        if name.startswith("#"):
            continue
        
        if is_builtin_function_variable(name):
            continue
        
        print("[NodePresets] Ensure function/global graph input:", name, type_id)
        
        # 如果源 graph 有对应的 graph input，从源复制
        if src_graph and find_graph_input_property(src_graph, name):
            ok = create_or_copy_graph_input_from_source(
                src_graph=src_graph,
                dst_graph=dst_graph,
                prop_id=name,
                type_search_nodes=type_search_nodes or src_nodes
            )
            
            if ok:
                continue
        
        # 否则创建新的
        ok = create_graph_input_property(
            graph=dst_graph,
            prop_id=name,
            type_id=type_id,
            type_search_nodes=type_search_nodes or src_nodes
        )
        
        if ok:
            # 设置默认值
            value = create_default_sdvalue_for_type(type_id)
            if value:
                prop = find_graph_input_property(dst_graph, name)
                if prop:
                    try:
                        dst_graph.setPropertyValue(prop, value)
                        print("[NodePresets] Set graph input default:", name, debug_value(value))
                    except BaseException:
                        pass
            
            # 删除可能存在的 property graph
            delete_graph_input_property_graph_if_exists(dst_graph, name)

# =============================================================================
# Dynamic Node Value Input
# =============================================================================

def get_dynamic_value_input_props(node):
    """获取节点的动态值输入属性（# 开头或在 type hints 中的）"""
    result = []
    
    if not node:
        return result
    
    try:
        props = list(node.getProperties(SDPropertyCategory.Input))
    except BaseException:
        return result
    
    type_hints = collect_dynamic_input_type_hints_from_node(node)
    variable_names = set(type_hints.keys())
    
    for prop in props:
        try:
            pid = prop.getId()
        except BaseException:
            continue
        
        if not pid:
            continue
        
        if pid.startswith("#"):
            result.append(prop)
            continue
        
        if pid in variable_names:
            result.append(prop)
            continue
        
        if pid.lstrip("#") in variable_names:
            result.append(prop)
            continue
    
    return result

def delete_property_if_possible(node, prop):
    """尝试删除节点属性"""
    try:
        node.deleteProperty(prop)
        return True
    except BaseException as e:
        print("[NodePresets] deleteProperty failed:", e)
        return False
    
def verify_dynamic_property_type(node, prop_id, expected_type_id=None):
    """验证动态属性类型是否正确"""
    try:
        prop = node.getPropertyFromId(prop_id, SDPropertyCategory.Input)
    except BaseException:
        prop = None
    
    if not prop:
        return False, None
    
    current_type = get_prop_first_type_id(prop)
    
    if expected_type_id:
        expected = normalize_type_id(expected_type_id)
        if current_type != expected:
            return False, prop
    
    return True, prop

def create_dynamic_value_input_by_id(dst_node, prop_id, forced_type_id, type_search_nodes=None):
    """通过 ID 创建动态值输入"""
    if not dst_node or not prop_id:
        return False
    
    if not str(prop_id).startswith("#"):
        print("[NodePresets] create_dynamic_value_input_by_id skipped non-# prop:", prop_id)
        return False
    
    expected = normalize_type_id(forced_type_id)
    
    # 验证是否已存在且类型正确
    ok, existing_prop = verify_dynamic_property_type(dst_node, prop_id, expected)
    if ok:
        print("[NodePresets] Dynamic input already exists and type ok:", prop_id, expected)
        return True
    
    # 类型不匹配，删除重建
    if existing_prop and expected:
        print("[NodePresets] Dynamic input exists but type mismatch, recreate:", prop_id)
        delete_property_if_possible(dst_node, existing_prop)
    
    # 获取 SDType 对象
    sd_type = None
    if expected and type_search_nodes:
        sd_type = find_sdtype_object_by_id(type_search_nodes, expected)
    
    if not sd_type:
        print("[NodePresets] Cannot find SDType for:", prop_id, expected)
        return False
    
    if not hasattr(dst_node, "newProperty"):
        print("[NodePresets] dst_node has no newProperty")
        return False
    
    print("[NodePresets] Trying newProperty dynamic input id={}, type={}".format(prop_id, expected))
    
    # 尝试多种参数组合
    variants = [
        (prop_id, sd_type, SDPropertyCategory.Input),
        (prop_id.lstrip("#"), sd_type, SDPropertyCategory.Input),
        (prop_id, SDPropertyCategory.Input, sd_type),
        (prop_id.lstrip("#"), SDPropertyCategory.Input, sd_type),
    ]
    
    for args in variants:
        try:
            result = dst_node.newProperty(*args)
            print("[NodePresets] newProperty{} -> {}".format(args, result))
            
            # 验证创建成功
            ok, created_prop = verify_dynamic_property_type(dst_node, prop_id, expected)
            if ok:
                print("[NodePresets] Verified dynamic input exists:", prop_id, expected)
                return True
            
            # 尝试不带 # 的版本
            no_hash = prop_id.lstrip("#")
            ok2, created_prop2 = verify_dynamic_property_type(dst_node, no_hash, expected)
            if ok2:
                print("[NodePresets] Verified dynamic input exists:", no_hash, expected)
                return True
            
            # 清理失败的创建
            if created_prop:
                delete_property_if_possible(dst_node, created_prop)
            if created_prop2:
                delete_property_if_possible(dst_node, created_prop2)
        
        except BaseException as e:
            print("[NodePresets] newProperty{} failed: {}".format(args, e))
    
    print("[NodePresets] FAILED create dynamic input:", prop_id, expected)
    return False

def create_dynamic_value_input_on_node(dst_node, src_prop, forced_type_id=None, type_search_nodes=None):
    """在节点上创建动态值输入"""
    if not dst_node or not src_prop:
        return False
    
    try:
        prop_id = src_prop.getId()
    except BaseException:
        return False
    
    if not prop_id:
        return False
    
    type_id = forced_type_id
    if not type_id:
        type_id = get_prop_first_type_id(src_prop)
    
    return create_dynamic_value_input_by_id(
        dst_node=dst_node,
        prop_id=prop_id,
        forced_type_id=type_id,
        type_search_nodes=type_search_nodes
    )

# =============================================================================
# Base Parameters Helpers
# =============================================================================

def is_base_parameter_prop_id(prop_id):
    """判断是否是 Substance Designer Base Parameters"""
    if not prop_id:
        return False

    pid = str(prop_id).lower()

    base_ids = {
        "$outputsize",
        "$format",
        "$pixelsize",
        "$pixelratio",
        "$tiling",
        "$randomseed",
        "$size",
        "$sizelog2",
        "$pos",
        "$normal",
        "$time",
    }

    if pid in base_ids:
        return True

    keywords = [
        "outputsize", "output_size",
        "format",
        "pixelsize", "pixel_size",
        "pixelratio", "pixel_ratio",
        "tiling",
        "randomseed", "random_seed",
    ]

    return any(k in pid for k in keywords)


def copy_property_inheritance_method_safe(src_node, dst_node, src_prop, dst_prop, prop_id=""):
    """复制属性 inheritance method，Base Parameters 很依赖这个"""
    if not src_node or not dst_node or not src_prop or not dst_prop:
        return False

    try:
        inheritance = src_node.getPropertyInheritanceMethod(src_prop)
    except BaseException as e:
        if is_base_parameter_prop_id(prop_id):
            print("[NodePresets] getPropertyInheritanceMethod failed {}: {}".format(prop_id, e))
        return False

    if inheritance is None:
        return False

    try:
        dst_node.setPropertyInheritanceMethod(dst_prop, inheritance)
        if is_base_parameter_prop_id(prop_id):
            print("[NodePresets] ✓ Copied base parameter inheritance {}: {}".format(
                prop_id, inheritance
            ))
        return True
    except BaseException as e:
        if is_base_parameter_prop_id(prop_id):
            print("[NodePresets] setPropertyInheritanceMethod failed {}: {}".format(prop_id, e))
        return False


def copy_graph_base_parameters(src_graph, dst_graph):
    """复制 Graph 级 Base Parameters"""
    if not src_graph or not dst_graph:
        return

    try:
        props = src_graph.getProperties(SDPropertyCategory.Input)
    except BaseException:
        props = []

    for src_prop in props:
        try:
            prop_id = src_prop.getId()
        except BaseException:
            continue

        if not prop_id:
            continue

        if not is_base_parameter_prop_id(prop_id):
            continue

        try:
            dst_prop = dst_graph.getPropertyFromId(prop_id, SDPropertyCategory.Input)
        except BaseException:
            dst_prop = None

        if not dst_prop:
            print("[NodePresets] Graph base parameter missing on dst:", prop_id)
            continue

        print("[NodePresets] Copy graph base parameter:", prop_id)

        try:
            inheritance = src_graph.getPropertyInheritanceMethod(src_prop)
            if inheritance is not None:
                dst_graph.setPropertyInheritanceMethod(dst_prop, inheritance)
                print("[NodePresets] ✓ Copied graph base inheritance {}: {}".format(
                    prop_id, inheritance
                ))
        except BaseException as e:
            print("[NodePresets] Copy graph base inheritance failed {}: {}".format(prop_id, e))

        try:
            prop_graph = src_graph.getPropertyGraph(src_prop)
        except BaseException:
            prop_graph = None

        if prop_graph:
            try:
                dst_prop_graph = dst_graph.newPropertyGraph(
                    sdProperty=dst_prop,
                    sdGraphTypeId="SDSBSFunctionGraph"
                )
                if dst_prop_graph:
                    copy_function_graph(prop_graph, dst_prop_graph, None, prop_id)
                    print("[NodePresets] ✓ Copied graph base property graph:", prop_id)
            except BaseException as e:
                print("[NodePresets] Copy graph base property graph failed {}: {}".format(prop_id, e))

        try:
            is_readonly = src_prop.isReadOnly()
        except BaseException:
            is_readonly = False

        if not is_readonly:
            try:
                value = src_graph.getPropertyValue(src_prop)
                if value is not None:
                    dst_graph.setPropertyValue(dst_prop, value)
                    print("[NodePresets] ✓ Copied graph base value {}: {}".format(
                        prop_id, debug_value(value)
                    ))
            except BaseException as e:
                print("[NodePresets] Copy graph base value failed {}: {}".format(prop_id, e))

# =============================================================================
# Annotation / Property Graph Copy
# =============================================================================

def copy_annotation_properties(src, dst):
    """复制 Annotation 属性"""
    if not src or not dst:
        return
    
    try:
        props = src.getProperties(SDPropertyCategory.Annotation)
    except BaseException:
        props = []
    
    for prop in props:
        try:
            prop_id = prop.getId()
        except BaseException:
            continue
        
        if not prop_id:
            continue
        
        value = get_property_value_safe(src, prop, SDPropertyCategory.Annotation)
        if value is None:
            continue
        
        try:
            dst_prop = dst.getPropertyFromId(prop_id, SDPropertyCategory.Annotation)
        except BaseException:
            dst_prop = None
        
        set_property_value_safe(dst, dst_prop, prop_id, SDPropertyCategory.Annotation, value)

def copy_dynamic_value_inputs(src_node, dst_node, all_type_search_nodes=None):
    """复制动态值输入"""
    if not src_node or not dst_node:
        return
    
    if all_type_search_nodes is None:
        all_type_search_nodes = [src_node, dst_node]
    
    type_hints = collect_dynamic_input_type_hints_from_node(src_node)
    
    if type_hints:
        print("[NodePresets] Type hints:", type_hints)
    
    dyn_props = get_dynamic_value_input_props(src_node)
    created_names = set()
    
    # 1. 复制源节点已有 # 动态输入
    for src_prop in dyn_props:
        try:
            pid = src_prop.getId()
        except BaseException:
            continue
        
        if not pid:
            continue
        
        if not pid.startswith("#"):
            print("[NodePresets] Skip non-# dynamic prop on node, graph handles it:", pid)
            continue
        
        # 确定类型
        forced_type_id = None
        if pid in type_hints:
            forced_type_id = type_hints[pid]
        elif pid.lstrip("#") in type_hints:
            forced_type_id = type_hints[pid.lstrip("#")]
        elif ("#" + pid) in type_hints:
            forced_type_id = type_hints["#" + pid]
        
        if not forced_type_id:
            forced_type_id = get_prop_first_type_id(src_prop)
        
        print("[NodePresets] Node dynamic input {} forced_type_id={}".format(pid, forced_type_id))
        
        ok = create_dynamic_value_input_on_node(
            dst_node=dst_node,
            src_prop=src_prop,
            forced_type_id=forced_type_id,
            type_search_nodes=all_type_search_nodes
        )
        
        if ok:
            created_names.add(pid)
            created_names.add(pid.lstrip("#"))
            
            # 复制 inheritance method
            try:
                dst_prop = dst_node.getPropertyFromId(pid, SDPropertyCategory.Input)
                inheritance = src_node.getPropertyInheritanceMethod(src_prop)
                if dst_prop and inheritance:
                    dst_node.setPropertyInheritanceMethod(dst_prop, inheritance)
            except BaseException:
                pass
    
    # 2. 根据 type_hints 补创建 # 开头的节点动态输入
    for var_name, type_id in type_hints.items():
        if not var_name:
            continue
        
        if not var_name.startswith("#"):
            print("[NodePresets] Skip non-# variable here, graph/global input handled by graph:", var_name, type_id)
            continue
        
        if var_name in created_names or var_name.lstrip("#") in created_names:
            continue
        
        if is_builtin_function_variable(var_name):
            continue
        
        print("[NodePresets] Creating missing # node dynamic input from type hint:", var_name, type_id)
        
        create_dynamic_value_input_by_id(
            dst_node=dst_node,
            prop_id=var_name,
            forced_type_id=type_id,
            type_search_nodes=all_type_search_nodes
        )

def copy_property_graph_if_exists(src_node, dst_node, src_prop, dst_prop, prop_id):
    """如果存在 property graph，则复制它"""
    if not src_node or not dst_node or not src_prop or not dst_prop:
        return
    
    try:
        prop_graph = src_node.getPropertyGraph(src_prop)
    except BaseException:
        prop_graph = None
    
    if not prop_graph:
        return
    
    print("[NodePresets] Found property graph for {}, copying...".format(prop_id))
    
    try:
        dst_prop_graph = dst_node.newPropertyGraph(
            sdProperty=dst_prop,
            sdGraphTypeId="SDSBSFunctionGraph"
        )
    except BaseException as e:
        print("[NodePresets] newPropertyGraph failed for {}: {}".format(prop_id, e))
        return
    
    if dst_prop_graph:
        copy_function_graph(prop_graph, dst_prop_graph, dst_node, prop_id)

def copy_node_properties(src, dst):
    """复制节点属性，包括 Base Parameters"""
    if not src or not dst:
        return

    src_def_id = get_node_def_id(src) or ""
    print("[NodePresets] copy_node_properties:", src_def_id)

    copy_annotation_properties(src, dst)
    copy_node_label(src, dst)

    try:
        input_props = src.getProperties(SDPropertyCategory.Input)
    except BaseException:
        input_props = []

    for prop in input_props:
        try:
            prop_id = prop.getId()
        except BaseException:
            continue

        if not prop_id:
            continue

        try:
            dst_prop = dst.getPropertyFromId(prop_id, SDPropertyCategory.Input)
        except BaseException:
            dst_prop = None

        if not dst_prop:
            if is_base_parameter_prop_id(prop_id):
                print("[NodePresets] ⚠ Base parameter missing on dst:", prop_id)
            else:
                print("[NodePresets] Skip input prop {}: no dst_prop".format(prop_id))
            continue

        try:
            is_readonly = bool(prop.isReadOnly())
        except BaseException:
            is_readonly = False

        try:
            connectable = prop.isConnectable()
        except BaseException:
            connectable = True

        if is_base_parameter_prop_id(prop_id):
            print("[NodePresets] Base parameter: id={}, readonly={}, connectable={}".format(
                prop_id, is_readonly, connectable
            ))

        # ============================================================
        # 关键修改 1：所有属性都尝试复制 inheritance method
        # ============================================================
        copy_property_inheritance_method_safe(
            src_node=src,
            dst_node=dst,
            src_prop=prop,
            dst_prop=dst_prop,
            prop_id=prop_id
        )

        # ============================================================
        # 关键修改 2：所有属性都尝试复制 property graph
        # ============================================================
        copy_property_graph_if_exists(src, dst, prop, dst_prop, prop_id)

        # ============================================================
        # 关键修改 3：readonly 属性不设置 value，但前面已经复制了 inheritance/graph
        # ============================================================
        if is_readonly:
            if is_base_parameter_prop_id(prop_id):
                print("[NodePresets] Skip setting readonly base parameter value:", prop_id)
            continue

        # 复制值
        value = get_property_value_safe(src, prop, SDPropertyCategory.Input)
        if value is not None:
            ok = set_input_value_safe(dst, prop_id, value)

            if is_base_parameter_prop_id(prop_id):
                print("[NodePresets] Copy base parameter value {} -> {}, ok={}".format(
                    prop_id, debug_value(value), ok
                ))

    # 再次复制 annotation 确保不被覆盖
    copy_annotation_properties(src, dst)

# =============================================================================
# Function Graph Copy
# =============================================================================

def copy_function_graph(src_graph, dst_graph, parent_node=None, prop_id=None):
    """复制 Function Graph"""
    print("[NodePresets] copy_function_graph:", prop_id)
    
    if not src_graph or not dst_graph:
        return
    
    try:
        src_nodes = list(src_graph.getNodes())
    except BaseException as e:
        print("[NodePresets] Function graph getNodes failed:", e)
        return
    
    node_map = {}
    
    # 1. 创建节点
    for src_node in src_nodes:
        def_id = get_node_def_id(src_node) or ""
        if not def_id:
            continue
        
        lower_def = def_id.lower()
        new_node = None
        
        try:
            if "instance" in lower_def:
                try:
                    ref = src_node.getReferencedResource()
                except BaseException:
                    ref = None
                
                if ref:
                    new_node = dst_graph.newInstanceNode(ref)
            else:
                new_node = dst_graph.newNode(def_id)
        except BaseException as e:
            print("[NodePresets] Function new node failed {}: {}".format(def_id, e))
            continue
        
        if not new_node:
            continue
        
        try:
            src_id = src_node.getIdentifier()
        except BaseException:
            src_id = str(len(node_map))
        
        node_map[src_id] = new_node
        
        # 设置位置
        try:
            new_node.setPosition(src_node.getPosition())
        except BaseException:
            pass
        
        copy_node_label(src_node, new_node)
        copy_annotation_properties(src_node, new_node)
        
        # 复制所有类别的属性
        for cat in [SDPropertyCategory.Input, SDPropertyCategory.Output, SDPropertyCategory.Annotation]:
            try:
                props = src_node.getProperties(cat)
            except BaseException:
                props = []
            
            for prop in props:
                try:
                    p_id = prop.getId()
                except BaseException:
                    continue
                
                if not p_id:
                    continue
                
                try:
                    dst_prop = new_node.getPropertyFromId(p_id, cat)
                except BaseException:
                    dst_prop = None
                
                if not dst_prop:
                    continue
                
                # 获取值
                if cat == SDPropertyCategory.Input:
                    value = get_function_property_value_safe(src_node, prop)
                else:
                    value = get_property_value_safe(src_node, prop, cat)
                
                if value is not None:
                    if cat == SDPropertyCategory.Input:
                        set_function_input_value_safe(new_node, p_id, value)
                    else:
                        set_property_value_safe(new_node, dst_prop, p_id, cat, value)
                
                # 复制 property graph（如果是 Input 且不可连接）
                if cat == SDPropertyCategory.Input:
                    try:
                        connectable = prop.isConnectable()
                    except BaseException:
                        connectable = True
                    
                    if not connectable:
                        copy_property_graph_if_exists(src_node, new_node, prop, dst_prop, p_id)
        
        copy_annotation_properties(src_node, new_node)
    
    # 2. 复制连接
    for src_node in src_nodes:
        try:
            src_id = src_node.getIdentifier()
        except BaseException:
            continue
        
        if src_id not in node_map:
            continue
        
        target_node = node_map[src_id]
        
        try:
            input_props = src_node.getProperties(SDPropertyCategory.Input)
        except BaseException:
            input_props = []
        
        for prop in input_props:
            try:
                if not prop.isConnectable():
                    continue
            except BaseException:
                continue
            
            try:
                conns = src_node.getPropertyConnections(prop)
            except BaseException:
                conns = []
            
            for conn in conns:
                try:
                    input_node = conn.getInputPropertyNode()
                    input_id = input_node.getIdentifier()
                except BaseException:
                    continue
                
                if input_id not in node_map:
                    continue
                
                target_input_node = node_map[input_id]
                
                try:
                    input_prop_id = conn.getInputProperty().getId()
                    output_prop_id = conn.getOutputProperty().getId()
                    
                    target_input_node.newPropertyConnectionFromId(
                        input_prop_id,
                        target_node,
                        output_prop_id
                    )
                except BaseException as e:
                    print("[NodePresets] Function connection failed:", e)
    
    # 3. 设置输出节点
    try:
        output_nodes = list(src_graph.getOutputNodes())
    except BaseException:
        output_nodes = []
    
    for out_node in output_nodes:
        try:
            out_id = out_node.getIdentifier()
        except BaseException:
            continue
        
        if out_id in node_map:
            try:
                dst_graph.setOutputNode(node_map[out_id], True)
            except BaseException:
                pass
            break

# =============================================================================
# Node Copy
# =============================================================================

def copy_internal_connections(src_nodes, node_map):
    """复制节点之间的内部连接"""
    print("[NodePresets] copy_internal_connections")
    
    src_id_to_idx = {}
    
    for i, node in enumerate(src_nodes):
        try:
            src_id_to_idx[node.getIdentifier()] = i
        except BaseException:
            src_id_to_idx[str(i)] = i
    
    conn_count = 0
    
    for src_idx, src_node in enumerate(src_nodes):
        if src_idx not in node_map:
            continue
        
        target_node = node_map[src_idx]
        
        try:
            input_props = src_node.getProperties(SDPropertyCategory.Input)
        except BaseException:
            input_props = []
        
        for prop in input_props:
            try:
                if not prop.isConnectable():
                    continue
            except BaseException:
                continue
            
            try:
                conns = src_node.getPropertyConnections(prop)
            except BaseException:
                conns = []
            
            for conn in conns:
                try:
                    input_node = conn.getInputPropertyNode()
                    input_id = input_node.getIdentifier()
                except BaseException:
                    continue
                
                if input_id not in src_id_to_idx:
                    continue
                
                input_src_idx = src_id_to_idx[input_id]
                
                if input_src_idx not in node_map:
                    continue
                
                target_input_node = node_map[input_src_idx]
                
                try:
                    input_prop_id = conn.getInputProperty().getId()
                    output_prop_id = conn.getOutputProperty().getId()
                    
                    target_input_node.newPropertyConnectionFromId(
                        input_prop_id,
                        target_node,
                        output_prop_id
                    )
                    
                    conn_count += 1
                
                except BaseException as e:
                    print("[NodePresets] Connection failed:", e)
    
    print("[NodePresets] copy_internal_connections done:", conn_count)

def copy_nodes_to_graph(src_nodes, dst_graph, offset_x=0, offset_y=0, src_graph=None):
    """复制节点到目标 Graph（核心函数）"""
    print("[NodePresets] copy_nodes_to_graph:", len(src_nodes))
    
    src_nodes = list(src_nodes)
    
    # 只处理 function graph 中的非 # 外部变量，例如 time11
    ensure_graph_inputs_from_function_hints_only(
        src_nodes=src_nodes,
        dst_graph=dst_graph,
        src_graph=src_graph,
        type_search_nodes=list(src_nodes)
    )
    
    created_nodes = []
    node_map = {}
    
    # 获取目标 graph 的可用定义
    dst_defs = {}
    try:
        for nd in dst_graph.getNodeDefinitions():
            try:
                dst_defs[nd.getId()] = nd
            except BaseException:
                pass
    except BaseException:
        pass
    
    # 创建节点
    for i, src_node in enumerate(src_nodes):
        src_def_id = get_node_def_id(src_node)
        if not src_def_id:
            continue
        
        print("[NodePresets] Copying node[{}]: {}".format(i, src_def_id))
        
        new_node = None
        
        try:
            # Instance 节点
            if "instance" in src_def_id.lower():
                try:
                    ref = src_node.getReferencedResource()
                except BaseException:
                    ref = None
                
                if ref:
                    new_node = dst_graph.newInstanceNode(ref)
                else:
                    print("[NodePresets] Skip instance, no referenced resource")
                    continue
            
            # Input Value 节点（需要特殊处理类型）
            elif is_input_value_node(src_node):
                # 推断类型
                src_type_id = infer_input_value_type_from_node(
                    node=src_node,
                    type_search_nodes=list(src_nodes),
                    all_nodes=list(src_nodes)
                )
                
                if src_type_id:
                    # 获取 SDType 对象
                    sd_type = find_sdtype_object_by_id(list(src_nodes) + [src_node], src_type_id)
                    
                    if sd_type:
                        print("[NodePresets] Creating Input Value node with type:", src_type_id)
                        
                        # 尝试方法 1: newNode with SDType parameter
                        try:
                            new_node = dst_graph.newNode(src_def_id, sd_type)
                            print("[NodePresets] Created Input Value via newNode(def, type)")
                        except BaseException as e:
                            print("[NodePresets] newNode(def, type) failed:", e)
                        
                        # 尝试方法 2: newInputValueNode (如果存在)
                        if not new_node and hasattr(dst_graph, "newInputValueNode"):
                            try:
                                new_node = dst_graph.newInputValueNode(sd_type)
                                print("[NodePresets] Created Input Value via newInputValueNode")
                            except BaseException as e:
                                print("[NodePresets] newInputValueNode failed:", e)
                
                # 如果上述方法都失败，使用默认创建
                if not new_node:
                    print("[NodePresets] Fallback to default Input Value creation")
                    new_node = dst_graph.newNode(src_def_id)
            
            # 普通节点
            else:
                if dst_defs and src_def_id not in dst_defs:
                    print("[NodePresets] Skip no matching def:", src_def_id)
                    continue
                
                new_node = dst_graph.newNode(src_def_id)
        
        except BaseException as e:
            print("[NodePresets] New node failed {}: {}".format(src_def_id, e))
            continue
        
        if not new_node:
            continue
        
        node_map[i] = new_node
        created_nodes.append(new_node)
        
        # 设置位置
        x, y = get_node_position(src_node)
        set_node_position(new_node, x + offset_x, y + offset_y)
        
        copy_node_label(src_node, new_node)
        
        # Input Value 节点特殊处理
        if is_input_value_node(src_node):
            # 检查源 Graph 是否有对应 Graph Input
            ident = get_input_value_identifier(src_node)
            should_create_graph_input = False
            
            if src_graph and ident:
                src_graph_input = find_graph_input_property(src_graph, ident)
                if src_graph_input:
                    print("[NodePresets] Input Value {} references existing Graph Input, will copy".format(ident))
                    should_create_graph_input = True
                else:
                    print("[NodePresets] Input Value {} is internal constant, will NOT create Graph Input".format(ident))
            
            # 如果需要，创建 Graph Input
            if should_create_graph_input:
                create_or_copy_graph_input_from_source(
                    src_graph=src_graph,
                    dst_graph=dst_graph,
                    prop_id=ident,
                    type_search_nodes=list(src_nodes) + created_nodes + [src_node, new_node]
                )
            
            # 复制 Input Value 节点数据
            copy_input_value_node_data(src_node, new_node)
        
        # 普通节点
        else:
            # 复制动态输入
            copy_dynamic_value_inputs(
                src_node=src_node,
                dst_node=new_node,
                all_type_search_nodes=list(src_nodes) + created_nodes + [src_node, new_node]
            )
            
            # 复制节点属性
            copy_node_properties(src_node, new_node)
    
    print("[NodePresets] Created nodes:", len(created_nodes), "/", len(src_nodes))

    # 复制内部连接
    copy_internal_connections(src_nodes, node_map)

    # ============================================================
    # 关键补充：复制 Graph 级别的 Base Parameters
    # ============================================================
    if src_graph:
        print("[NodePresets] Copying graph-level base parameters...")
        copy_graph_base_parameters(src_graph, dst_graph)

    return created_nodes

# =============================================================================
# UI Styles
# =============================================================================

UI_STYLE = """
QWidget {
    font-family: "Segoe UI", Arial, sans-serif;
    background: #404040;
    color: #e0e0e0;
}
QLabel#title {
    font-size: 18px;
    font-weight: bold;
    color: #ffffff;
}
QLabel#subtitle {
    font-size: 11px;
    color: #aaaaaa;
}
QGroupBox {
    font-weight: bold;
    font-size: 12px;
    color: #e0e0e0;
    border: 1px solid #555555;
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 10px;
    background: #383838;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 5px;
    color: #e0e0e0;
}
QLineEdit {
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 6px 10px;
    background: #505050;
    color: #e0e0e0;
    font-size: 13px;
}
QLineEdit:focus {
    border: 1px solid #3498db;
    background: #484848;
}
QLineEdit::placeholder {
    color: #888888;
}
QComboBox {
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 4px 10px;
    background: #505050;
    color: #e0e0e0;
    font-size: 12px;
}
QComboBox:focus {
    border: 1px solid #3498db;
}
QComboBox::dropDown {
    background: #484848;
}
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3498db, stop:1 #2980b9);
    color: white;
    border: none;
    border-radius: 4px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: bold;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5dade2, stop:1 #3498db);
}
QPushButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2980b9, stop:1 #2471a3);
}
QPushButton:disabled {
    background: #555555;
    color: #888888;
}
QPushButton#secondary {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #606060, stop:1 #505050);
    color: #e0e0e0;
    font-weight: normal;
    padding: 6px 12px;
    font-size: 12px;
}
QPushButton#secondary:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #707070, stop:1 #606060);
}
QListWidget {
    border: 1px solid #555555;
    border-radius: 4px;
    background: #383838;
    font-size: 13px;
    color: #e0e0e0;
    show-decoration-selected: 1;
}
QListWidget::item {
    background: #484848;
    border-radius: 4px;
    border: none;
    padding: 0;
    margin: 3px 0;
}
QListWidget::item:selected {
    background: #3498db;
    color: white;
}
QListWidget::item:hover {
    background: #585858;
}
QListWidget::item::disabled {
    background: #484848;
    color: #888888;
}
QToolTip {
    background: #2c3e50;
    color: #ecf0f1;
    border: none;
    padding: 6px 10px;
    border-radius: 4px;
}
"""

# 图标路径配置
_plugin_dir = None

def _get_preset_icon_dir():
    """获取图标目录路径"""
    global _plugin_dir
    if _plugin_dir is None:
        _plugin_dir = os.path.dirname(os.path.abspath(__file__))
    return _plugin_dir

LOAD_ICON_PATH = None  # 默认使用插件目录下的 icons/load.png
DELETE_ICON_PATH = None  # 默认使用插件目录下的 icons/delete.png

def _get_load_icon_path():
    """获取 Load 图标路径"""
    if LOAD_ICON_PATH:
        return LOAD_ICON_PATH
    path = os.path.join(_get_preset_icon_dir(), "icons", "load.png")
    return path if os.path.exists(path) else None

def _get_delete_icon_path():
    """获取 Delete 图标路径"""
    if DELETE_ICON_PATH:
        return DELETE_ICON_PATH
    path = os.path.join(_get_preset_icon_dir(), "icons", "delete.png")
    return path if os.path.exists(path) else None

def set_preset_icons(load_icon_path, delete_icon_path):
    """设置预设按钮的图标路径"""
    global LOAD_ICON_PATH, DELETE_ICON_PATH
    LOAD_ICON_PATH = load_icon_path
    DELETE_ICON_PATH = delete_icon_path

# =============================================================================
# Clickable Label
# =============================================================================

class ClickableLabel(QtWidgets.QLabel):
    """支持单击 / 双击的 QLabel"""

    clicked = QtCore.Signal()
    doubleClicked = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super(ClickableLabel, self).__init__(*args, **kwargs)

        self._click_timer = QtCore.QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(220)
        self._click_timer.timeout.connect(self.clicked.emit)

        self.setCursor(QtCore.Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == QtCore.Qt.LeftButton:
                self._click_timer.start()
        except BaseException:
            pass

        super(ClickableLabel, self).mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        try:
            if event.button() == QtCore.Qt.LeftButton:
                if self._click_timer.isActive():
                    self._click_timer.stop()
                self.doubleClicked.emit()
        except BaseException:
            pass

        super(ClickableLabel, self).mouseDoubleClickEvent(event)


class GroupHeaderWidget(QtWidgets.QWidget):
    """All Groups 模式下的可折叠分组标题"""

    toggled = QtCore.Signal(str)
    deleteRequested = QtCore.Signal(str)

    HEADER_STYLE = """
        QWidget#group_header_root {
            background: #2f2f2f;
            border-radius: 5px;
        }

        QLabel#group_header_title {
            color: #ffffff;
            font-size: 13px;
            font-weight: bold;
            background: transparent;
            padding-left: 6px;
        }

        QLabel#group_header_count {
            color: #aaaaaa;
            font-size: 11px;
            background: transparent;
        }

        QPushButton#delete_group_header_btn {
            background: transparent;
            color: #e74c3c;
            border: none;
            font-size: 11px;
            font-weight: bold;
            padding: 3px 8px;
        }

        QPushButton#delete_group_header_btn:hover {
            background: #5a2f2f;
            border-radius: 3px;
        }

        QPushButton#delete_group_header_btn:disabled {
            color: #666666;
            background: transparent;
        }
    """

    def __init__(self, group_name, count=0, expanded=True, deletable=True, parent=None):
        super(GroupHeaderWidget, self).__init__(parent)

        self.group_name = group_name
        self.count = count
        self.expanded = expanded
        self.deletable = deletable

        self.setObjectName("group_header_root")
        self.setStyleSheet(self.HEADER_STYLE)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(8)

        arrow = "▼" if expanded else "▶"

        self.title_label = ClickableLabel("{} {}".format(arrow, group_name))
        self.title_label.setObjectName("group_header_title")
        self.title_label.setToolTip("Click to expand / collapse group")
        self.title_label.clicked.connect(self._on_toggle_clicked)
        layout.addWidget(self.title_label, 1)

        self.count_label = QtWidgets.QLabel("{} preset(s)".format(count))
        self.count_label.setObjectName("group_header_count")
        layout.addWidget(self.count_label)

        self.delete_btn = QtWidgets.QPushButton("Delete Group")
        self.delete_btn.setObjectName("delete_group_header_btn")
        self.delete_btn.setToolTip("Delete this UI group. Presets will become ungrouped.")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        self.delete_btn.setEnabled(bool(deletable))
        self.delete_btn.setVisible(bool(deletable))
        layout.addWidget(self.delete_btn)

        self.setMinimumHeight(32)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed
        )

    def _on_toggle_clicked(self):
        self.toggled.emit(self.group_name)

    def _on_delete_clicked(self):
        self.deleteRequested.emit(self.group_name)

    def set_expanded(self, expanded):
        self.expanded = bool(expanded)
        arrow = "▼" if self.expanded else "▶"
        self.title_label.setText("{} {}".format(arrow, self.group_name))


class PresetItemWidget(QtWidgets.QWidget):
    """每个预设项的自定义 widget，包含名称和 Load/Delete 按钮"""

    ITEM_STYLE = """
        QWidget {
            background: #505050;
            border-radius: 6px;
        }
        QWidget:hover {
            background: #555555;
        }
        QLabel {
            color: #e0e0e0;
            background: transparent;
        }
        QLabel#preset_name {
            font-size: 14px;
            font-weight: bold;
            color: #ffffff;
        }
        QLabel#group_name {
            font-size: 11px;
            color: #f1c40f;
            font-style: italic;
        }
        QLabel#group_name:hover {
            color: #ffffff;
        }
        QLabel#preset_name:hover {
            color: #5dade2;
        }
        QLabel#param_item {
            font-size: 11px;
            color: #b0b0b0;
            padding-left: 10px;
        }
        QLabel#params_title {
            font-size: 11px;
            color: #f1c40f;
            padding-left: 5px;
            margin-top: 4px;
            background: transparent;
        }
        QLabel#params_title:hover {
            color: #ffffff;
        }
        QPushButton {
            background: transparent;
            border: none;
            border-radius: 3px;
            padding: 3px;
            color: #e0e0e0;
        }
        QPushButton:hover {
            background: #606060;
        }
        QPushButton:pressed {
            background: #484848;
        }
        QFrame#separator {
            background: #606060;
            max-height: 1px;
            margin: 4px 0px;
        }
    """

    def __init__(self, preset_name, group_name, params_list=None, parent=None):
        """
        Args:
            preset_name: 预设名称
            group_name: 分组名称
            params_list: 参数列表 [{'identifier': 'xxx', 'value': 'xxx'}, ...]
        """
        super(PresetItemWidget, self).__init__(parent)
        self.preset_name = preset_name
        self._param_count = len(params_list) if params_list else 0

        # 调试信息
        print("[PresetItemWidget] Creating widget for: {}".format(preset_name))
        print("[PresetItemWidget] Group: {}".format(group_name))
        print("[PresetItemWidget] Params list: {}".format(params_list))
        print("[PresetItemWidget] Params list type: {}".format(type(params_list)))
        if params_list:
            print("[PresetItemWidget] Params list length: {}".format(len(params_list)))

        self.setStyleSheet(self.ITEM_STYLE)

        # 主布局：垂直
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(4)

        # 顶部：预设名称 + 按钮
        top_layout = QtWidgets.QHBoxLayout()
        top_layout.setSpacing(6)

        # 左侧：名称和分组的垂直布局
        name_group_layout = QtWidgets.QVBoxLayout()
        name_group_layout.setSpacing(2)

        # 预设名称
        self.name_label = ClickableLabel(preset_name)
        self.name_label.setObjectName("preset_name")
        self.name_label.setToolTip("Double click to rename preset")
        self.name_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed
        )
        name_group_layout.addWidget(self.name_label)

        # 分组名称，始终显示，方便点击修改
        display_group = group_name if group_name else "Ungrouped"

        self.group_label = ClickableLabel("[{}]".format(display_group))
        self.group_label.setObjectName("group_name")
        self.group_label.setToolTip(
            "Click to choose group\nDouble click to edit group"
        )
        name_group_layout.addWidget(self.group_label)

        top_layout.addLayout(name_group_layout, 1)  # stretch factor = 1

        # 右侧：按钮
        buttons_layout = QtWidgets.QHBoxLayout()
        buttons_layout.setSpacing(3)

        # Load 按钮
        load_icon = _get_load_icon_path()
        if load_icon and os.path.exists(load_icon):
            icon = QtGui.QIcon(load_icon)
            self.load_btn = QtWidgets.QPushButton(icon, "")
        else:
            self.load_btn = QtWidgets.QPushButton("►")
            self.load_btn.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 14px; background: transparent;")
        self.load_btn.setFixedSize(28, 28)
        self.load_btn.setIconSize(QtCore.QSize(18, 18))
        self.load_btn.setToolTip("Load this preset")
        buttons_layout.addWidget(self.load_btn)

        # 不再显示 Rename / Group 按钮（现在通过双击名字和点击分组标签操作）
        self.rename_btn = None
        self.group_btn = None

        # Delete 按钮
        delete_icon = _get_delete_icon_path()
        if delete_icon and os.path.exists(delete_icon):
            icon = QtGui.QIcon(delete_icon)
            self.delete_btn = QtWidgets.QPushButton(icon, "")
        else:
            self.delete_btn = QtWidgets.QPushButton("✕")
            self.delete_btn.setStyleSheet("color: #e74c3c; font-weight: bold; font-size: 14px; background: transparent;")
        self.delete_btn.setFixedSize(28, 28)
        self.delete_btn.setIconSize(QtCore.QSize(18, 18))
        self.delete_btn.setToolTip("Delete this preset")
        buttons_layout.addWidget(self.delete_btn)

        top_layout.addLayout(buttons_layout)

        main_layout.addLayout(top_layout)

        # 参数列表：折叠显示
        params_list = params_list or []
        self._param_count = len(params_list)
        self._params_expanded = False

        if len(params_list) > 0:
            print("[PresetItemWidget] Adding {} parameters to collapsible UI".format(len(params_list)))

            separator = QtWidgets.QFrame()
            separator.setObjectName("separator")
            separator.setFrameShape(QtWidgets.QFrame.HLine)
            separator.setFrameShadow(QtWidgets.QFrame.Sunken)
            separator.setMinimumHeight(1)
            main_layout.addWidget(separator)

            # 可点击 Parameters 标题
            self.params_title_label = ClickableLabel("▶ Parameters ({})".format(len(params_list)))
            self.params_title_label.setObjectName("params_title")
            self.params_title_label.setToolTip("Click to expand / collapse parameters")
            self.params_title_label.setMinimumHeight(20)
            self.params_title_label.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Fixed
            )
            main_layout.addWidget(self.params_title_label)

            # 参数内容容器
            self.params_content_widget = QtWidgets.QWidget()
            self.params_content_widget.setStyleSheet("background: transparent;")
            self.params_content_widget.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Fixed
            )

            params_content_layout = QtWidgets.QVBoxLayout(self.params_content_widget)
            params_content_layout.setContentsMargins(0, 2, 0, 2)
            params_content_layout.setSpacing(2)

            self.params_content_layout = params_content_layout

            max_display = 10
            self._params_max_display = max_display

            for i, param in enumerate(params_list[:max_display]):
                if isinstance(param, dict):
                    identifier = param.get("identifier", "?")
                    value = param.get("value", "?")
                else:
                    try:
                        identifier = param[0]
                        value = param[1]
                    except BaseException:
                        identifier = str(param)
                        value = ""

                identifier = str(identifier)
                value = str(value)

                print("[PresetItemWidget] Adding param {}: {} = {}".format(i, identifier, value))

                param_label = QtWidgets.QLabel(u"  • {}: {}".format(identifier, value))
                param_label.setObjectName("param_item")
                param_label.setWordWrap(False)
                param_label.setMinimumHeight(20)
                param_label.setMaximumHeight(20)
                param_label.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding,
                    QtWidgets.QSizePolicy.Fixed
                )
                params_content_layout.addWidget(param_label)

            if len(params_list) > max_display:
                more_label = QtWidgets.QLabel(
                    "  ... and {} more parameter(s)".format(len(params_list) - max_display)
                )
                more_label.setStyleSheet(
                    "font-size: 10px; color: #777777; padding-left: 10px; "
                    "font-style: italic; background: transparent;"
                )
                more_label.setMinimumHeight(20)
                more_label.setMaximumHeight(20)
                more_label.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding,
                    QtWidgets.QSizePolicy.Fixed
                )
                params_content_layout.addWidget(more_label)

            main_layout.addWidget(self.params_content_widget)

            # 默认收起
            self.params_content_widget.setVisible(False)

            # 单击 Parameters 展开 / 收起
            self.params_title_label.clicked.connect(self.toggle_params_expanded)

        else:
            print("[PresetItemWidget] No parameters to display")
            self.params_title_label = None
            self.params_content_widget = None

        self.setLayout(main_layout)

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Minimum
        )

        # 默认收起
        if self._param_count > 0:
            self.setMinimumHeight(self._collapsed_height())

            if self.params_content_widget:
                self.params_content_widget.setVisible(False)
                self.params_content_widget.setMinimumHeight(0)
                self.params_content_widget.setMaximumHeight(0)
        else:
            self.setMinimumHeight(self._collapsed_height())

        self.updateGeometry()
        self.update()


    def toggle_params_expanded(self):
        """单击 Parameters 标题展开 / 收起"""
        self.set_params_expanded(
            not getattr(self, "_params_expanded", False)
        )


    def _collapsed_height(self):
        """Parameters 收起时卡片高度"""
        if getattr(self, "_param_count", 0) > 0:
            return 92
        return 64


    def _params_content_height(self):
        """参数内容区高度"""
        param_count = getattr(self, "_param_count", 0)
        max_display = getattr(self, "_params_max_display", 10)

        display_count = min(param_count, max_display)

        line_h = 20
        h = display_count * line_h

        if display_count > 1:
            h += (display_count - 1) * 2

        if param_count > max_display:
            h += 20

        h += 6

        return h


    def _expanded_height(self):
        """Parameters 展开时卡片高度"""
        return self._collapsed_height() + self._params_content_height()


    def set_params_expanded(self, expanded):
        """设置参数区域展开状态，修复展开后内容被挤压的问题"""
        self._params_expanded = bool(expanded)

        scroll_area = None
        p = self.parentWidget()

        while p:
            if isinstance(p, QtWidgets.QScrollArea):
                scroll_area = p
                break
            p = p.parentWidget()

        viewport = scroll_area.viewport() if scroll_area else None

        try:
            if viewport:
                viewport.setUpdatesEnabled(False)

            self.setUpdatesEnabled(False)

            if hasattr(self, "params_title_label") and self.params_title_label:
                arrow = "▼" if self._params_expanded else "▶"
                count = getattr(self, "_param_count", 0)
                self.params_title_label.setText("{} Parameters ({})".format(arrow, count))

            if hasattr(self, "params_content_widget") and self.params_content_widget:
                if self._params_expanded:
                    content_h = self._params_content_height()

                    self.params_content_widget.setVisible(True)
                    self.params_content_widget.setMinimumHeight(content_h)
                    self.params_content_widget.setMaximumHeight(content_h)

                    self.setMinimumHeight(self._expanded_height())

                else:
                    self.params_content_widget.setVisible(False)
                    self.params_content_widget.setMinimumHeight(0)
                    self.params_content_widget.setMaximumHeight(0)

                    self.setMinimumHeight(self._collapsed_height())

            self.updateGeometry()

            layout = self.layout()
            if layout:
                layout.invalidate()
                layout.activate()

            parent = self.parentWidget()
            if parent:
                parent.updateGeometry()

                playout = parent.layout()
                if playout:
                    playout.invalidate()
                    playout.activate()

        finally:
            self.setUpdatesEnabled(True)

            if viewport:
                QtCore.QTimer.singleShot(
                    0,
                    lambda: viewport.setUpdatesEnabled(True)
                )

            self.update()


    def sizeHint(self):
        """稳定 sizeHint，避免 Qt 把展开内容压扁"""
        hint = super(PresetItemWidget, self).sizeHint()

        if getattr(self, "_params_expanded", False):
            h = self._expanded_height()
        else:
            h = self._collapsed_height()

        return QtCore.QSize(hint.width(), max(hint.height(), h))


def format_input_value_params_for_widget(params):
    """格式化 Input Value 参数为 widget 使用的列表格式"""
    if not params:
        return []

    result = []
    for p in params:
        result.append({
            'identifier': p['identifier'],
            'value': p['value']
        })

    return result


def params_list_has_real_values(params_list):
    """判断 params_list 里是否至少有一个有效 value"""
    if not params_list:
        return False

    for p in params_list:
        if not isinstance(p, dict):
            continue

        v = p.get("value", None)

        if v is None:
            continue

        s = str(v).strip()

        if not s:
            continue

        if s.lower() in ["none", "null", "nil"]:
            continue

        return True

    return False


def normalize_params_list_for_ui(params_list):
    """把任意参数列表整理成 UI 需要的格式"""
    result = []

    for p in params_list or []:
        if isinstance(p, dict):
            identifier = p.get("identifier", "")
            value = p.get("value", "")
        else:
            try:
                identifier = p[0]
                value = p[1]
            except BaseException:
                identifier = str(p)
                value = ""

        result.append({
            "identifier": str(identifier),
            "value": str(value)
        })

    return result


def get_preset_params_for_ui(preset_graph, preset_name=""):
    """获取 UI 显示用参数列表，避免使用旧的 None 缓存"""
    params_list = []

    # 1. 读取 params_detail
    try:
        params_detail_value = preset_graph.getAnnotationPropertyValueFromId("params_detail")
        print("[NodePresets] params_detail_value for {}: {}".format(
            preset_name,
            params_detail_value
        ))

        if params_detail_value:
            import json
            params_json = sdvalue_to_python(params_detail_value)
            print("[NodePresets] params_json for {}: {}".format(
                preset_name,
                params_json
            ))

            if params_json:
                loaded = json.loads(params_json)

                if isinstance(loaded, list):
                    params_list = normalize_params_list_for_ui(loaded)

    except BaseException as e:
        print("[NodePresets] Failed to read params_detail for {}: {}".format(
            preset_name,
            e
        ))
        params_list = []

    print("[NodePresets] params from annotation for {}: {}".format(
        preset_name,
        params_list
    ))

    # 2. 如果 annotation 里的值有效，直接用
    if params_list_has_real_values(params_list):
        print("[NodePresets] Using params_detail for UI:", params_list)
        return params_list

    # 3. annotation 无效，强制从 graph 节点重建
    print("[NodePresets] params_detail invalid or all None, rebuilding from graph nodes:", preset_name)

    rebuilt_ui_params = []

    try:
        preset_nodes = list(preset_graph.getNodes())
        rebuilt = extract_input_value_params_from_nodes(preset_nodes)

        rebuilt_ui_params = normalize_params_list_for_ui(rebuilt)

        print("[NodePresets] rebuilt params for {}: {}".format(
            preset_name,
            rebuilt_ui_params
        ))

    except BaseException as e:
        print("[NodePresets] Failed to rebuild params from graph nodes for {}: {}".format(
            preset_name,
            e
        ))
        import traceback
        traceback.print_exc()

    # 4. 如果重建出来有有效值，修复 params_detail
    if params_list_has_real_values(rebuilt_ui_params):
        try:
            import json
            from sd.api.sdvaluestring import SDValueString

            fixed_json = json.dumps(rebuilt_ui_params, ensure_ascii=False)
            preset_graph.setAnnotationPropertyValueFromId(
                "params_detail",
                SDValueString.sNew(fixed_json)
            )

            print("[NodePresets] Fixed params_detail for {}: {}".format(
                preset_name,
                fixed_json
            ))

        except BaseException as e:
            print("[NodePresets] Failed to fix params_detail:", e)

        return rebuilt_ui_params

    # 5. 如果重建也全是 None
    print("[NodePresets] WARNING: rebuilt params still invalid for {}: {}".format(
        preset_name,
        rebuilt_ui_params
    ))

    return rebuilt_ui_params


# =============================================================================
# UI - Main Widget
# =============================================================================

class NodePresetWidget(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super(NodePresetWidget, self).__init__(parent)

        self.package_path = get_package_path()
        self.presets = []
        self.groups = []  # 分组列表

        # All Groups 模式下，每个分组是否展开
        self.group_expanded = {}

        self.build_ui()
        self.refresh_groups()
        self.refresh_presets()

    def build_ui(self):
        """构建 UI 界面"""
        self.setStyleSheet(UI_STYLE)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)

        # 标题区域
        title_layout = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Node Presets")
        title.setObjectName("title")
        title_layout.addWidget(title)
        title_layout.addStretch()

        # Package 信息
        pkg_info = QtWidgets.QLabel("Package: {}".format(get_package_name()))
        pkg_info.setObjectName("subtitle")
        pkg_info.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        title_layout.addWidget(pkg_info)
        layout.addLayout(title_layout)

        # 保存预设组
        save_group = QtWidgets.QGroupBox("Create New Preset")
        save_layout = QtWidgets.QVBoxLayout(save_group)
        save_layout.setSpacing(10)

        # 名称输入
        name_layout = QtWidgets.QHBoxLayout()
        name_layout.addWidget(QtWidgets.QLabel("Name:"))
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("Enter preset name...")
        name_layout.addWidget(self.name_edit)
        save_layout.addLayout(name_layout)

        # 分组输入
        group_layout = QtWidgets.QHBoxLayout()
        group_layout.addWidget(QtWidgets.QLabel("Group:"))
        self.group_edit = QtWidgets.QLineEdit()
        self.group_edit.setPlaceholderText("Optional (e.g. Effects, Materials)")
        group_layout.addWidget(self.group_edit)
        save_layout.addLayout(group_layout)

        # 保存按钮
        self.save_btn = QtWidgets.QPushButton("Save Selection")
        self.save_btn.clicked.connect(self.create_preset_from_selection)
        save_layout.addWidget(self.save_btn)

        layout.addWidget(save_group)

        # 预设列表组
        list_group = QtWidgets.QGroupBox("Saved Presets")
        list_layout = QtWidgets.QVBoxLayout(list_group)
        list_layout.setSpacing(8)

        # 分组筛选
        filter_layout = QtWidgets.QHBoxLayout()
        filter_layout.addWidget(QtWidgets.QLabel("Filter:", styleSheet="color: #5a6c7d;"))
        self.group_filter = QtWidgets.QComboBox()
        self.group_filter.setEditable(True)
        self.group_filter.addItem("(All Groups)")
        self.group_filter.addItem("(Ungrouped)")
        self.group_filter.currentIndexChanged.connect(self.on_group_filter_changed)
        filter_layout.addWidget(self.group_filter)

        # 删除当前分组按钮
        self.delete_group_btn = QtWidgets.QPushButton("Delete Group")
        self.delete_group_btn.setToolTip("Delete selected UI group. Presets will become ungrouped.")
        self.delete_group_btn.clicked.connect(self.delete_current_group)
        self.delete_group_btn.hide()
        filter_layout.addWidget(self.delete_group_btn)

        filter_layout.addStretch()
        list_layout.addLayout(filter_layout)

        # 列表 - 使用 QScrollArea + QWidget 代替 QListWidget 以便更好控制布局
        self.list_scroll = QtWidgets.QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid #555555;
                border-radius: 4px;
                background: #383838;
            }
            QScrollArea > QWidget {
                background: #383838;
            }
        """)
        self.list_container = QtWidgets.QWidget()
        self.list_container.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Minimum
        )

        self.list_layout = QtWidgets.QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(6, 6, 6, 6)
        self.list_layout.setSpacing(8)

        try:
            self.list_layout.setSizeConstraint(QtWidgets.QLayout.SetMinAndMaxSize)
        except BaseException:
            pass

        self.list_layout.addStretch()
        self.list_scroll.setWidget(self.list_container)
        list_layout.addWidget(self.list_scroll)

        # 底部按钮
        bottom_layout = QtWidgets.QHBoxLayout()
        bottom_layout.addStretch()
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.setObjectName("secondary")
        self.refresh_btn.clicked.connect(self.refresh_presets)
        bottom_layout.addWidget(self.refresh_btn)
        list_layout.addLayout(bottom_layout)

        layout.addWidget(list_group)

        self.setMinimumSize(400, 550)

    def on_group_filter_changed(self, index):
        """分组筛选改变时刷新列表"""
        self.refresh_presets()

    def open_package(self):
        """打开或创建 package（后台）"""
        pkg = load_or_create_package()
        
        # 如果加载失败且文件不存在，创建新的
        if not pkg and not os.path.exists(self.package_path):
            print("[NodePresets] Creating package on first use")
            if test_create_package():
                pkg = load_or_create_package()
        
        return pkg

    def refresh_groups(self):
        """刷新 UI-only 分组，并自动清理空白/无效分组"""
        self.group_filter.blockSignals(True)

        current_text = self.group_filter.currentText()

        self.group_filter.clear()
        self.group_filter.addItem("(All Groups)")
        self.group_filter.addItem("(Ungrouped)")

        graph_ids = []

        if os.path.exists(self.package_path):
            pkg = None

            try:
                pkg = self.open_package()
                if pkg:
                    graph_ids = list_graph_names(pkg)

            except BaseException as e:
                print("[NodePresets] refresh_groups get graph ids failed:", e)

            finally:
                if pkg:
                    close_package(pkg)

        # 自动清理：
        # 1. 已经不存在的 preset meta
        # 2. 空白 group 字符串
        try:
            cleanup_ui_meta_groups(graph_ids)
        except BaseException as e:
            print("[NodePresets] cleanup_ui_meta_groups failed:", e)

        # 用 graph identifier 自动修复 UI meta
        try:
            repair_ui_meta_from_graph_ids(graph_ids)
        except BaseException as e:
            print("[NodePresets] repair_ui_meta_from_graph_ids failed:", e)

        # 只显示当前真正被 preset 使用的分组
        try:
            self.groups = get_used_ui_groups(graph_ids)
        except BaseException as e:
            print("[NodePresets] get_used_ui_groups failed:", e)
            self.groups = []

        for g in self.groups:
            self.group_filter.addItem(g)

        idx = self.group_filter.findText(current_text)

        if idx >= 0:
            self.group_filter.setCurrentIndex(idx)
        else:
            self.group_filter.setCurrentIndex(0)

        self.group_filter.blockSignals(False)

        print("[NodePresets] UI groups:", self.groups)

    def refresh_presets(self):
        """刷新预设列表，All Groups 下按 UI 分组折叠显示"""

        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.presets = []

        if not os.path.exists(self.package_path):
            print("[NodePresets] Package file does not exist yet")
            return

        pkg = None

        try:
            pkg = self.open_package()

            if not pkg:
                return

            graph_ids = list_graph_names(pkg)

            try:
                cleanup_ui_meta_groups(graph_ids)
            except BaseException as e:
                print("[NodePresets] cleanup_ui_meta_groups failed:", e)

            try:
                repair_ui_meta_from_graph_ids(graph_ids)
            except BaseException as e:
                print("[NodePresets] repair_ui_meta_from_graph_ids failed:", e)

            filter_text = self.group_filter.currentText()
            show_all = (filter_text == "(All Groups)")
            show_ungrouped = (filter_text == "(Ungrouped)")

            ui_meta = load_ui_meta()
            meta_presets = ui_meta.get("presets", {}) or {}

            preset_infos = []

            for name in graph_ids:
                preset_graph = find_graph_by_name(pkg, name)

                if not preset_graph:
                    continue

                preset_group = get_preset_ui_group(name)

                display_name = name

                try:
                    pn_value = preset_graph.getAnnotationPropertyValueFromId("preset_name")
                    if pn_value:
                        display_name = sdvalue_to_python(pn_value) or name
                except BaseException:
                    pass

                try:
                    meta = meta_presets.get(name, {}) or {}
                    if meta.get("display_name"):
                        display_name = str(meta.get("display_name"))
                except BaseException:
                    pass

                try:
                    order_value = meta_presets.get(name, {}).get("order", 0)
                except BaseException:
                    order_value = 0

                preset_infos.append({
                    "graph_id": name,
                    "graph": preset_graph,
                    "group": preset_group,
                    "group_title": preset_group or "Ungrouped",
                    "display_name": display_name,
                    "order": order_value,
                })

            filtered_infos = []

            for info in preset_infos:
                preset_group = info["group"]

                if show_all:
                    filtered_infos.append(info)

                elif show_ungrouped:
                    if not preset_group:
                        filtered_infos.append(info)

                else:
                    if preset_group == filter_text:
                        filtered_infos.append(info)

            filtered_infos.sort(
                key=lambda x: (
                    str(x["group_title"]).lower(),
                    x["order"],
                    str(x["display_name"]).lower()
                )
            )

            if not show_all:
                for info in filtered_infos:
                    self._add_preset_item_to_layout(info)

                return

            grouped = {}

            for info in filtered_infos:
                group_title = info["group_title"]
                grouped.setdefault(group_title, []).append(info)

            def _group_sort_key(group_name):
                if group_name == "Ungrouped":
                    return ("zzzzzz", group_name.lower())
                return ("", group_name.lower())

            for group_title in sorted(grouped.keys(), key=_group_sort_key):
                items = grouped[group_title]

                expanded = self.group_expanded.get(group_title, True)
                deletable = (group_title != "Ungrouped")

                header = GroupHeaderWidget(
                    group_name=group_title,
                    count=len(items),
                    expanded=expanded,
                    deletable=deletable
                )

                header.toggled.connect(self.toggle_group_expanded)
                header.deleteRequested.connect(self.delete_group_by_name)

                self.list_layout.insertWidget(
                    self.list_layout.count() - 1,
                    header
                )

                if not expanded:
                    continue

                for info in items:
                    self._add_preset_item_to_layout(info)

        except Exception as e:
            print("[NodePresets] Refresh failed:", e)
            import traceback
            traceback.print_exc()

        finally:
            if pkg:
                close_package(pkg)


    def _add_preset_item_to_layout(self, info):
        """把单个 preset item 添加到列表 UI"""

        name = info["graph_id"]
        preset_graph = info["graph"]
        preset_group = info["group"]

        display_name = get_preset_display_name_for_ui(name, preset_graph)

        preset_kind = get_preset_ui_kind(name, preset_graph)

        if preset_kind == PRESET_KIND_FUNCTION_GRAPH:
            display_name = "{}  [Function]".format(display_name)

        params_list = []

        try:
            params_detail_value = preset_graph.getAnnotationPropertyValueFromId("params_detail")

            if params_detail_value:
                import json
                params_json = sdvalue_to_python(params_detail_value)

                if params_json:
                    loaded = json.loads(params_json)

                    if isinstance(loaded, list):
                        for item in loaded:
                            if isinstance(item, dict):
                                value = str(item.get("value", ""))

                                try:
                                    fixed_value = _parse_xyzw_string_to_tuple_text(value)
                                    if fixed_value:
                                        value = fixed_value
                                except BaseException:
                                    pass

                                params_list.append({
                                    "identifier": str(item.get("identifier", "")),
                                    "value": value
                                })

        except BaseException as e:
            print("[NodePresets] Failed to load params_detail:", e)

        if not params_list:
            try:
                desc_value = preset_graph.getAnnotationPropertyValueFromId("description")

                if desc_value:
                    params_summary = sdvalue_to_python(desc_value) or ""
                    params_list = parse_params_summary_to_list(params_summary)

            except BaseException as e:
                print("[NodePresets] Failed to parse description:", e)

        item_widget = PresetItemWidget(
            display_name,
            preset_group or "",
            params_list
        )

        current_name = name

        item_widget.load_btn.clicked.connect(
            lambda checked=False, n=current_name: self.load_preset_by_name(n)
        )

        item_widget.delete_btn.clicked.connect(
            lambda checked=False, n=current_name: self.delete_preset_by_name(n)
        )

        if hasattr(item_widget, "name_label") and item_widget.name_label:
            item_widget.name_label.doubleClicked.connect(
                lambda n=current_name: self.rename_preset_by_name(n)
            )

        if hasattr(item_widget, "group_label") and item_widget.group_label:
            item_widget.group_label.clicked.connect(
                lambda n=current_name: self.show_group_menu_for_preset(n)
            )

            item_widget.group_label.doubleClicked.connect(
                lambda n=current_name: self.edit_preset_group_by_name(n)
            )

        if hasattr(item_widget, "rename_btn") and item_widget.rename_btn:
            item_widget.rename_btn.clicked.connect(
                lambda checked=False, n=current_name: self.rename_preset_by_name(n)
            )

        if hasattr(item_widget, "group_btn") and item_widget.group_btn:
            item_widget.group_btn.clicked.connect(
                lambda checked=False, n=current_name: self.edit_preset_group_by_name(n)
            )

        self.list_layout.insertWidget(
            self.list_layout.count() - 1,
            item_widget
        )


    def _create_group_header(self, title):
        """创建分组标题 header widget（已废弃，由 GroupHeaderWidget 替代）"""
        label = QtWidgets.QLabel(title)
        label.setStyleSheet("""
            QLabel {
                color: #ffffff;
                background: #2f2f2f;
                border-radius: 4px;
                padding: 6px 8px;
                font-weight: bold;
                font-size: 13px;
            }
        """)
        label.setMinimumHeight(28)
        return label


    def create_preset_from_selection(self):
        """从选中的节点创建预设"""
        preset_name = safe_name(self.name_edit.text())
        preset_group = self.group_edit.text().strip()

        if not preset_name:
            QtWidgets.QMessageBox.warning(self, "Warning", "请输入预设名称。")
            return

        current_graph = get_current_graph()
        if not current_graph:
            QtWidgets.QMessageBox.warning(self, "Warning", "当前没有打开的 Graph。")
            return

        selected_nodes = get_selected_nodes()
        if not selected_nodes:
            QtWidgets.QMessageBox.warning(self, "Warning", "请先选择节点。")
            return

        # 检测当前 graph 类型
        source_kind = get_preset_resource_kind(current_graph)
        print("[NodePresets] Create preset from kind:", source_kind)
        print("[NodePresets] Source graph class:", get_resource_class_name(current_graph))

        pkg = None

        try:
            pkg = self.open_package()

            if not pkg:
                pkg = get_pkg_mgr().newUserPackage()

            if not pkg:
                QtWidgets.QMessageBox.warning(self, "Error", "无法创建预设 Package。")
                return

            graph_identifier = make_preset_graph_identifier(preset_name, preset_group, source_kind)

            # ------------------------------------------------------------
            # 检查是否已经存在同名 graph
            # 当前规则：
            #   无分组：graph id = 预设名
            #   有分组：graph id = 预设名-分组名
            # ------------------------------------------------------------
            existing = find_graph_by_name(pkg, graph_identifier)

            if existing:
                should_overwrite = ask_overwrite_preset(
                    self,
                    preset_name,
                    preset_group,
                    graph_identifier
                )

                if not should_overwrite:
                    print("[NodePresets] User cancelled overwrite:", graph_identifier)
                    return

                print("[NodePresets] Overwrite preset:", graph_identifier)

                # 复用现有删除预设的核心逻辑
                if not self._delete_preset_graph_in_package(pkg, graph_identifier):
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Error",
                        "覆盖失败：无法删除旧预设。\n\n{}".format(graph_identifier)
                    )
                    return

                # 清理旧 UI meta
                try:
                    remove_preset_ui_meta(graph_identifier)
                except BaseException as e:
                    print("[NodePresets] remove_preset_ui_meta failed:", e)

            # ------------------------------------------------------------
            # 创建新的 preset graph
            # ------------------------------------------------------------
            input_params = extract_input_value_params_from_nodes(selected_nodes)
            print("[NodePresets] Extracted input_params:", input_params)

            params_summary = format_input_value_params_summary(input_params)
            print("[NodePresets] Formatted params_summary:", params_summary)

            preset_graph = create_preset_resource(pkg, graph_identifier, source_kind)

            set_graph_identifier_safe(preset_graph, graph_identifier)

            # UI 显示名只保存基础预设名，不保存 group 后缀
            set_graph_display_name_annotation(preset_graph, preset_name)

            set_preset_ui_meta(
                graph_or_name=graph_identifier,
                group=preset_group,
                display_name=preset_name,
                order=time.time(),
                kind=source_kind
            )

            print("[NodePresets] Stored UI-only group:", graph_identifier, preset_group)

            display_name = self.name_edit.text().strip()
            if display_name:
                set_annotation_string(preset_graph, "preset_name", display_name)
                print("[NodePresets] Stored display name:", display_name)

            # 保存 preset_kind annotation
            try:
                from sd.api.sdvaluestring import SDValueString
                preset_graph.setAnnotationPropertyValueFromId(
                    "preset_kind",
                    SDValueString.sNew(source_kind)
                )
            except BaseException as e:
                print("[NodePresets] set preset_kind annotation failed:", e)

            # 将参数信息存储到 graph 的 description annotation（存储 JSON，带前缀）
            if input_params:
                try:
                    import json
                    from sd.api.sdvaluestring import SDValueString

                    serializable_params = []

                    for param in input_params:
                        identifier = str(param.get("identifier", ""))
                        raw_value = str(param.get("value", ""))

                        # 使用新的格式化函数转换值
                        formatted_value = _parse_xyzw_string_to_tuple_text(raw_value)
                        if formatted_value is None:
                            formatted_value = raw_value

                        serializable_params.append({
                            "identifier": identifier,
                            "value": formatted_value
                        })

                    params_json = json.dumps(serializable_params, ensure_ascii=False)
                    desc_json = PARAMS_JSON_PREFIX + params_json

                    print("[NodePresets] ABOUT TO STORE description:", desc_json)

                    desc_value = SDValueString.sNew(desc_json)
                    preset_graph.setAnnotationPropertyValueFromId("description", desc_value)

                    # 立刻读回来验证
                    check_value = preset_graph.getAnnotationPropertyValueFromId("description")
                    check_text = sdvalue_to_python(check_value) if check_value else None
                    print("[NodePresets] STORED description CHECK:", check_text)

                except BaseException as e:
                    print("[NodePresets] Failed to store description:", e)
                    import traceback
                    traceback.print_exc()
            else:
                print("[NodePresets] No input_params to store")

            # 将完整参数列表存储为 JSON 字符串到 params_detail（用于详细显示）
            if input_params:
                try:
                    import json
                    from sd.api.sdvaluestring import SDValueString

                    serializable_params = []

                    for param in input_params:
                        identifier = str(param.get("identifier", ""))
                        raw_value = str(param.get("value", ""))

                        # 使用新的格式化函数转换值
                        formatted_value = _parse_xyzw_string_to_tuple_text(raw_value)
                        if formatted_value is None:
                            formatted_value = raw_value

                        serializable_params.append({
                            "identifier": identifier,
                            "value": formatted_value
                        })

                    params_json = json.dumps(serializable_params, ensure_ascii=False)

                    print("[NodePresets] ABOUT TO STORE params_detail:", params_json)

                    params_value = SDValueString.sNew(params_json)
                    preset_graph.setAnnotationPropertyValueFromId("params_detail", params_value)

                    # 立刻读回来验证
                    check_value = preset_graph.getAnnotationPropertyValueFromId("params_detail")
                    check_json = sdvalue_to_python(check_value) if check_value else None
                    print("[NodePresets] STORED params_detail CHECK:", check_json)

                except BaseException as e:
                    print("[NodePresets] Failed to store params_detail:", e)
                    import traceback
                    traceback.print_exc()
            else:
                print("[NodePresets] No input_params to store")

            # 复制节点
            created = copy_nodes_to_graph(
                src_nodes=selected_nodes,
                dst_graph=preset_graph,
                offset_x=0,
                offset_y=0,
                src_graph=current_graph
            )

            if not created:
                QtWidgets.QMessageBox.warning(self, "Error", "没有成功保存任何节点。")
                return

            # 保存 package
            if not save_package(pkg, self.package_path):
                QtWidgets.QMessageBox.warning(self, "Error", "保存 Package 失败。")
                return

            self.name_edit.clear()
            self.group_edit.clear()

            # 显示保存信息，包含参数值
            msg = "预设 '{}' 已保存。\n节点数量：{}".format(preset_name, len(created))
            if input_params:
                msg += "\n\nInput Value 参数 ({}):\n".format(len(input_params))
                msg += format_input_value_params_full(input_params)

            QtWidgets.QMessageBox.information(self, "Success", msg)

            # 刷新列表和分组
            self.refresh_groups()
            self.refresh_presets()

        except Exception as e:
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.warning(self, "Error", "保存失败：{}".format(e))

        finally:
            # 确保卸载
            if pkg:
                close_package(pkg)

    def load_selected_preset(self):
        """加载选中的预设（保留方法，供未来使用）"""
        QtWidgets.QMessageBox.information(
            self, "Info", "请通过每个预设右侧的 Load 按钮来加载预设。"
        )

    def delete_selected_preset(self):
        """删除选中的预设（已弃用，请使用 delete_preset_by_name）"""
        QtWidgets.QMessageBox.information(
            self, "Info", "请通过每个预设右侧的 Delete 按钮来删除预设。"
        )

    def load_preset_by_name(self, preset_name):
        """通过预设名称加载（用于按钮回调）"""
        target_graph = get_current_graph()
        if not target_graph:
            QtWidgets.QMessageBox.warning(self, "Warning", "当前没有打开的 Graph。")
            return

        if not os.path.exists(self.package_path):
            QtWidgets.QMessageBox.warning(self, "Error", "预设 Package 不存在。")
            return

        pkg = None

        try:
            pkg = self.open_package()

            if not pkg:
                QtWidgets.QMessageBox.warning(self, "Error", "无法加载预设 Package。")
                return

            preset_graph = find_graph_by_name(pkg, preset_name)
            if not preset_graph:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Error",
                    "找不到预设：{}".format(preset_name)
                )
                return

            # 检查 preset 类型和当前 graph 类型是否一致
            preset_kind = get_preset_ui_kind(preset_name, preset_graph)
            target_kind = get_preset_resource_kind(target_graph)

            if preset_kind != target_kind:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Type Mismatch",
                    "当前预设类型和目标 graph 类型不一致。\n\n"
                    "预设类型：{}\n"
                    "当前目标：{}\n\n"
                    "Function 预设只能加载到 Function Graph，普通 Graph 预设只能加载到普通 Graph。".format(
                        preset_kind,
                        target_kind
                    )
                )
                return

            try:
                src_nodes = list(preset_graph.getNodes())
            except Exception:
                src_nodes = []

            if not src_nodes:
                QtWidgets.QMessageBox.warning(self, "Warning", "该预设 Graph 中没有节点。")
                return

            # 计算偏移量，使预设节点居中到当前 graph view 的中间
            preset_center = get_nodes_bounding_box_center(src_nodes)
            view_center_x, view_center_y = get_current_graph_view_center()

            if preset_center:
                offset_x = view_center_x - preset_center[0]
                offset_y = view_center_y - preset_center[1]
                print("[NodePresets] Centering preset: preset_center=({:.0f}, {:.0f}), "
                      "view_center=({:.0f}, {:.0f}), "
                      "offset=({:.0f}, {:.0f})".format(
                          preset_center[0], preset_center[1],
                          view_center_x, view_center_y,
                          offset_x, offset_y))
            else:
                offset_x = 0
                offset_y = 0

            # 复制节点到当前 graph
            created = copy_nodes_to_graph(
                src_nodes=src_nodes,
                dst_graph=target_graph,
                offset_x=offset_x,
                offset_y=offset_y,
                src_graph=preset_graph
            )

            if created:
                QtWidgets.QMessageBox.information(
                    self,
                    "Success",
                    "已加载预设 '{}'\n节点数量：{}".format(preset_name, len(created))
                )
            else:
                QtWidgets.QMessageBox.warning(self, "Warning", "没有成功创建任何节点。")

        except Exception as e:
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.warning(self, "Error", "加载失败：{}".format(e))

        finally:
            if pkg:
                close_package(pkg)

    def rename_preset_by_name(self, preset_name):
        """重命名预设，保持当前分组。"""
        if not preset_name:
            return

        if not os.path.exists(self.package_path):
            QtWidgets.QMessageBox.warning(self, "Error", "预设 Package 不存在。")
            return

        pkg = None

        try:
            pkg = self.open_package()

            if not pkg:
                QtWidgets.QMessageBox.warning(self, "Error", "无法打开预设 Package。")
                return

            preset_graph = find_graph_by_name(pkg, preset_name)

            if not preset_graph:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Error",
                    "找不到预设：{}".format(preset_name)
                )
                return

            try:
                old_identifier = preset_graph.getIdentifier()
            except BaseException:
                old_identifier = preset_name

            current_group = get_preset_ui_group(old_identifier) or ""
            current_display_name = get_preset_display_name_for_ui(old_identifier, preset_graph)

            new_name, ok = QtWidgets.QInputDialog.getText(
                self,
                "Rename Preset",
                "New preset name:",
                QtWidgets.QLineEdit.Normal,
                str(current_display_name)
            )

            if not ok:
                return

            new_base_name = safe_name(str(new_name).strip())

            if not new_base_name:
                QtWidgets.QMessageBox.warning(self, "Warning", "预设名称不能为空。")
                return

            preset_kind = get_preset_ui_kind(old_identifier, preset_graph)

            new_identifier = make_preset_graph_identifier(new_base_name, current_group, preset_kind)

            if new_identifier == old_identifier:
                return

            existing = find_graph_by_name(pkg, new_identifier)

            if existing:
                try:
                    existing_id = existing.getIdentifier()
                except BaseException:
                    existing_id = None

                if existing_id != old_identifier:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Name Exists",
                        "预设 '{}' 在分组 '{}' 中已存在。".format(
                            new_base_name,
                            current_group or "Ungrouped"
                        )
                    )
                    return

            reply = QtWidgets.QMessageBox.question(
                self,
                "Confirm Rename",
                "确定要将预设：\n\n{}\n\n重命名为：\n\n{} ?".format(
                    old_identifier,
                    new_identifier
                ),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes
            )

            if reply != QtWidgets.QMessageBox.Yes:
                return

            if not set_graph_identifier_safe(preset_graph, new_identifier):
                QtWidgets.QMessageBox.warning(
                    self,
                    "Error",
                    "修改 Graph Identifier 失败。"
                )
                return

            set_graph_display_name_annotation(preset_graph, new_base_name)
            print("[NodePresets] Renamed graph identifier: {} -> {}".format(
                old_identifier,
                new_identifier
            ))

            rename_preset_ui_meta(
                old_graph_id=old_identifier,
                new_graph_id=new_identifier,
                display_name=new_base_name
            )

            set_preset_ui_meta(
                graph_or_name=new_identifier,
                group=current_group,
                display_name=new_base_name,
                kind=preset_kind
            )

            if not save_package(pkg, self.package_path):
                QtWidgets.QMessageBox.warning(self, "Error", "保存 Package 失败。")
                return

            try:
                self.refresh_groups()
            except BaseException:
                pass

            self.refresh_presets()

            QtWidgets.QMessageBox.information(
                self,
                "Success",
                "预设已重命名为：{}".format(new_base_name)
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.warning(self, "Error", "重命名失败：{}".format(e))

        finally:
            if pkg:
                close_package(pkg)

    def delete_preset_by_name(self, preset_name):
        """通过预设名称删除（用于按钮回调）"""
        reply = QtWidgets.QMessageBox.question(
            self,
            "Confirm Delete",
            "确定要删除预设 '{}' 吗？".format(preset_name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )

        if reply != QtWidgets.QMessageBox.Yes:
            return

        pkg = None

        try:
            pkg = self.open_package()
            if not pkg:
                QtWidgets.QMessageBox.warning(self, "Error", "无法打开预设 Package。")
                return

            ok = self._delete_preset_graph_in_package(pkg, preset_name)

            if not ok:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Error",
                    "删除预设失败：{}".format(preset_name)
                )
                return

            if not save_package(pkg, self.package_path):
                QtWidgets.QMessageBox.warning(self, "Error", "保存 Package 失败。")
                return

            try:
                remove_preset_ui_meta(preset_name)
            except BaseException as e:
                print("[NodePresets] remove_preset_ui_meta failed:", e)

            self.refresh_groups()
            self.refresh_presets()
            QtWidgets.QMessageBox.information(
                self,
                "Success",
                "预设 '{}' 已删除。".format(preset_name)
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.warning(self, "Error", "删除失败：{}".format(e))

        finally:
            if pkg:
                close_package(pkg)

    def _delete_preset_graph_in_package(self, pkg, graph_identifier):
        """删除 package 中的 preset graph（内部函数，不弹窗不刷新）。

        给 delete_preset_by_name() 和覆盖保存共用。
        """
        if not pkg or not graph_identifier:
            return False

        preset_graph = find_graph_by_name(pkg, graph_identifier)

        if not preset_graph:
            print("[NodePresets] _delete_preset_graph_in_package: graph not found:", graph_identifier)
            return True

        try:
            real_id = preset_graph.getIdentifier()
        except BaseException:
            real_id = graph_identifier

        print("[NodePresets] Deleting preset graph:", real_id)

        try:
            pkg.deleteGraph(preset_graph)
        except Exception:
            try:
                preset_graph.delete()
            except Exception:
                pass

        print("[NodePresets] Deleted preset graph:", real_id)
        return True

    def show_group_menu_for_preset(self, preset_name):
        """单击分组标签：从现有分组里选择"""
        if not preset_name:
            return

        menu = QtWidgets.QMenu(self)

        current_group = ""
        try:
            current_group = get_preset_ui_group(preset_name) or ""
        except BaseException:
            current_group = ""

        ungrouped_action = menu.addAction("(Ungrouped)")
        ungrouped_action.setData("")

        menu.addSeparator()

        groups = []

        try:
            groups = list(self.groups)
        except BaseException:
            groups = []

        if not groups:
            try:
                data = load_ui_meta()
                presets = data.get("presets", {}) or {}
                group_set = set()

                for _, meta in presets.items():
                    g = str(meta.get("group", "") or "").strip()
                    if g:
                        group_set.add(g)

                groups = sorted(group_set)

            except BaseException:
                groups = []

        for g in groups:
            if not g:
                continue

            action = menu.addAction(g)

            if g == current_group:
                action.setCheckable(True)
                action.setChecked(True)

            action.setData(g)

        menu.addSeparator()

        new_action = menu.addAction("New / Edit Group...")
        new_action.setData("__edit__")

        if current_group:
            delete_group_action = menu.addAction("Delete Group '{}'...".format(current_group))
            delete_group_action.setData("__delete_current_group__")

        try:
            selected = menu.exec(QtGui.QCursor.pos())
        except AttributeError:
            selected = menu.exec_(QtGui.QCursor.pos())

        if not selected:
            return

        value = selected.data()

        if value == "__edit__":
            self.edit_preset_group_by_name(preset_name)
            return

        if value == "__delete_current_group__":
            idx = self.group_filter.findText(current_group)
            if idx >= 0:
                self.group_filter.setCurrentIndex(idx)

            self.delete_current_group()
            return

        new_group = str(value or "").strip()

        self.move_preset_to_group(preset_name, new_group)

    def edit_preset_group_by_name(self, preset_name):
        """双击分组标签：手动输入该预设的 UI 分组"""
        if not preset_name:
            return

        current_group = ""

        try:
            current_group = get_preset_ui_group(preset_name) or ""
        except BaseException:
            current_group = ""

        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "Edit Preset Group",
            "Group name:",
            QtWidgets.QLineEdit.Normal,
            current_group
        )

        if not ok:
            return

        new_group = str(text).strip()

        self.move_preset_to_group(preset_name, new_group)

    def change_preset_group(self, preset_name):
        """修改 UI 层分组，同时更新 package graph identifier"""
        current_group = get_preset_ui_group(preset_name) or ""

        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "Change UI Group",
            "Group name:",
            QtWidgets.QLineEdit.Normal,
            current_group
        )

        if not ok:
            return

        new_group = text.strip()

        self.move_preset_to_group(preset_name, new_group)

    def delete_current_group(self):
        """删除当前选中的 UI 分组，不删除 preset"""
        group_name = self.group_filter.currentText().strip()

        if not group_name:
            return

        if group_name in ["", "(All Groups)", "(Ungrouped)"]:
            QtWidgets.QMessageBox.information(
                self,
                "Info",
                "这个分组不能删除。"
            )
            return

        self.delete_group_by_name(group_name)

    def toggle_group_expanded(self, group_name):
        """All Groups 下点击分组标题展开 / 收起"""
        if not group_name:
            return

        current = self.group_expanded.get(group_name, True)
        self.group_expanded[group_name] = not current

        self.refresh_presets()

    def delete_group_by_name(self, group_name):
        """删除指定 UI 分组。

        用户可选择：
            1. 只删除分组，保留预设
            2. 删除分组和组内所有预设
        """
        group_name = str(group_name or "").strip()

        if not group_name or group_name == "Ungrouped":
            return

        graph_ids = []

        try:
            data = load_ui_meta()
            presets = data.get("presets", {}) or {}

            for graph_id, meta in presets.items():
                g = str((meta or {}).get("group", "") or "").strip()
                if g == group_name:
                    graph_ids.append(graph_id)

        except BaseException:
            graph_ids = []

        if not graph_ids:
            current_ids = []
            try:
                pkg = self.open_package()
                if pkg:
                    current_ids = list_graph_names(pkg)
            except BaseException:
                pass
            finally:
                if pkg:
                    close_package(pkg)

            try:
                cleanup_ui_meta_groups(current_ids)
            except BaseException:
                pass

            if group_name in self.group_expanded:
                del self.group_expanded[group_name]

            self.refresh_groups()
            self.refresh_presets()
            return

        mode = ask_delete_group_mode(
            self,
            group_name,
            len(graph_ids)
        )

        if mode is None:
            return

        # ------------------------------------------------------------
        # 模式 A：只删除分组，保留预设
        # ------------------------------------------------------------
        if mode == "keep":
            failed = []

            for graph_id in list(graph_ids):
                ok = self.move_preset_to_group(graph_id, "")
                if not ok:
                    failed.append(graph_id)

            if group_name in self.group_expanded:
                del self.group_expanded[group_name]

            self.refresh_groups()
            self.refresh_presets()

            if failed:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Warning",
                    "分组已部分删除，但以下预设移动到未分组时发生冲突或失败：\n\n{}".format(
                        "\n".join(failed)
                    )
                )
            else:
                QtWidgets.QMessageBox.information(
                    self,
                    "Success",
                    "分组 '{}' 已删除，组内预设已移动到未分组。".format(group_name)
                )
            return

        # ------------------------------------------------------------
        # 模式 B：删除分组和组内所有预设
        # ------------------------------------------------------------
        if mode == "delete":
            reply = QtWidgets.QMessageBox.warning(
                self,
                "Confirm Delete Presets",
                "即将删除分组 '{}' 以及其中的 {} 个预设。\n\n"
                "此操作会删除 package 中对应的 preset graph。\n"
                "确定继续？".format(
                    group_name,
                    len(graph_ids)
                ),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No
            )

            if reply != QtWidgets.QMessageBox.Yes:
                return

            pkg = None
            deleted = []
            failed = []

            try:
                pkg = self.open_package()

                if not pkg:
                    QtWidgets.QMessageBox.warning(self, "Error", "无法打开预设 Package。")
                    return

                for graph_id in list(graph_ids):
                    try:
                        ok = self._delete_preset_graph_in_package(pkg, graph_id)
                        if ok:
                            deleted.append(graph_id)
                            try:
                                remove_preset_ui_meta(graph_id)
                            except BaseException as e:
                                print("[NodePresets] remove_preset_ui_meta failed:", e)
                        else:
                            failed.append(graph_id)
                    except BaseException as e:
                        print("[NodePresets] delete preset in group failed:", graph_id, e)
                        failed.append(graph_id)

                if not save_package(pkg, self.package_path):
                    QtWidgets.QMessageBox.warning(self, "Error", "保存 Package 失败。")
                    return

                print("[NodePresets] Deleted group presets:", deleted)

                if failed:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Warning",
                        "以下预设删除失败：\n\n{}".format("\n".join(failed))
                    )

            except Exception as e:
                import traceback
                traceback.print_exc()
                QtWidgets.QMessageBox.warning(
                    self,
                    "Error",
                    "删除组内预设失败：{}".format(e)
                )
            finally:
                if pkg:
                    close_package(pkg)

            if group_name in self.group_expanded:
                del self.group_expanded[group_name]

            self.refresh_groups()
            self.refresh_presets()

            if not failed:
                QtWidgets.QMessageBox.information(
                    self,
                    "Success",
                    "分组 '{}' 及组内预设已删除。".format(group_name)
                )

    def move_preset_to_group(self, preset_name, new_group):
        """修改 preset 的 UI 分组，同时重命名 package graph identifier。"""
        if not preset_name:
            return False

        new_group = str(new_group or "").strip()

        pkg = None

        try:
            pkg = self.open_package()

            if not pkg:
                QtWidgets.QMessageBox.warning(self, "Error", "无法打开预设 Package。")
                return False

            preset_graph = find_graph_by_name(pkg, preset_name)

            if not preset_graph:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Error",
                    "找不到预设：{}".format(preset_name)
                )
                return False

            old_identifier = preset_graph.getIdentifier()

            base_name = get_preset_display_name_for_ui(old_identifier, preset_graph)
            base_name = safe_name(base_name)

            if not base_name:
                base_name = get_preset_base_name_from_graph_id(old_identifier)

            preset_kind = get_preset_ui_kind(old_identifier, preset_graph)

            new_identifier = make_preset_graph_identifier(base_name, new_group, preset_kind)

            if new_identifier == old_identifier:
                set_preset_ui_meta(
                    graph_or_name=old_identifier,
                    group=new_group,
                    display_name=base_name
                )
                self.refresh_groups()
                self.refresh_presets()
                return True

            existing = find_graph_by_name(pkg, new_identifier)

            if existing:
                try:
                    existing_id = existing.getIdentifier()
                except BaseException:
                    existing_id = None

                if existing_id != old_identifier:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Name Exists",
                        "分组 '{}' 中已经存在同名预设 '{}'\n\n目标 Graph 名：{}".format(
                            new_group or "Ungrouped",
                            base_name,
                            new_identifier
                        )
                    )
                    return False

            try:
                preset_graph.setIdentifier(new_identifier)
            except BaseException as e:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Error",
                    "修改 Graph 名称失败：{}".format(e)
                )
                return False

            set_graph_display_name_annotation(preset_graph, base_name)

            if not save_package(pkg, self.package_path):
                QtWidgets.QMessageBox.warning(self, "Error", "保存 Package 失败。")
                return False

            rename_preset_ui_meta(
                old_graph_id=old_identifier,
                new_graph_id=new_identifier,
                display_name=base_name
            )

            set_preset_ui_meta(
                graph_or_name=new_identifier,
                group=new_group,
                display_name=base_name,
                kind=preset_kind
            )

            self.refresh_groups()
            self.refresh_presets()

            return True

        except Exception as e:
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.warning(self, "Error", "修改分组失败：{}".format(e))
            return False

        finally:
            if pkg:
                close_package(pkg)

    def delete_selected_preset(self):
        """删除选中的预设（已弃用，请使用 delete_preset_by_name）"""
        QtWidgets.QMessageBox.information(
            self, "Info", "请通过每个预设右侧的 Delete 按钮来删除预设。"
        )

    def delete_preset_by_name(self, preset_name):
        """通过预设名称删除（用于按钮回调）"""
        reply = QtWidgets.QMessageBox.question(
            self,
            "Confirm Delete",
            "确定要删除预设 '{}' 吗？".format(preset_name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )

        if reply != QtWidgets.QMessageBox.Yes:
            return

        pkg = None

        try:
            pkg = self.open_package()
            if not pkg:
                QtWidgets.QMessageBox.warning(self, "Error", "无法打开预设 Package。")
                return

            ok = self._delete_preset_graph_in_package(pkg, preset_name)

            if not ok:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Error",
                    "删除预设失败：{}".format(preset_name)
                )
                return

            if not save_package(pkg, self.package_path):
                QtWidgets.QMessageBox.warning(self, "Error", "保存 Package 失败。")
                return

            try:
                remove_preset_ui_meta(preset_name)
            except BaseException as e:
                print("[NodePresets] remove_preset_ui_meta failed:", e)

            self.refresh_groups()
            self.refresh_presets()
            QtWidgets.QMessageBox.information(
                self,
                "Success",
                "预设 '{}' 已删除。".format(preset_name)
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.warning(self, "Error", "删除失败：{}".format(e))

        finally:
            if pkg:
                close_package(pkg)


# =============================================================================
# Plugin Entry
# =============================================================================

preset_dock = None


def initializeSDPlugin():
    """插件初始化函数"""
    global preset_dock
    
    # 不在初始化时创建 package，等到第一次使用时再创建
    package_path = get_package_path()
    if not os.path.exists(package_path):
        print("[NodePresets] Package will be created on first use")
    
    app = get_app()
    ui_mgr = app.getQtForPythonUIMgr()
    
    if not ui_mgr:
        return
    
    preset_dock = ui_mgr.newDockWidget(
        identifier=DOCK_ID,
        title="Node Presets"
    )
    
    widget = NodePresetWidget()
    
    if hasattr(preset_dock, "setWidget"):
        preset_dock.setWidget(widget)
    else:
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(widget)
        preset_dock.setLayout(layout)
    
    try:
        preset_dock.setVisible(True)
        preset_dock.show()
    except Exception as e:
        print("[NodePresets] Show dock failed:", e)

def uninitializeSDPlugin():
    """插件卸载函数"""
    global preset_dock
    
    # 清理缓存的 package
    print("[NodePresets] Plugin unloading, cleaning up...")
    invalidate_package_cache()
    
    if preset_dock:
        try:
            preset_dock.close()
        except Exception:
            pass
    
    preset_dock = None
