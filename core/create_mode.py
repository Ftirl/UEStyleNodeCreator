"""
UE Style Node Creator - Create Mode Controller

Workflow:
    Long-press shortcut key → route left/right click independently to JSON or SBS
    Release key → exit create mode
    Short press → forwarded to SD as normal
"""

import math

import sd

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets


class CreateModeController(QtCore.QObject):

    IDLE = 0
    PLACING = 1

    mode_changed = QtCore.Signal(int, str)
    selection_shortcut_triggered = QtCore.Signal()

    def __init__(self, shortcut_manager, graph_manager, config=None, node_database=None,
                 preset_module=None):
        super().__init__()
        self._sm = shortcut_manager
        self._gm = graph_manager
        self._config = config
        self._node_db = node_database
        self._preset_module = preset_module

        self._state = self.IDLE
        self._pending = None
        self._graph_view = None
        self._last_interacted_graph_view = None
        self._main_window = None
        self._cursor_overridden = False

        # Long-press detection
        self._hold_timer = QtCore.QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._on_hold_timeout)
        self._held_key = None
        self._held_target = None
        self._held_node_info = None
        self._indicator = None
        self._programmatic_selection_click = False
        self._properties_focus_token = 0

    # ==================================================================
    # Lifecycle
    # ==================================================================

    def initialize(self):
        try:
            app = sd.getContext().getSDApplication()
            ui_mgr = app.getQtForPythonUIMgr()
            self._main_window = ui_mgr.getMainWindow()
        except BaseException as e:
            print("[UEStyleNodeCreator] Failed to init: {}".format(e))
            return False

        qapp = QtWidgets.QApplication.instance()
        if qapp is not None:
            qapp.installEventFilter(self)

        if self._main_window is not None:
            self._graph_view = self._find_graph_view(self._main_window)
            if self._graph_view is not None:
                self._graph_view.installEventFilter(self)
                try:
                    vp = self._graph_view.viewport()
                    if vp is not None:
                        vp.installEventFilter(self)
                except BaseException:
                    pass
        return True

    def shutdown(self):
        self._properties_focus_token += 1
        self.cancel()
        qapp = QtWidgets.QApplication.instance()
        if qapp is not None:
            try:
                qapp.removeEventFilter(self)
            except BaseException:
                pass
        if self._graph_view is not None:
            try:
                self._graph_view.removeEventFilter(self)
            except BaseException:
                pass
            try:
                vp = self._graph_view.viewport()
                if vp:
                    vp.removeEventFilter(self)
            except BaseException:
                pass
            self._graph_view = None

    # ==================================================================
    # Event Filter
    # ==================================================================

    def eventFilter(self, watched, event):
        etype = event.type()

        # QApplication's global filter sees the real widget used by Designer
        # for graph interaction. Remember it when the user pans, zooms or
        # clicks; this is more reliable than guessing a QGraphicsView at init.
        if etype in (
                QtCore.QEvent.Type.MouseButtonPress,
                QtCore.QEvent.Type.MouseMove,
                QtCore.QEvent.Type.Wheel):
            interacted_view = self._graphics_view_for_widget(watched)
            if interacted_view is not None and self._looks_like_graph_view(interacted_view):
                self._last_interacted_graph_view = interacted_view

        if etype == QtCore.QEvent.Type.KeyPress:
            if self._handle_key_press(event, watched):
                return True

        if etype == QtCore.QEvent.Type.KeyRelease:
            if self._handle_key_release(event):
                return True

        if etype == QtCore.QEvent.Type.ShortcutOverride:
            if self._state == self.PLACING and event.key() == QtCore.Qt.Key.Key_Escape:
                self.cancel()
                return True
            # Shortcut detection for ALL keys (S, F, H, Z, V & others SD may consume)
            if self._try_start_hold(event):
                event.accept()
                return True

        if etype == QtCore.QEvent.Type.MouseMove:
            if self._indicator is not None and self._indicator.isVisible():
                self._indicator.move_to_cursor()

        if (etype == QtCore.QEvent.Type.ContextMenu and
                self._state == self.PLACING):
            return True

        if etype == QtCore.QEvent.Type.MouseButtonPress:
            # A Properties jump can be implemented by sending a real Graph
            # View click.  Let Designer consume that click instead of treating
            # it as another placement click.
            if self._programmatic_selection_click:
                return False
            if self._state == self.PLACING:
                button = event.button()
                if button in (QtCore.Qt.MouseButton.LeftButton,
                              QtCore.Qt.MouseButton.RightButton):
                    target_name = (
                        "left" if button == QtCore.Qt.MouseButton.LeftButton
                        else "right")
                    target = (self._pending or {}).get(target_name)
                    click_context = self._graph_click_context(watched, event)
                    if target is not None and click_context is not None:
                        graph_view, sp, global_pos = click_context
                        print(
                            "[UEStyleNodeCreator] {} placement click -> {}".format(
                                "Right" if target_name == "right" else "Left",
                                target.get("preset_name",
                                           target.get("node_name", target_name))))
                        self._place_node_at(
                            (sp.x(), sp.y()),
                            entry=target,
                            properties_graph_view=graph_view,
                            properties_click_global_pos=global_pos,
                        )
                        return True
                    # Consume a right-click only after confirming it belongs
                    # to the graph. The app-wide filter can first see an
                    # internal child that cannot mapToScene; consuming there
                    # prevents the real Graph View from receiving the event.
                    if (button == QtCore.Qt.MouseButton.RightButton and
                            click_context is not None):
                        return True

        return False

    # ==================================================================
    # Key Handling
    # ==================================================================

    def _handle_key_press(self, event, watched=None):
        key = event.key()
        modifiers = event.modifiers()

        if event.isAutoRepeat():
            if self._held_key is not None and key == self._held_key:
                return True
            return False

        if key == QtCore.Qt.Key.Key_Escape:
            if self._state == self.PLACING:
                self.cancel()
                return True
            return False

        if key in (QtCore.Qt.Key.Key_Shift, QtCore.Qt.Key.Key_Control,
                    QtCore.Qt.Key.Key_Alt, QtCore.Qt.Key.Key_Meta):
            return False

        if self._is_text_input_focused():
            return False

        ctrl_alt = (QtCore.Qt.KeyboardModifier.ControlModifier
                     | QtCore.Qt.KeyboardModifier.AltModifier
                     | QtCore.Qt.KeyboardModifier.MetaModifier)
        if modifiers & ctrl_alt:
            return False

        char = self._key_to_char(key, modifiers)
        if char is None:
            return False

        targets = self._sm.get_targets_for_key(char)
        if targets is None:
            return False

        # If already holding, ignore — prevent accidental double-trigger
        if self._held_key is not None or self._hold_timer.isActive():
            return True

        if self._state == self.PLACING:
            self._pending = targets
            self._held_key = key
            self.mode_changed.emit(
                self.PLACING, self._target_description(targets))
            return True

        self._held_key = key
        self._held_target = watched
        self._held_node_info = targets
        delay = self._config.get_setting("hold_delay_ms", 400) if self._config else 400
        self._hold_timer.start(delay)
        return True

    def _try_start_hold(self, event):
        """Try to start long-press detection from ShortcutOverride.
        Also handles the 'From Selection' shortcut (immediate action)."""
        if self._state != self.IDLE:
            return False
        # Ignore if a hold is already in progress (prevents accidental double-trigger)
        if self._hold_timer.isActive():
            return False
        key = event.key()
        if event.isAutoRepeat():
            return False

        char = self._key_to_char(key, event.modifiers())
        if char is None:
            return False

        # 'From Selection' shortcut — immediate action, no hold
        sel_key = self._config.get_setting("selection_shortcut", "") if self._config else ""
        if sel_key and self._match_combo(key, event.modifiers(), sel_key):
            if self._is_text_input_focused():
                return False
            event.accept()
            self.selection_shortcut_triggered.emit()
            return True

        # Normal shortcut — long press
        targets = self._sm.get_targets_for_key(char)
        if targets is None:
            return False
        if self._is_text_input_focused():
            return False
        if event.modifiers() & (
            QtCore.Qt.KeyboardModifier.ControlModifier
            | QtCore.Qt.KeyboardModifier.AltModifier
            | QtCore.Qt.KeyboardModifier.MetaModifier
        ):
            return False

        self._held_key = key
        self._held_target = None
        self._held_node_info = targets
        delay = self._config.get_setting("hold_delay_ms", 400) if self._config else 400
        self._hold_timer.start(delay)
        return True

    def _handle_key_release(self, event):
        key = event.key()
        if event.isAutoRepeat():
            return False

        if self._held_key is not None and key == self._held_key:
            if self._hold_timer.isActive():
                self._hold_timer.stop()
                target = self._held_target
                k = self._held_key
                self._clear_held()
                self._resend_short_press(k, target)
                return False
            if self._state == self.PLACING:
                self._clear_held()
                self.cancel()
                return False
            self._clear_held()
            return False
        return False

    def _on_hold_timeout(self):
        if self._held_node_info is None:
            self._clear_held()
            return
        targets = self._held_node_info
        self._held_node_info = None
        self._held_target = None
        self._show_indicator()
        self._enter_placement(targets)

    def _clear_held(self):
        self._hold_timer.stop()
        self._held_key = None
        self._held_target = None
        self._held_node_info = None
        self._hide_indicator()

    def _resend_short_press(self, key, target):
        receiver = target or QtWidgets.QApplication.focusWidget()
        if receiver is None:
            return
        press = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, key, QtCore.Qt.KeyboardModifier.NoModifier, "")
        release = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyRelease, key, QtCore.Qt.KeyboardModifier.NoModifier, "")
        QtWidgets.QApplication.postEvent(receiver, press)
        QtWidgets.QApplication.postEvent(receiver, release)

    # ==================================================================
    # Placement
    # ==================================================================

    @staticmethod
    def _target_description(targets):
        parts = []
        for button, prefix in (("left", "LMB"), ("right", "RMB")):
            entry = (targets or {}).get(button)
            if entry is None:
                continue
            name = entry.get(
                "preset_name", entry.get("node_name", "Entry"))
            kind = "SBS" if entry.get("entry_type") == "preset" else "JSON"
            parts.append("{} {}: {}".format(prefix, kind, name))
        return " | ".join(parts)

    def _enter_placement(self, targets):
        self._state = self.PLACING
        self._pending = targets
        if not self._cursor_overridden:
            QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.CrossCursor))
            self._cursor_overridden = True
        self.mode_changed.emit(
            self.PLACING, self._target_description(targets))

    def cancel(self):
        if self._state == self.IDLE:
            return
        self._state = self.IDLE
        self._pending = None
        self._clear_held()
        if self._cursor_overridden:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._cursor_overridden = False
        self.mode_changed.emit(self.IDLE, "")

    def _place_node_at(self, graph_pos, entry=None, properties_graph_view=None,
                       properties_click_global_pos=None):
        if self._state != self.PLACING or self._pending is None:
            return
        pending = entry
        if pending is None:
            # Compatibility with callers from before mouse-button routing.
            pending = self._pending
            if "left" in pending or "right" in pending:
                pending = pending.get("left") or pending.get("right")
        if pending is None:
            return

        if pending.get("entry_type") == "preset":
            self._place_preset_at(graph_pos, pending)
            return

        node_uid = pending["node_uid"]

        node = self._gm.create_node(
            node_uid,
            position=graph_pos,
            node_label=pending.get("node_name", ""),
            node_database=self._node_db,
            **{k: v for k, v in pending.items()
               if k not in ("node_uid", "node_name")},
        )

        if node is None:
            print("[UEStyleNodeCreator] ERROR: Node creation failed!")
            return

        actual_pos = self._gm.get_node_position(node)
        print("[UEStyleNodeCreator] Created '{}' OK — pos=({:.0f},{:.0f})".format(
            pending["node_name"], actual_pos[0], actual_pos[1]))

        if self._config is None or self._config.get_setting("enable_auto_connect", True):
            selected = self._gm.get_selected_nodes()
            if selected:
                count = self._gm.try_auto_connect(node, selected)
                if count:
                    print("[UEStyleNodeCreator] Auto-connected {} input(s).".format(count))

        # The graph API creates the node but does not necessarily make it the
        # active UI selection.  Defer this until the current mouse event has
        # finished so Designer has had a chance to create the visual item.
        if (self._config is not None and
                self._config.get_setting("open_properties_after_create", False)):
            self._schedule_properties_focus(
                node, properties_graph_view, properties_click_global_pos)

    def _place_preset_at(self, graph_pos, preset_info=None):
        if self._preset_module is None:
            print("[UEStyleNodeCreator] ERROR: NodePreset module is unavailable.")
            return
        preset_info = preset_info or self._pending
        preset_id = preset_info.get("preset_id", "")
        try:
            created = self._preset_module.create_at(preset_id, graph_pos)
        except BaseException as exc:
            print("[UEStyleNodeCreator] ERROR creating SBS preset '{}': {}".format(
                preset_id, exc))
            return
        if not created:
            print("[UEStyleNodeCreator] ERROR: SBS preset created no nodes.")
            return
        print("[UEStyleNodeCreator] Created SBS preset '{}' ({} nodes).".format(
            preset_info.get("preset_name", preset_id), len(created)))

    def _focus_properties_after_create(self, node, graph_view=None,
                                       click_global_pos=None):
        """Select the new node using a Graph View click after it is rendered."""
        self._programmatic_selection_click = True
        try:
            focused = self._gm.focus_node_properties(
                node,
                simulate_click=True,
                graph_view=graph_view or self._graph_view,
                click_global_pos=click_global_pos,
                fast_click=True,
            )
            if not focused:
                print("[UEStyleNodeCreator] Created node, but could not select it in Properties.")
        finally:
            self._programmatic_selection_click = False

    def _schedule_properties_focus(self, node, graph_view=None,
                                   click_global_pos=None):
        """Retry Properties selection asynchronously without blocking the UI."""
        self._properties_focus_token += 1
        token = self._properties_focus_token
        # Start almost immediately so Properties feels attached to the create
        # gesture. Two compact retries cover slower graph rendering while the
        # full sequence still completes in roughly 150 ms.
        retry_delays = (16, 18, 24)
        verify_delays = (24, 30, 40)

        def attempt(index):
            if token != self._properties_focus_token:
                return
            # First attempt repeats the user's exact placement click. Later
            # attempts resolve the rendered node's current bounding box, so a
            # viewport move or another transient operation cannot invalidate
            # the original screen coordinate.
            exact_pos = click_global_pos if index == 0 else None
            self._focus_properties_after_create(
                node, graph_view, exact_pos)
            QtCore.QTimer.singleShot(
                verify_delays[index],
                lambda retry_index=index: verify(retry_index))

        def verify(index):
            if token != self._properties_focus_token:
                return
            if self._gm.is_node_selected(node):
                return
            next_index = index + 1
            if next_index >= len(retry_delays):
                print(
                    "[UEStyleNodeCreator] Created node, but Properties focus "
                    "did not persist after asynchronous retries.")
                return
            QtCore.QTimer.singleShot(
                retry_delays[next_index],
                lambda retry_index=next_index: attempt(retry_index))

        QtCore.QTimer.singleShot(retry_delays[0], lambda: attempt(0))

    # ==================================================================
    # Widget Discovery
    # ==================================================================

    def _find_graph_view(self, parent):
        views = parent.findChildren(QtWidgets.QGraphicsView)
        visible = [v for v in views if v.isVisible() and v.scene() is not None]
        named = []
        for v in visible:
            name = (v.objectName() or "").lower()
            cls = (v.metaObject().className() or "").lower() if v.metaObject() else ""
            if any(kw in name or kw in cls for kw in ("graph", "node", "canvas")):
                named.append(v)
        candidates = named or visible or views
        return max(candidates, key=lambda v: v.width() * v.height(), default=None)

    @staticmethod
    def _graphics_view_for_widget(widget):
        current = widget
        while current is not None:
            if isinstance(current, QtWidgets.QGraphicsView):
                return current
            try:
                current = current.parentWidget()
            except BaseException:
                break
        return None

    def _graph_click_context(self, watched, event):
        """Resolve a mouse event to the graph under the global cursor."""
        try:
            global_pos = event.globalPosition().toPoint()
        except AttributeError:
            try:
                global_pos = event.globalPos()
            except BaseException:
                global_pos = QtGui.QCursor.pos()

        direct = (
            watched if isinstance(watched, QtWidgets.QGraphicsView)
            else self._graphics_view_for_widget(watched))
        candidates = []
        for view in (direct, self._last_interacted_graph_view, self._graph_view):
            if view is not None and view not in candidates:
                candidates.append(view)
        if self._main_window is not None:
            discovered = self._find_graph_view(self._main_window)
            if discovered is not None and discovered not in candidates:
                candidates.append(discovered)

        for graph_view in candidates:
            try:
                viewport = graph_view.viewport()
                if viewport is None or not viewport.isVisible():
                    continue
                view_pos = viewport.mapFromGlobal(global_pos)
                if not viewport.rect().contains(view_pos):
                    continue
                scene_pos = graph_view.mapToScene(view_pos)
                self._last_interacted_graph_view = graph_view
                return graph_view, scene_pos, global_pos
            except BaseException:
                continue
        return None

    @staticmethod
    def _looks_like_graph_view(view):
        try:
            text = ((view.objectName() or "") + " " +
                    (view.metaObject().className() or "")).lower()
            if any(part in text for part in ("graph", "node", "canvas")):
                return True
            # Designer's graph is normally the largest visible graphics view.
            return view.isVisible() and view.width() >= 400 and view.height() >= 300
        except BaseException:
            return False

    def get_graph_view_center_position(self):
        """Return the center of the currently visible Graph viewport in scene units."""
        view = self._last_interacted_graph_view
        if view is not None:
            try:
                if not view.isVisible() or view.scene() is None:
                    view = None
            except BaseException:
                view = None
        if view is None and self._main_window is not None:
            current = self._find_graph_view(self._main_window)
            if current is not None:
                self._graph_view = current
                view = current
        if view is None:
            view = self._graph_view
        if view is None or view.scene() is None:
            return None
        try:
            viewport = view.viewport()
            center_point = viewport.rect().center()
            scene_point = view.mapToScene(center_point)
            return scene_point.x(), scene_point.y()
        except BaseException:
            return None

    # ==================================================================
    # Key Decoding
    # ==================================================================

    @staticmethod
    def _match_combo(key, modifiers, combo_str):
        """Check if a key event matches a combo string like 'Ctrl+Shift+D'."""
        if not combo_str:
            return False
        parts = [p.strip() for p in combo_str.upper().split("+")]
        required_mods = 0
        required_key = None
        for p in parts:
            if p == "CTRL":
                required_mods |= int(QtCore.Qt.KeyboardModifier.ControlModifier.value)
            elif p == "SHIFT":
                required_mods |= int(QtCore.Qt.KeyboardModifier.ShiftModifier.value)
            elif p == "ALT":
                required_mods |= int(QtCore.Qt.KeyboardModifier.AltModifier.value)
            elif p == "META":
                required_mods |= int(QtCore.Qt.KeyboardModifier.MetaModifier.value)
            else:
                required_key = p
        if required_key is None:
            return False
        try:
            actual_mods = int(modifiers.value)
        except AttributeError:
            actual_mods = int(modifiers)
        mod_mask = (int(QtCore.Qt.KeyboardModifier.ControlModifier.value) |
                     int(QtCore.Qt.KeyboardModifier.ShiftModifier.value) |
                     int(QtCore.Qt.KeyboardModifier.AltModifier.value) |
                     int(QtCore.Qt.KeyboardModifier.MetaModifier.value))
        if (actual_mods & mod_mask) != required_mods:
            return False
        char = CreateModeController._key_to_char(key, modifiers)
        return char is not None and char == required_key

    @staticmethod
    def _key_to_char(key, modifiers):
        if QtCore.Qt.Key.Key_A <= key <= QtCore.Qt.Key.Key_Z:
            return chr(key)
        if QtCore.Qt.Key.Key_0 <= key <= QtCore.Qt.Key.Key_9:
            return chr(key)
        if QtCore.Qt.Key.Key_F1 <= key <= QtCore.Qt.Key.Key_F35:
            return "F{}".format(key - QtCore.Qt.Key.Key_F1 + 1)
        try:
            text = QtGui.QKeySequence(key).toString()
            if text and len(text) == 1:
                return text.upper()
        except BaseException:
            pass
        return None

    # ==================================================================
    # Focus Detection
    # ==================================================================

    def _is_graph_view_focused(self):
        """Check if cursor or keyboard focus is within the Graph View area."""
        if self._graph_view is None:
            return True
        # Check widget under cursor first (most reliable)
        cursor_pos = QtGui.QCursor.pos()
        widget = QtWidgets.QApplication.widgetAt(cursor_pos)
        if widget is None:
            widget = QtWidgets.QApplication.focusWidget()
        if widget is None:
            return True
        # Walk up to Graph View
        w = widget
        while w is not None:
            if w is self._graph_view:
                return True
            if hasattr(w, "parentWidget"):
                w = w.parentWidget()
            elif hasattr(w, "parent"):
                w = w.parent()
            else:
                break
        return False

    @staticmethod
    def _is_text_input_focused():
        """Check if a non-Graph interactive widget has focus — skip interception."""
        w = QtWidgets.QApplication.focusWidget()
        if w is None:
            return False
        # Text inputs
        if isinstance(w, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
            return True
        # Spin/combos
        if isinstance(w, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            return True
        if isinstance(w, QtWidgets.QComboBox):
            return w.isEditable()
        # Tree/list/table views (Explorer, Library, etc.)
        if isinstance(w, (QtWidgets.QTreeView, QtWidgets.QListView, QtWidgets.QTableView)):
            return True
        # Check if the focus widget has a class name suggesting a panel
        try:
            cls = w.metaObject().className().lower() if w.metaObject() else ""
        except BaseException:
            cls = ""
        panel_keywords = ("explorer", "library", "console", "property", "attributes",
                          "parameter", "dockwidget", "toolbox", "menu", "combobox")
        if any(k in cls for k in panel_keywords):
            return True
        return False

    # ==================================================================
    # Properties
    # ==================================================================

    @property
    def state(self):
        return self._state

    @property
    def pending_node(self):
        return dict(self._pending) if self._pending else None

    # ==================================================================
    # Indicator
    # ==================================================================

    def _show_indicator(self):
        color = self._config.get_setting("indicator_color", "#288CFF") if self._config else "#288CFF"
        size = self._config.get_setting("indicator_size", 108) if self._config else 108
        shape = self._config.get_setting("indicator_shape", "orb") if self._config else "orb"
        if self._indicator is None:
            self._indicator = HoldIndicator(color, size, shape)
        else:
            self._indicator.set_color(color)
            self._indicator.set_size(size)
            self._indicator.set_shape(shape)
        self._indicator.move_to_cursor()
        self._indicator.show()

    def _hide_indicator(self):
        if self._indicator is not None:
            self._indicator.hide()

    def update_indicator_color(self, hex_color):
        if self._indicator is not None:
            self._indicator.set_color(hex_color)
        if self._config is not None:
            self._config.set_setting("indicator_color", hex_color)

    def update_indicator_size(self, size):
        if self._indicator is not None:
            self._indicator.set_size(size)
        if self._config is not None:
            self._config.set_setting("indicator_size", size)

    def update_indicator_shape(self, shape):
        if self._indicator is not None:
            self._indicator.set_shape(shape)
        if self._config is not None:
            self._config.set_setting("indicator_shape", shape)


class HoldIndicator(QtWidgets.QWidget):
    """Luminous orb that follows the cursor during CREATE MODE."""

    def __init__(self, hex_color="#288CFF", size=108, shape="orb"):
        super().__init__()
        self._color = QtGui.QColor(hex_color)
        self._size = size
        self._shape = shape if shape in ("orb", "cross_star") else "orb"
        self._phase = 0.0
        self._pulse_timer = QtCore.QTimer(self)
        self._pulse_timer.setInterval(33)
        self._pulse_timer.timeout.connect(self._advance_animation)
        flags = (
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        if hasattr(QtCore.Qt.WindowType, "WindowTransparentForInput"):
            flags |= QtCore.Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._apply_size()

    def showEvent(self, event):
        self._pulse_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._pulse_timer.stop()
        super().hideEvent(event)

    def _advance_animation(self):
        self._phase = (self._phase + 0.075) % (math.pi * 2.0)
        self.update()

    def set_color(self, hex_color):
        self._color = QtGui.QColor(hex_color)
        self.update()

    def set_size(self, size):
        self._size = size
        self._apply_size()
        self.update()

    def set_shape(self, shape):
        self._shape = shape if shape in ("orb", "cross_star") else "orb"
        self.update()

    def _apply_size(self):
        self.setFixedSize(self._size, self._size)

    def move_to_cursor(self):
        pos = QtGui.QCursor.pos()
        self.move(pos.x() - self._size // 2, pos.y() - self._size // 2)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        cx = self._size / 2
        r = self._size / 2
        c = self._color
        pulse = 0.94 + 0.06 * math.sin(self._phase)
        breath = 0.985 + 0.015 * math.sin(self._phase)

        def color(alpha, white_mix=0.0):
            mix = max(0.0, min(1.0, white_mix))
            red = int(c.red() + (255 - c.red()) * mix)
            green = int(c.green() + (255 - c.green()) * mix)
            blue = int(c.blue() + (255 - c.blue()) * mix)
            return QtGui.QColor(
                red, green, blue,
                max(0, min(255, int(alpha * pulse))))

        try:
            plus_mode = QtGui.QPainter.CompositionMode.CompositionMode_Plus
            source_over = QtGui.QPainter.CompositionMode.CompositionMode_SourceOver
        except AttributeError:
            plus_mode = QtGui.QPainter.CompositionMode_Plus
            source_over = QtGui.QPainter.CompositionMode_SourceOver

        p.setPen(QtCore.Qt.PenStyle.NoPen)

        if self._shape == "cross_star":
            def draw_ray(radius, thickness, stops, angle):
                p.save()
                p.translate(cx, cx)
                p.rotate(angle)
                p.scale(1.0, thickness)
                gradient = QtGui.QRadialGradient(0.0, 0.0, radius)
                for stop, alpha, white_mix in stops:
                    gradient.setColorAt(stop, color(alpha, white_mix))
                p.setBrush(gradient)
                p.drawEllipse(QtCore.QPointF(0.0, 0.0), radius, radius)
                p.restore()

            ray_r = r * 0.98 * breath
            broad_stops = (
                (0.00, 72, 0.20), (0.12, 66, 0.14),
                (0.28, 50, 0.06), (0.48, 31, 0.00),
                (0.66, 16, 0.00), (0.82, 6, 0.00),
                (0.94, 1, 0.00), (1.00, 0, 0.00))
            medium_stops = (
                (0.00, 130, 0.55), (0.12, 116, 0.38),
                (0.30, 86, 0.18), (0.52, 48, 0.04),
                (0.72, 20, 0.00), (0.88, 4, 0.00),
                (1.00, 0, 0.00))
            sharp_stops = (
                (0.00, 205, 0.92), (0.10, 175, 0.72),
                (0.30, 112, 0.34), (0.56, 55, 0.08),
                (0.78, 15, 0.00), (0.94, 2, 0.00),
                (1.00, 0, 0.00))

            # Broad rays soften the cross; narrower additive rays form the
            # fluorescent star spikes without introducing hard boundaries.
            p.setCompositionMode(source_over)
            draw_ray(ray_r, 0.23, broad_stops, 0.0)
            draw_ray(ray_r, 0.23, broad_stops, 90.0)
            p.setCompositionMode(plus_mode)
            draw_ray(ray_r, 0.095, medium_stops, 0.0)
            draw_ray(ray_r, 0.095, medium_stops, 90.0)
            draw_ray(ray_r, 0.032, sharp_stops, 0.0)
            draw_ray(ray_r, 0.032, sharp_stops, 90.0)

            # A compact circular haze keeps the intersection soft.
            haze_r = r * 0.38 * breath
            haze = QtGui.QRadialGradient(cx, cx, haze_r)
            haze.setColorAt(0.0, color(92, 0.32))
            haze.setColorAt(0.35, color(54, 0.12))
            haze.setColorAt(0.72, color(14, 0.00))
            haze.setColorAt(1.0, color(0, 0.00))
            p.setBrush(haze)
            p.drawEllipse(cx - haze_r, cx - haze_r, haze_r * 2, haze_r * 2)
        else:
            # Continuous wide halo with a long, smooth tail.
            p.setCompositionMode(source_over)
            outer_r = r * breath
            outer = QtGui.QRadialGradient(cx, cx, outer_r)
            for stop, alpha in (
                    (0.00, 58), (0.10, 55), (0.22, 47), (0.36, 35),
                    (0.50, 24), (0.64, 14), (0.76, 8), (0.87, 3),
                    (0.94, 1), (1.00, 0)):
                outer.setColorAt(stop, color(alpha))
            p.setBrush(outer)
            p.drawEllipse(cx - outer_r, cx - outer_r, outer_r * 2, outer_r * 2)

            # Additive colored bloom creates a stronger fluorescent response.
            p.setCompositionMode(plus_mode)
            bloom_r = r * 0.68 * breath
            bloom = QtGui.QRadialGradient(cx, cx, bloom_r)
            for stop, alpha, white_mix in (
                    (0.00, 105, 0.32), (0.12, 100, 0.24),
                    (0.28, 82, 0.12), (0.46, 55, 0.04),
                    (0.64, 30, 0.00), (0.80, 12, 0.00),
                    (0.92, 3, 0.00), (1.00, 0, 0.00)):
                bloom.setColorAt(stop, color(alpha, white_mix))
            p.setBrush(bloom)
            p.drawEllipse(cx - bloom_r, cx - bloom_r, bloom_r * 2, bloom_r * 2)

        # Hot inner bloom and white core complete the neon look.
        inner_r = r * 0.31 * breath
        inner = QtGui.QRadialGradient(cx, cx, inner_r)
        for stop, alpha, white_mix in (
                (0.00, 205, 0.92), (0.16, 188, 0.72),
                (0.34, 145, 0.46), (0.56, 88, 0.20),
                (0.76, 36, 0.05), (0.92, 7, 0.00),
                (1.00, 0, 0.00)):
            inner.setColorAt(stop, color(alpha, white_mix))
        p.setBrush(inner)
        p.drawEllipse(cx - inner_r, cx - inner_r, inner_r * 2, inner_r * 2)

        core_r = max(1.5, r * 0.055)
        core = QtGui.QRadialGradient(cx, cx, core_r)
        core.setColorAt(0.0, QtGui.QColor(255, 255, 255, int(245 * pulse)))
        core.setColorAt(0.55, color(190, 0.95))
        core.setColorAt(1.0, color(0, 0.80))
        p.setBrush(core)
        p.drawEllipse(cx - core_r, cx - core_r, core_r * 2, core_r * 2)

        if self._shape == "orb":
            # Subtle deterministic dither breaks up residual 8-bit banding.
            p.setCompositionMode(source_over)
            golden_angle = 2.399963229728653
            dot_count = max(36, min(120, self._size))
            for index in range(dot_count):
                t = (index + 0.5) / dot_count
                dot_r = r * (0.34 + 0.61 * math.sqrt(t))
                angle = index * golden_angle + self._phase * 0.08
                x = cx + math.cos(angle) * dot_r
                y = cx + math.sin(angle) * dot_r
                alpha = 2 + (index % 3)
                p.setBrush(QtGui.QColor(c.red(), c.green(), c.blue(), alpha))
                p.drawEllipse(QtCore.QPointF(x, y), 0.55, 0.55)

        p.end()
