"""
UE Style Node Creator - Settings Dock Widget

Provides a dockable panel for managing shortcut mappings,
scanning available nodes, and configuring plugin behavior.
Uses the SD ui_mgr.newDockWidget() API like NodePreset does.
"""

import os

from .i18n import translate

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets


DOCK_ID = "UEStyleNodeCreator_Dock_01"


class ShortcutTableWidget(QtWidgets.QWidget):
    """The main settings panel widget (goes inside the SD dock)."""

    def __init__(self, config, node_database, shortcut_manager, create_mode,
                 preset_module=None, parent=None, reload_callback=None):
        super().__init__(parent)
        self._config = config
        self._node_database = node_database
        self._shortcut_manager = shortcut_manager
        self._create_mode = create_mode
        self._preset_module = preset_module
        self._reload_callback = reload_callback
        self._language = self._config.get_setting("language", "en")
        self._pending_extra = {}  # row -> {extra data} for unsaved rows
        self._collapsed_preset_groups = set(
            str(group) for group in self._config.get_setting(
                "collapsed_preset_groups", []) if group is not None)
        self._preset_group_header_rows = {}
        self._preset_group_child_rows = {}
        self._track_window_size = False
        self._window_size_timer = QtCore.QTimer(self)
        self._window_size_timer.setSingleShot(True)
        self._window_size_timer.setInterval(350)
        self._window_size_timer.timeout.connect(self._save_window_size)
        self._preset_unload_timer = QtCore.QTimer(self)
        self._preset_unload_timer.setSingleShot(True)
        self._preset_unload_timer.setInterval(max(
            0, int(self._config.get_setting("preset_unload_delay_ms", 1500))))
        self._preset_unload_timer.timeout.connect(
            self._unload_disabled_preset_extension)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding)
        self._build_ui()
        self._refresh_table()
        self._apply_content_mode(initial=True)
        self._apply_language()

        # Hook create-mode state for live status display
        self._create_mode.mode_changed.connect(self._on_mode_changed)
        self._create_mode.selection_shortcut_triggered.connect(self._add_from_selection)

    def _tr(self, text, **values):
        return translate(self._language, text, **values)

    @staticmethod
    def _set_form_label(form, field, text):
        label = form.labelForField(field)
        if label is not None:
            label.setText(text)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._main_scroll = QtWidgets.QScrollArea()
        self._main_scroll.setWidgetResizable(True)
        self._main_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._main_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._main_scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._main_scroll.setMinimumSize(0, 0)

        self._content_widget = QtWidgets.QWidget()
        self._content_widget.setMinimumWidth(0)
        self._content_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred)
        self._main_scroll.setWidget(self._content_widget)
        root_layout.addWidget(self._main_scroll)

        layout = QtWidgets.QVBoxLayout(self._content_widget)
        self._main_layout = layout
        layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # --- Header ---
        self._header_widget = QtWidgets.QWidget()
        self._header_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed)
        header = QtWidgets.QHBoxLayout(self._header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        self._title_label = QtWidgets.QLabel("<b>UE Style Node Creator</b>")
        self._title_label.setMinimumWidth(0)
        self._title_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed)
        header.addWidget(self._title_label, 1)
        header.addStretch()
        self._status_label = QtWidgets.QLabel("● Idle")
        self._status_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed)
        self._status_label.setStyleSheet("color: #888;")
        header.addWidget(self._status_label)
        layout.addWidget(self._header_widget)

        # --- Optional SBS node-group preset extension ---
        self._preset_extension_check = QtWidgets.QCheckBox(
            "Enable SBS Preset Extension")
        self._preset_extension_check.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed)
        self._preset_extension_check.setChecked(self._config.get_setting(
            "preset_module_enabled", False))
        self._preset_extension_check.setToolTip(
            "Enable node-group presets stored in NodePresets.sbs. The SBS package "
            "stays resident while enabled; disabling restores the JSON node "
            "workflow immediately and unloads the SBS package after a short delay.")
        self._preset_extension_check.toggled.connect(
            self._on_preset_extension_toggled)
        layout.addWidget(self._preset_extension_check)

        # --- Shared creation panel for JSON nodes and SBS node groups ---
        self._node_creation_panel = self._build_shared_creation_panel()
        self._node_creation_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed)
        layout.addWidget(self._node_creation_panel)

        # --- Collapsible JSON shortcut library ---
        self._node_library_toggle = QtWidgets.QPushButton()
        self._node_library_toggle.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed)
        self._node_library_toggle.setFlat(True)
        self._node_library_toggle.setStyleSheet(
            "QPushButton { color: #999; text-align: left; font-size: 11px; } "
            "QPushButton:hover { color: #ddd; }")
        self._node_library_toggle.clicked.connect(self._toggle_node_library)
        layout.addWidget(self._node_library_toggle)

        self._node_library_widget = QtWidgets.QWidget()
        self._node_library_widget.setMinimumSize(0, 0)
        self._node_library_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding)
        node_library_layout = QtWidgets.QVBoxLayout(self._node_library_widget)
        node_library_layout.setContentsMargins(0, 0, 0, 0)
        node_library_layout.setSpacing(4)

        search_row = QtWidgets.QHBoxLayout()
        search_row.setSpacing(4)
        self._search_input = QtWidgets.QLineEdit()
        self._search_input.setMinimumWidth(0)
        self._search_input.setPlaceholderText("Search node definitions…")
        self._search_input.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self._search_input, 1)
        node_library_layout.addLayout(search_row)

        self._search_list = QtWidgets.QListWidget()
        self._search_list.setMinimumSize(0, 0)
        self._search_list.setMaximumHeight(100)
        self._search_list.setVisible(False)
        self._search_list.itemDoubleClicked.connect(self._on_search_result_double_click)
        node_library_layout.addWidget(self._search_list)

        # --- Table (columns: Key, Name, Delete; UID hidden) ---
        self._table = QtWidgets.QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Key", "UID", "Name", ""])
        self._table.setColumnHidden(1, True)
        self._table.setMinimumSize(0, 0)
        self._table.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Ignored)
        self._table.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setMinimumSectionSize(0)
        self._table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 40)
        self._table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(3, 32)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._apply_table_alternate_color()
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.cellChanged.connect(self._on_cell_changed)
        node_library_layout.addWidget(self._table, 1)
        expanded = bool(self._config.get_setting(
            "node_library_expanded", False))
        self._node_library_expanded = expanded
        # The JSON area participates in the upper half only while expanded.
        # When collapsed, release it so the SBS toggle moves directly below
        # the JSON toggle; the SBS panel remains the lower-half spacer.
        self._node_library_widget.setVisible(expanded)
        self._set_node_library_content_visible(expanded)
        layout.addWidget(self._node_library_widget, 1)
        self._update_node_library_toggle()

        # --- SBS preset module panel ---
        self._preset_library_toggle = QtWidgets.QPushButton()
        self._preset_library_toggle.setFlat(True)
        self._preset_library_toggle.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed)
        self._preset_library_toggle.setStyleSheet(
            "QPushButton { color: #999; text-align: left; font-size: 11px; } "
            "QPushButton:hover { color: #ddd; }")
        self._preset_library_toggle.clicked.connect(
            self._toggle_preset_library)
        layout.addWidget(self._preset_library_toggle)

        self._preset_panel = self._build_preset_panel()
        self._preset_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding)
        self._preset_library_expanded = bool(self._config.get_setting(
            "preset_library_expanded", True))
        self._preset_count = 0
        self._set_preset_library_content_visible(
            self._preset_library_expanded)
        layout.addWidget(self._preset_panel, 1)
        self._update_preset_library_toggle()

        # --- Settings toggle ---
        self._settings_toggle = QtWidgets.QPushButton("⚙ Settings ▸")
        self._settings_toggle.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed)
        self._settings_toggle.setFlat(True)
        self._settings_toggle.setStyleSheet("QPushButton { color: #888; text-align: left; font-size: 11px; } QPushButton:hover { color: #ccc; }")
        self._settings_toggle.clicked.connect(self._toggle_settings)
        layout.addWidget(self._settings_toggle)

        # --- Settings drawer (collapsible) ---
        self._settings_widget = QtWidgets.QWidget()
        self._settings_widget.setMinimumSize(0, 0)
        settings_layout = QtWidgets.QVBoxLayout(self._settings_widget)
        settings_layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
        settings_layout.setContentsMargins(0, 4, 0, 0)
        settings_layout.setSpacing(4)

        settings_form = QtWidgets.QFormLayout()
        self._settings_form = settings_form
        settings_form.setContentsMargins(0, 0, 0, 0)
        settings_form.setSpacing(4)
        settings_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        settings_form.setRowWrapPolicy(
            QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)

        self._language_combo = QtWidgets.QComboBox()
        self._language_combo.addItem("English", "en")
        self._language_combo.addItem("简体中文", "zh_CN")
        language_index = self._language_combo.findData(self._language)
        self._language_combo.setCurrentIndex(max(0, language_index))
        self._language_combo.currentIndexChanged.connect(
            self._on_language_changed)
        settings_form.addRow("Language:", self._language_combo)

        self._color_btn = QtWidgets.QPushButton()
        self._color_btn.setFixedSize(24, 24)
        self._color_btn.setToolTip("Indicator glow color")
        self._color_btn.clicked.connect(self._pick_color)
        self._color_btn.setStyleSheet("background: {}; border: 1px solid #555; border-radius: 4px;".format(
            self._config.get_setting("indicator_color", "#288CFF")))
        settings_form.addRow("Glow:", self._color_btn)

        self._row_color_btn = QtWidgets.QPushButton()
        self._row_color_btn.setFixedSize(24, 24)
        self._row_color_btn.setToolTip("Custom alternate shortcut-table row color")
        self._row_color_btn.clicked.connect(self._pick_table_alternate_color)
        self._update_row_color_button()
        settings_form.addRow("Row Color:", self._row_color_btn)

        self._shape_combo = QtWidgets.QComboBox()
        self._shape_combo.setMinimumWidth(0)
        self._shape_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._shape_combo.setMinimumContentsLength(4)
        self._shape_combo.addItem("Orb", "orb")
        self._shape_combo.addItem("Cross Star", "cross_star")
        current_shape = self._config.get_setting("indicator_shape", "orb")
        current_shape_index = self._shape_combo.findData(current_shape)
        self._shape_combo.setCurrentIndex(max(0, current_shape_index))
        self._shape_combo.setToolTip("Glow indicator shape")
        self._shape_combo.currentIndexChanged.connect(self._on_shape_changed)
        settings_form.addRow("Shape:", self._shape_combo)

        self._size_spin = QtWidgets.QSpinBox()
        self._size_spin.setMinimumWidth(0)
        self._size_spin.setRange(40, 300)
        self._size_spin.setValue(self._config.get_setting("indicator_size", 108))
        self._size_spin.setSuffix(" px")
        self._size_spin.setToolTip("Indicator glow diameter")
        self._size_spin.valueChanged.connect(self._on_size_changed)
        settings_form.addRow("Size:", self._size_spin)

        self._delay_spin = QtWidgets.QSpinBox()
        self._delay_spin.setMinimumWidth(0)
        self._delay_spin.setRange(100, 2000)
        self._delay_spin.setValue(self._config.get_setting("hold_delay_ms", 400))
        self._delay_spin.setSuffix(" ms")
        self._delay_spin.setToolTip("Long-press duration to activate")
        self._delay_spin.valueChanged.connect(self._on_delay_changed)
        settings_form.addRow("Delay:", self._delay_spin)

        self._sel_key_btn = QtWidgets.QPushButton()
        self._sel_key_btn.setMinimumWidth(0)
        self._sel_key_btn.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed)
        current_sel = self._config.get_setting("selection_shortcut", "")
        self._sel_key_btn.setText(current_sel if current_sel else "(click to set)")
        self._sel_key_btn.setToolTip("Click then press desired key combo (e.g., Ctrl+Shift+D)")
        self._sel_key_btn.clicked.connect(self._capture_sel_key)
        settings_form.addRow("Sel.Key:", self._sel_key_btn)
        settings_layout.addLayout(settings_form)

        self._open_properties_check = QtWidgets.QCheckBox("Open Properties")
        self._open_properties_check.setMinimumWidth(0)
        self._open_properties_check.setChecked(
            self._config.get_setting("open_properties_after_create", False))
        self._open_properties_check.setToolTip(
            "Select the newly created node and show its parameters in the Properties panel.")
        self._open_properties_check.toggled.connect(self._on_open_properties_toggled)
        settings_layout.addWidget(self._open_properties_check)

        # Install app-wide filter for capture mode
        self._sel_key_capturing = False

        self._settings_scroll = QtWidgets.QScrollArea()
        self._settings_scroll.setWidgetResizable(True)
        self._settings_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._settings_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._settings_scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._settings_scroll.setMinimumSize(0, 0)
        self._settings_scroll.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding)
        self._settings_scroll.setWidget(self._settings_widget)
        self._settings_scroll.setVisible(False)
        layout.addWidget(self._settings_scroll)

        # Absorb unused height only when no content panel is available. This
        # keeps all fixed controls packed at the top instead of distributing
        # empty space between them.
        self._bottom_spacer = QtWidgets.QWidget()
        self._bottom_spacer.setMinimumSize(0, 0)
        self._bottom_spacer.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding)
        layout.addWidget(self._bottom_spacer)

        self._node_mode_widgets = (
            self._node_creation_panel, self._node_library_toggle,
        )

    def _build_shared_creation_panel(self):
        """Build one creation form shared by JSON nodes and SBS node groups."""
        panel = QtWidgets.QWidget()
        panel.setMinimumSize(0, 0)
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(5)

        self._shared_storage_label = QtWidgets.QLabel()
        self._shared_storage_label.setWordWrap(True)
        self._shared_storage_label.setStyleSheet(
            "color: #888; font-size: 10px;")
        panel_layout.addWidget(self._shared_storage_label)

        form = QtWidgets.QFormLayout()
        self._shared_form = form
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
        self._node_name_edit = QtWidgets.QLineEdit()
        self._node_name_edit.setPlaceholderText(
            "Optional for one node; required for node group")
        self._node_group_edit = QtWidgets.QLineEdit()
        self._node_group_edit.setPlaceholderText("Optional group")
        self._node_key_edit = QtWidgets.QLineEdit()
        self._node_key_edit.setPlaceholderText(
            "Quick-create key (required for one node)")
        self._node_key_edit.setMaxLength(3)
        # The SBS and JSON save paths intentionally consume the same fields.
        self._preset_name_edit = self._node_name_edit
        self._preset_group_edit = self._node_group_edit
        self._preset_key_edit = self._node_key_edit
        form.addRow("Name:", self._node_name_edit)
        form.addRow("Group:", self._node_group_edit)
        form.addRow("Key:", self._node_key_edit)
        panel_layout.addLayout(form)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(5)
        self._btn_save_shared = QtWidgets.QPushButton("Save Selection")
        self._btn_save_shared.setToolTip(
            "One selected node saves to JSON; two or more selected nodes save "
            "as an SBS preset when the extension is enabled.")
        self._btn_save_shared.clicked.connect(self._save_shared_selection)
        self._btn_refresh_shared = QtWidgets.QPushButton("Refresh")
        self._btn_refresh_shared.setToolTip(
            "Reload both JSON shortcuts and SBS presets.")
        self._btn_refresh_shared.clicked.connect(self._refresh_shared_data)
        action_row.addWidget(self._btn_save_shared, 1)
        action_row.addWidget(self._btn_refresh_shared)
        panel_layout.addLayout(action_row)
        self._update_shared_storage_label()
        return panel

    def _build_preset_panel(self):
        panel = QtWidgets.QWidget()
        panel.setMinimumSize(0, 0)
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(5)

        storage = ""
        if (self._preset_module and
                self._config.get_setting("preset_module_enabled", False)):
            storage = self._preset_module.package_path()
        self._preset_storage_label = QtWidgets.QLabel(
            "SBS: {}".format(storage or "NodePreset module unavailable"))
        self._preset_storage_label.setWordWrap(True)
        self._preset_storage_label.setStyleSheet("color: #888; font-size: 10px;")
        self._preset_storage_label.setVisible(False)
        panel_layout.addWidget(self._preset_storage_label)

        filter_row = QtWidgets.QHBoxLayout()
        self._preset_group_label = QtWidgets.QLabel("Group:")
        filter_row.addWidget(self._preset_group_label)
        self._preset_group_combo = QtWidgets.QComboBox()
        self._preset_group_combo.addItem("(All Groups)", "__all__")
        self._preset_group_combo.addItem("(Ungrouped)", "__ungrouped__")
        self._preset_group_combo.currentIndexChanged.connect(
            self._on_preset_group_changed)
        filter_row.addWidget(self._preset_group_combo, 1)
        panel_layout.addLayout(filter_row)

        self._preset_table = QtWidgets.QTableWidget()
        self._preset_table.setColumnCount(6)
        self._preset_table.setHorizontalHeaderLabels(
            ["Key", "Preset ID", "Preset", "Group", "Load", ""])
        self._preset_table.setColumnHidden(1, True)
        self._preset_table.setColumnHidden(3, True)
        self._preset_table.setMinimumSize(0, 0)
        self._preset_table.setAlternatingRowColors(True)
        custom_row_color = self._config.get_setting("table_alternate_color", "")
        row_color = QtGui.QColor(custom_row_color)
        if custom_row_color and row_color.isValid():
            palette = self._preset_table.palette()
            palette.setColor(self._palette_alternate_role(), row_color)
            self._preset_table.setPalette(palette)
        self._preset_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._preset_table.verticalHeader().setVisible(False)
        header = self._preset_table.horizontalHeader()
        header.setMinimumSectionSize(0)
        header.setStretchLastSection(False)
        self._preset_table.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self._preset_table.setColumnWidth(0, 40)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self._preset_table.setColumnWidth(4, 34)
        self._preset_table.setColumnWidth(5, 34)
        self._preset_table.cellChanged.connect(self._on_preset_cell_changed)
        self._preset_table.cellClicked.connect(self._on_preset_table_clicked)
        panel_layout.addWidget(self._preset_table, 1)
        return panel

    def minimumSizeHint(self):
        """Allow Designer's dock splitter to collapse this panel freely."""
        return QtCore.QSize(0, 0)

    def resizeEvent(self, event):
        """Use compact labels when the dock becomes narrow."""
        width = event.size().width()
        compact = width < 240
        very_compact = width < 170
        # Vertical compression is handled by the outer scroll area. Keep the
        # content intact regardless of dock height.
        self._title_label.setVisible(True)
        self._status_label.setVisible(not compact)
        if self._shortcut_manager.preset_mode_enabled():
            self._title_label.setText(
                "<b>{}</b>".format(self._tr(
                    "Node + Group" if compact else "Node + Group Creator")))
        else:
            self._title_label.setText(
                "<b>{}</b>".format(self._tr(
                    "UE Node Creator" if compact else "UE Style Node Creator")))
        self._search_input.setPlaceholderText(
            self._tr("Search…" if compact else "Search node definitions…"))
        self._btn_save_shared.setText(
            self._tr("Save" if very_compact else "Save Selection"))
        self._preset_table.setColumnHidden(3, True)
        self._preset_table.setColumnHidden(4, compact)
        super().resizeEvent(event)
        if self._track_window_size and event.size().width() > 80 and event.size().height() > 80:
            self._window_size_timer.start()

    def eventFilter(self, watched, event):
        if (watched is getattr(self, "_size_host", None) and
                event.type() == QtCore.QEvent.Type.Resize and
                self._track_window_size):
            size = event.size()
            if size.width() > 80 and size.height() > 80:
                self._window_size_timer.start()
        return super().eventFilter(watched, event)

    def restore_saved_size(self, host=None):
        """Restore the last useful content/dock size after the dock is built."""
        if host is not None:
            self._size_host = host
        width = int(self._config.get_setting("window_width", 0) or 0)
        height = int(self._config.get_setting("window_height", 0) or 0)
        if width <= 80 or height <= 80:
            return
        self.resize(width, height)
        if host is not None:
            try:
                host.resize(width, height)
            except BaseException:
                pass

    def enable_window_size_tracking(self):
        self._track_window_size = True
        host = getattr(self, "_size_host", None)
        if host is not None:
            try:
                host.installEventFilter(self)
            except BaseException:
                pass

    def _save_window_size(self):
        if not self._track_window_size:
            return
        host = getattr(self, "_size_host", None)
        size = host.size() if host is not None else self.size()
        if size.width() <= 80 or size.height() <= 80:
            return
        self._config.set_setting("window_width", int(size.width()))
        self._config.set_setting("window_height", int(size.height()))

    def closeEvent(self, event):
        if self._track_window_size:
            self._save_window_size()
        host = getattr(self, "_size_host", None)
        if host is not None:
            try:
                host.removeEventFilter(self)
            except BaseException:
                pass
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Table Operations
    # ------------------------------------------------------------------

    def _refresh_table(self):
        self._table.cellChanged.disconnect(self._on_cell_changed)
        # Always display the ordinary JSON table, even while SBS mode is active.
        shortcuts = self._config.get_shortcuts()
        self._table.setRowCount(len(shortcuts))
        ordered = sorted(
            shortcuts.items(),
            key=lambda item: (
                self._config.shortcut_logical_key(item[0], item[1]),
                self._config.shortcut_mouse_button(item[0], item[1], "left")))
        for row, (storage_key, data) in enumerate(ordered):
            key = self._config.shortcut_logical_key(storage_key, data)
            uid = data.get("node_uid", "")
            name = data.get("node_name", "")
            group = data.get("group", "")
            mouse_button = self._config.shortcut_mouse_button(
                storage_key, data, "left")
            tip = "[{}]\nNode: {}\nUID: {}\nCreate: hold key + {}-click".format(
                key, name, uid, mouse_button)
            if group:
                tip += "\nGroup: {}".format(group)

            key_item = QtWidgets.QTableWidgetItem(key)
            key_item.setData(QtCore.Qt.ItemDataRole.UserRole, mouse_button)
            key_item.setToolTip(tip)
            self._table.setItem(row, 0, key_item)

            self._table.setItem(row, 1, QtWidgets.QTableWidgetItem(uid))

            name_item = QtWidgets.QTableWidgetItem(name)
            name_item.setToolTip(tip)
            self._table.setItem(row, 2, name_item)

            self._set_delete_button(row, "Delete [{}] — {}".format(key, name))
        self._table.cellChanged.connect(self._on_cell_changed)
        self._update_node_library_toggle()

    def _update_node_library_toggle(self):
        if not hasattr(self, "_node_library_toggle"):
            return
        expanded = bool(getattr(self, "_node_library_expanded", False))
        arrow = "▾" if expanded else "▸"
        count = len(self._config.get_shortcuts())
        self._node_library_toggle.setText(
            "{} {} ({})".format(
                arrow, self._tr("JSON Shortcuts"), count))

    def _toggle_node_library(self, checked=False):
        expanded = not bool(getattr(self, "_node_library_expanded", False))
        self._node_library_expanded = expanded
        self._set_node_library_content_visible(expanded)
        self._config.set_setting("node_library_expanded", expanded)
        self._update_node_library_toggle()
        self._refresh_adaptive_layout()

    def _set_node_library_content_visible(self, visible):
        """Show/release the JSON half while preserving stable fixed controls."""
        if not hasattr(self, "_node_library_widget"):
            return
        self._node_library_widget.setVisible(bool(visible))
        self._search_input.setVisible(bool(visible))
        self._table.setVisible(bool(visible))
        if visible:
            self._on_search_changed(self._search_input.text())
        else:
            self._search_list.setVisible(False)

    def _update_preset_library_toggle(self):
        if not hasattr(self, "_preset_library_toggle"):
            return
        expanded = bool(getattr(self, "_preset_library_expanded", True))
        arrow = "▾" if expanded else "▸"
        count = int(getattr(self, "_preset_count", 0))
        self._preset_library_toggle.setText(
            "{} {} ({})".format(
                arrow, self._tr("SBS Presets"), count))

    def _toggle_preset_library(self, checked=False):
        expanded = not bool(getattr(
            self, "_preset_library_expanded", True))
        self._preset_library_expanded = expanded
        self._set_preset_library_content_visible(expanded)
        self._config.set_setting("preset_library_expanded", expanded)
        self._update_preset_library_toggle()
        self._refresh_adaptive_layout()

    def _set_preset_library_content_visible(self, visible):
        """Collapse SBS controls while preserving their layout allocation."""
        if not hasattr(self, "_preset_panel"):
            return
        self._preset_panel.setVisible(True)
        self._preset_group_label.setVisible(bool(visible))
        self._preset_group_combo.setVisible(bool(visible))
        self._preset_table.setVisible(bool(visible))
        if not visible:
            self._preset_storage_label.setVisible(False)

    def _refresh_adaptive_layout(self):
        """Recompute panel stretch and enable scrolling before controls squash."""
        layout = getattr(self, "_main_layout", None)
        if layout is None:
            return
        # Preserve usable row/control heights. QScrollArea then introduces a
        # vertical scrollbar whenever the dock viewport is shorter than the
        # currently expanded content instead of compressing every section.
        minimum_height = 245
        if bool(getattr(self, "_node_library_expanded", False)):
            minimum_height += 155
        if self._shortcut_manager.preset_mode_enabled():
            minimum_height += 28
            if bool(getattr(self, "_preset_library_expanded", True)):
                minimum_height += 170
        if (hasattr(self, "_settings_scroll") and
                self._settings_scroll.isVisible()):
            minimum_height += 210
        content = getattr(self, "_content_widget", None)
        if content is not None:
            content.setMinimumHeight(minimum_height)

        json_expanded = bool(getattr(
            self, "_node_library_expanded", False))
        sbs_enabled = self._shortcut_manager.preset_mode_enabled()
        settings_expanded = bool(
            hasattr(self, "_settings_scroll") and
            self._settings_scroll.isVisible())

        # Explicit stretch prevents Qt from spreading spare height across the
        # header, checkbox, creation form and fold buttons.
        if hasattr(self, "_node_library_widget"):
            layout.setStretchFactor(
                self._node_library_widget, 1 if json_expanded else 0)
        if hasattr(self, "_preset_panel"):
            # Keep the SBS lower-half allocation while the extension is on;
            # when its list is folded this becomes the intended blank area.
            layout.setStretchFactor(
                self._preset_panel, 1 if sbs_enabled else 0)
        if hasattr(self, "_settings_scroll"):
            layout.setStretchFactor(
                self._settings_scroll, 1 if settings_expanded else 0)
        if hasattr(self, "_bottom_spacer"):
            use_bottom_spacer = not (
                json_expanded or sbs_enabled or settings_expanded)
            self._bottom_spacer.setVisible(use_bottom_spacer)
            layout.setStretchFactor(
                self._bottom_spacer, 1 if use_bottom_spacer else 0)
        layout.invalidate()
        layout.activate()
        if content is not None:
            content.updateGeometry()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()

    def _update_shared_storage_label(self):
        enabled = self._config.get_setting("preset_module_enabled", False)
        if enabled:
            text = self._tr("Shared save: 1 node → JSON  |  2+ nodes → SBS")
        else:
            text = self._tr(
                "Shared save: 1 node → JSON  |  Enable SBS for node groups")
        self._shared_storage_label.setText(text)
        sbs_path = self._preset_module.package_path() if self._preset_module else ""
        self._shared_storage_label.setToolTip(
            "Mouse button is configurable per shortcut (defaults: JSON left, "
            "SBS right).\nJSON: {}\nSBS: {}".format(
                self._config.file_path, sbs_path))

    def _refresh_shared_data(self, checked=False):
        # Refresh now replaces the old separate Scan Nodes and Reload buttons.
        self._reload_plugin()

    def _save_shared_selection(self, checked=False):
        """Route the shared form by selection count without duplicating controls."""
        try:
            selected = self._create_mode._gm.get_selected_nodes()
        except BaseException:
            selected = []

        if not selected:
            QtWidgets.QMessageBox.information(
                self, self._tr("No Selection"), self._tr(
                    "Select one node for a JSON shortcut, or multiple nodes for an SBS group preset."))
            return
        if len(selected) == 1:
            print("[UEStyleNodeCreator] Selection detected: 1 node -> JSON shortcut")
            self._add_from_selection(use_form=True)
            return
        if not self._shortcut_manager.preset_mode_enabled():
            QtWidgets.QMessageBox.information(
                self, self._tr("SBS Extension Disabled"), self._tr(
                    "Enable the SBS Preset Extension to save multiple selected nodes."))
            return
        print("[UEStyleNodeCreator] Selection detected: {} nodes -> SBS preset".format(
            len(selected)))
        self._save_preset_from_selection()

    def _refresh_preset_table(self):
        if self._preset_module is None:
            return
        try:
            presets = self._preset_module.list_presets()
        except BaseException as exc:
            self._preset_count = 0
            self._update_preset_library_toggle()
            self._preset_storage_label.setText("SBS error: {}".format(exc))
            self._preset_storage_label.setVisible(True)
            return
        self._preset_count = len(presets)
        self._update_preset_library_toggle()
        self._preset_storage_label.setToolTip(
            "SBS resident: {}".format(self._preset_module.package_path()))
        self._preset_storage_label.setVisible(False)

        current_group_text = self._preset_group_combo.currentText()
        self._preset_group_combo.blockSignals(True)
        self._preset_group_combo.clear()
        self._preset_group_combo.addItem(
            self._tr("(All Groups)"), "__all__")
        self._preset_group_combo.addItem(
            self._tr("(Ungrouped)"), "__ungrouped__")
        for group in sorted(set(
                p.get("group", "") for p in presets if p.get("group", ""))):
            self._preset_group_combo.addItem(group, group)
        restored = self._preset_group_combo.findText(current_group_text)
        self._preset_group_combo.setCurrentIndex(restored if restored >= 0 else 0)
        self._preset_group_combo.blockSignals(False)
        selected_group = self._preset_group_combo.currentData()
        if selected_group == "__ungrouped__":
            presets = [p for p in presets if not p.get("group", "")]
        elif selected_group not in (None, "__all__"):
            presets = [p for p in presets if p.get("group", "") == selected_group]

        mappings = self._config.get_preset_shortcuts()
        key_by_id = {}
        mouse_by_id = {}
        for storage_key, entry in mappings.items():
            preset_id = entry.get("preset_id", "")
            if preset_id and preset_id not in key_by_id:
                key_by_id[preset_id] = self._config.shortcut_logical_key(
                    storage_key, entry)
                mouse_by_id[preset_id] = self._config.shortcut_mouse_button(
                    storage_key, entry, "right")

        grouped = {}
        for preset in presets:
            group = str(preset.get("group", "") or "")
            grouped.setdefault(group, []).append(preset)
        ordered_groups = sorted(
            grouped, key=lambda group: (group == "", group.lower()))

        self._preset_table.cellChanged.disconnect(self._on_preset_cell_changed)
        self._preset_table.clearSpans()
        self._preset_group_header_rows = {}
        self._preset_group_child_rows = {}
        total_rows = len(presets) + len(ordered_groups)
        self._preset_table.setRowCount(total_rows)
        row = 0
        for group in ordered_groups:
            group_presets = grouped[group]
            collapsed = group in self._collapsed_preset_groups
            display_group = group or self._tr("(Ungrouped)")
            header_item = QtWidgets.QTableWidgetItem(
                "{} {}  ({})".format(
                    "▸" if collapsed else "▾",
                    display_group, len(group_presets)))
            header_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsEnabled |
                QtCore.Qt.ItemFlag.ItemIsSelectable)
            header_font = header_item.font()
            header_font.setBold(True)
            header_item.setFont(header_font)
            header_item.setForeground(QtGui.QColor("#d8d8e8"))
            header_item.setBackground(QtGui.QColor("#25252d"))
            header_item.setToolTip(
                self._tr("Click to expand or collapse this group"))
            self._preset_table.setItem(row, 0, header_item)
            self._preset_table.setSpan(row, 0, 1, 6)
            self._preset_table.setRowHeight(row, 24)
            self._preset_group_header_rows[row] = group
            header_row = row
            row += 1

            child_rows = []
            for preset in group_presets:
                child_row = row
                child_rows.append(child_row)
                preset_id = preset.get("preset_id", "")
                key_item = QtWidgets.QTableWidgetItem(
                    key_by_id.get(preset_id, ""))
                key_item.setToolTip(
                    "Hold this key and {}-click in the graph.".format(
                        mouse_by_id.get(preset_id, "right")))
                self._preset_table.setItem(child_row, 0, key_item)

                id_item = QtWidgets.QTableWidgetItem(preset_id)
                id_item.setFlags(
                    id_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self._preset_table.setItem(child_row, 1, id_item)

                name = preset.get("preset_name", preset_id)
                name_item = QtWidgets.QTableWidgetItem(
                    "{}  ({} nodes)".format(
                        name, preset.get("node_count", 0)))
                name_item.setData(QtCore.Qt.ItemDataRole.UserRole, name)
                name_item.setFlags(
                    name_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self._preset_table.setItem(child_row, 2, name_item)

                group_item = QtWidgets.QTableWidgetItem(group)
                group_item.setFlags(
                    group_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self._preset_table.setItem(child_row, 3, group_item)
                self._set_preset_load_button(
                    child_row, preset_id, name)
                self._set_preset_delete_button(
                    child_row, preset_id, name)
                self._preset_table.setRowHidden(child_row, collapsed)
                row += 1
            self._preset_group_child_rows[header_row] = child_rows
        self._preset_table.cellChanged.connect(self._on_preset_cell_changed)

    def _on_preset_group_changed(self, index):
        self._refresh_preset_table()

    def _on_preset_table_clicked(self, row, column):
        if row not in self._preset_group_header_rows:
            return
        group = self._preset_group_header_rows[row]
        collapsed = group not in self._collapsed_preset_groups
        if collapsed:
            self._collapsed_preset_groups.add(group)
        else:
            self._collapsed_preset_groups.discard(group)
        for child_row in self._preset_group_child_rows.get(row, []):
            self._preset_table.setRowHidden(child_row, collapsed)
        item = self._preset_table.item(row, 0)
        if item is not None:
            display_group = group or self._tr("(Ungrouped)")
            item.setText("{} {}  ({})".format(
                "▸" if collapsed else "▾", display_group,
                len(self._preset_group_child_rows.get(row, []))))
        self._config.set_setting(
            "collapsed_preset_groups",
            sorted(self._collapsed_preset_groups))

    def _set_preset_load_button(self, row, preset_id, preset_name):
        button = QtWidgets.QToolButton()
        button.setText("►")
        button.setToolTip(self._tr(
            "Load this preset at the current Graph View center"))
        button.setFixedSize(30, 24)
        button.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed)
        button.setAutoRaise(True)
        button.setStyleSheet(
            "QToolButton { border: none; color: #5fca78; font-weight: bold; "
            "font-size: 14px; padding: 0px; } "
            "QToolButton:hover { color: #8bea9d; background: rgba(80,220,100,28); }")
        button.clicked.connect(
            lambda checked=False, pid=preset_id, name=preset_name:
                self._load_preset(pid, name))
        self._preset_table.setCellWidget(row, 4, button)

    def _set_preset_delete_button(self, row, preset_id, preset_name):
        button = QtWidgets.QToolButton()
        button.setText("×")
        button.setToolTip(self._tr(
            "Delete SBS preset '{name}'", name=preset_name))
        button.setAutoRaise(True)
        button.setFixedSize(28, 24)
        button.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed)
        button.setStyleSheet(
            "QToolButton { border: none; color: #e06666; font-weight: bold; "
            "font-size: 16px; padding: 0px; } "
            "QToolButton:hover { color: #ff7777; background: rgba(255,80,80,28); }")
        button.clicked.connect(
            lambda checked=False, pid=preset_id, name=preset_name:
                self._delete_preset(pid, name))
        self._preset_table.setCellWidget(row, 5, button)

    def _load_preset(self, preset_id, preset_name):
        try:
            position = self._create_mode.get_graph_view_center_position()
            if position is None:
                raise RuntimeError("Could not locate the current Graph View center.")
            created = self._preset_module.create_at(preset_id, position)
        except BaseException as exc:
            QtWidgets.QMessageBox.warning(
                self, self._tr("Load Preset Failed"), str(exc))
            return
        QtWidgets.QMessageBox.information(
            self, self._tr("Preset Loaded"), self._tr(
                "Loaded '{name}' with {count} nodes at the Graph View center.",
                name=preset_name, count=len(created)))

    def _on_preset_cell_changed(self, row, column):
        if column != 0:
            return
        key_item = self._preset_table.item(row, 0)
        id_item = self._preset_table.item(row, 1)
        name_item = self._preset_table.item(row, 2)
        group_item = self._preset_table.item(row, 3)
        key = key_item.text().strip().upper() if key_item else ""
        preset_id = id_item.text().strip() if id_item else ""
        preset_name = name_item.data(
            QtCore.Qt.ItemDataRole.UserRole) if name_item else preset_id
        group = group_item.text().strip() if group_item else ""
        if not preset_id:
            return

        mouse_button = "right"
        for entry in self._config.get_preset_shortcuts().values():
            if entry.get("preset_id") == preset_id:
                mouse_button = str(
                    entry.get("mouse_button", "right")).lower()
                break

        # Update only the edited row. This preserves shortcuts belonging to
        # presets hidden by the current group filter.
        for old_key, entry in list(self._config.get_preset_shortcuts().items()):
            logical_key = self._config.shortcut_logical_key(old_key, entry)
            old_button = self._config.shortcut_mouse_button(
                old_key, entry, "right")
            if (entry.get("preset_id") == preset_id or
                    (key and logical_key == key and
                     old_button == mouse_button)):
                self._config.remove_preset_shortcut(old_key)
        if key and self._is_valid_preset_key(key):
            self._config.set_preset_shortcut(
                key, preset_id, preset_name or preset_id, group=group,
                mouse_button=mouse_button)
        if self._shortcut_manager.preset_mode_enabled():
            self._shortcut_manager.reload()

    def _save_preset_from_selection(self, checked=False, prompt_if_empty=False,
                                    mouse_button="right"):
        if self._preset_module is None or not self._preset_module.is_available():
            QtWidgets.QMessageBox.warning(
                self, self._tr("NodePreset Unavailable"),
                self._preset_module.error if self._preset_module else
                self._tr("NodePreset module is not configured."))
            return
        name = self._preset_name_edit.text().strip()
        group = self._preset_group_edit.text().strip()
        shortcut_key = self._preset_key_edit.text().strip().upper()
        if not name and prompt_if_empty:
            details = self._prompt_preset_details()
            if details is None:
                return
            name, group, shortcut_key = details
            accepted = True
            if not accepted:
                return
        if not name:
            QtWidgets.QMessageBox.information(
                self, self._tr("Preset Name"),
                self._tr("Enter a preset name first."))
            self._preset_name_edit.setFocus()
            return
        if shortcut_key and not self._is_valid_preset_key(shortcut_key):
            QtWidgets.QMessageBox.warning(
                self, self._tr("Invalid Shortcut"), self._tr(
                    "Use one letter, number, symbol, or an F-key such as F1."))
            self._preset_key_edit.setFocus()
            return
        try:
            result = self._preset_module.save_selection(name, group, overwrite=False)
            if result.get("exists"):
                answer = QtWidgets.QMessageBox.question(
                    self, self._tr("Overwrite Preset"), self._tr(
                        "Preset '{name}' already exists. Overwrite it?",
                        name=name),
                    QtWidgets.QMessageBox.StandardButton.Yes |
                    QtWidgets.QMessageBox.StandardButton.No)
                if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                    return
                result = self._preset_module.save_selection(name, group, overwrite=True)
        except BaseException as exc:
            QtWidgets.QMessageBox.warning(
                self, self._tr("Save Preset Failed"), str(exc))
            return
        assigned_key = ""
        if shortcut_key:
            assigned_key = self._assign_preset_shortcut(
                shortcut_key,
                result.get("preset_id", ""),
                result.get("preset_name", name),
                result.get("group", group),
                mouse_button=mouse_button,
            )
        self._preset_name_edit.clear()
        self._preset_key_edit.clear()
        self._refresh_preset_table()
        shortcut_text = "\nShortcut: {}".format(assigned_key) if assigned_key else ""
        QtWidgets.QMessageBox.information(
            self, self._tr("Preset Saved"),
            "Saved '{}' to SBS with {} nodes.{}".format(
                result.get("preset_name", name), result.get("node_count", 0),
                shortcut_text or "\nNo shortcut assigned; you can enter one in the table."))

    def _prompt_preset_details(self):
        """Collect SBS name, UI group and shortcut in one dialog."""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(self._tr("Save Node Group Preset"))
        dialog.setMinimumWidth(280)
        layout = QtWidgets.QVBoxLayout(dialog)
        form = QtWidgets.QFormLayout()
        name_edit = QtWidgets.QLineEdit(self._preset_name_edit.text().strip())
        group_edit = QtWidgets.QLineEdit(self._preset_group_edit.text().strip())
        key_edit = QtWidgets.QLineEdit(self._preset_key_edit.text().strip())
        key_edit.setMaxLength(3)
        name_edit.setPlaceholderText(self._tr("Required"))
        group_edit.setPlaceholderText(self._tr("Optional"))
        key_edit.setPlaceholderText(self._tr("Optional, e.g. 1 or F2"))
        form.addRow(self._tr("Preset Name:"), name_edit)
        form.addRow(self._tr("Group:"), group_edit)
        form.addRow(self._tr("Shortcut:"), key_edit)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Save).setText(
                self._tr("Save"))
        buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(
                self._tr("Cancel"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        name_edit.setFocus()
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        return (
            name_edit.text().strip(),
            group_edit.text().strip(),
            key_edit.text().strip().upper(),
        )

    @staticmethod
    def _is_valid_preset_key(key):
        key = str(key or "").strip().upper()
        if len(key) == 1:
            return True
        if key.startswith("F") and key[1:].isdigit():
            return 1 <= int(key[1:]) <= 35
        return False

    def _assign_preset_shortcut(self, key, preset_id, preset_name, group="",
                                mouse_button="right"):
        """Synchronize the newly saved SBS preset with its JSON key reference."""
        key = str(key or "").strip().upper()
        if not key or not preset_id:
            return ""
        gesture_conflict = self._shortcut_manager.get_gesture_conflict(
            key, mouse_button, "preset")
        if gesture_conflict is not None:
            answer = QtWidgets.QMessageBox.question(
                self, "Replace Mouse Gesture",
                "Shortcut '{} + {} click' is currently assigned to JSON node "
                "'{}'.\n\nReplace it with this SBS preset?".format(
                    key, mouse_button,
                    gesture_conflict.get("node_name", "JSON node")),
                QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No)
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return ""
            self._shortcut_manager.remove(
                key, entry_type="node", mouse_button=mouse_button)
        mappings = self._config.get_preset_shortcuts()
        conflict = self._shortcut_manager.get_entry(
            key, mouse_button, entry_type="preset")
        if conflict and conflict.get("preset_id") != preset_id:
            answer = QtWidgets.QMessageBox.question(
                self, "Replace Shortcut",
                "Shortcut '{}' is assigned to '{}'. Replace it?".format(
                    key, conflict.get("preset_name", conflict.get("preset_id", ""))),
                QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.No)
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return ""

        # Keep one visible shortcut per preset when a new key is selected.
        for old_key, entry in list(mappings.items()):
            if entry.get("preset_id") == preset_id:
                self._config.remove_preset_shortcut(old_key)
        self._config.set_preset_shortcut(
            key, preset_id, preset_name, group=group,
            mouse_button=mouse_button)
        if self._shortcut_manager.preset_mode_enabled():
            self._shortcut_manager.reload()
        self._refresh_table()
        return key

    def _delete_preset(self, preset_id, preset_name):
        answer = QtWidgets.QMessageBox.question(
            self, "Delete SBS Preset",
            "Delete '{}' from NodePresets.sbs?".format(preset_name),
            QtWidgets.QMessageBox.StandardButton.Yes |
            QtWidgets.QMessageBox.StandardButton.No)
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if not self._preset_module.delete_preset(preset_id):
            QtWidgets.QMessageBox.warning(
                self, "Delete Failed",
                self._preset_module.error or "Could not delete the SBS preset.")
            return
        for key, entry in list(self._config.get_preset_shortcuts().items()):
            if entry.get("preset_id") == preset_id:
                self._config.remove_preset_shortcut(key)
        self._shortcut_manager.reload()
        self._refresh_preset_table()

    def _set_delete_button(self, row, tooltip):
        """Put a visible, responsive delete button directly in column 3."""
        button = QtWidgets.QToolButton()
        button.setText("×")
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setMinimumSize(0, 0)
        button.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding)
        button.setStyleSheet(
            "QToolButton { border: none; color: #e06666; font-weight: bold; "
            "font-size: 16px; padding: 0px; } "
            "QToolButton:hover { color: #ff7777; background: rgba(255,80,80,28); }")
        button.clicked.connect(
            lambda checked=False, delete_button=button:
                self._delete_button_clicked(delete_button))
        self._table.setCellWidget(row, 3, button)

    def _delete_button_clicked(self, button):
        """Resolve the button's current row so deletions never use stale rows."""
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, 3) is button:
                self._delete_row(row)
                return

    def _delete_row(self, row):
        self._table.removeRow(row)
        self._auto_save()

    def _delete_selected_row(self):
        rows = set(idx.row() for idx in self._table.selectedIndexes())
        for row in sorted(rows, reverse=True):
            self._table.removeRow(row)
        self._auto_save()

    # ------------------------------------------------------------------
    # Add from Graph View Selection
    # ------------------------------------------------------------------

    def _add_from_selection(self, checked=False, use_form=False):
        """Read the currently selected node in the Graph View and add a shortcut."""
        try:
            selected = self._create_mode._gm.get_selected_nodes()
        except BaseException:
            selected = []

        if not selected:
            QtWidgets.QMessageBox.information(
                self, self._tr("No Selection"),
                self._tr("Please select a node in the Graph View first, then click 'From Selection'.")
            )
            return

        # Use the first selected node
        node = selected[0]
        uid = ""
        label = ""

        # Try to get definition ID and label
        try:
            if hasattr(node, "getDefinitionId"):
                uid = node.getDefinitionId() or ""
        except BaseException:
            pass

        if not uid:
            try:
                definition = node.getDefinition()
                if definition:
                    uid = definition.getId() or ""
                    label = definition.getLabel() or ""
            except BaseException:
                pass

        if not uid:
            QtWidgets.QMessageBox.warning(
                self, self._tr("Cannot Identify Node"), self._tr(
                    "Could not read the selected node's definition ID.\n\nTry selecting a different node.")
            )
            return

        if not label or label == uid:
            parts = uid.split("::")
            raw = parts[-1] if parts else uid
            label = raw.replace("const_", "").replace("_", " ").title()

        # Detect instance nodes — store URL-based reference
        extra_data = {}
        if "instance" in uid.lower():
            try:
                ref = node.getReferencedResource()
                if ref is not None:
                    # Try pkg:// URL first (most reliable across sessions)
                    if hasattr(ref, "getUrl"):
                        try:
                            extra_data["instance_url"] = ref.getUrl() or ""
                        except BaseException:
                            pass
                    # Fallbacks
                    if not extra_data.get("instance_url"):
                        try:
                            extra_data["instance_url"] = "pkg:///{}/{}".format(
                                ref.getFilePath() or "", ref.getIdentifier() or "")
                        except BaseException:
                            pass
                    try:
                        extra_data["instance_graph"] = ref.getIdentifier() or ""
                    except BaseException:
                        pass
                    try:
                        extra_data["instance_pkg"] = ref.getFilePath() or ""
                    except BaseException:
                        pass
                    # Also try to get the package's own URL/identifier
                    try:
                        owner = ref.getPackage() if hasattr(ref, "getPackage") else None
                        if owner is not None and hasattr(owner, "getUrl"):
                            extra_data["instance_pkg_url"] = owner.getUrl() or ""
                    except BaseException:
                        pass
                    try:
                        label = ref.getIdentifier() or label
                    except BaseException:
                        pass
            except BaseException:
                pass

        if use_form:
            custom_name = self._node_name_edit.text().strip()
            group = self._node_group_edit.text().strip()
            if custom_name:
                label = custom_name
            if group:
                extra_data["group"] = group
            extra_data["mouse_button"] = "left"

            key = self._node_key_edit.text().strip().upper()
            if not key:
                QtWidgets.QMessageBox.information(
                    self, self._tr("Shortcut Key"),
                    self._tr("Enter a quick-create key first."))
                self._node_key_edit.setFocus()
                return
            if not self._is_valid_preset_key(key):
                QtWidgets.QMessageBox.warning(
                    self, self._tr("Invalid Shortcut"), self._tr(
                        "Use one letter, number, symbol, or an F-key such as F1."))
                self._node_key_edit.setFocus()
                return

            blocked = {"F", "Z", "H", "V", "F9"}
            if key in blocked:
                QtWidgets.QMessageBox.warning(
                    self, self._tr("Reserved Key"), self._tr(
                        "'{key}' is reserved by Substance Designer and cannot be used.\n\nPlease choose a different key.",
                        key=key))
                self._node_key_edit.setFocus()
                return

            gesture_conflict = self._shortcut_manager.get_gesture_conflict(
                key, "left", "node")
            if gesture_conflict is not None:
                reply = QtWidgets.QMessageBox.question(
                    self, "Replace Mouse Gesture",
                    "Shortcut '{} + left click' is assigned to SBS preset "
                    "'{}'.\n\nReplace it with this JSON node?".format(
                        key, gesture_conflict.get(
                            "preset_name", gesture_conflict.get(
                                "node_name", "SBS preset"))),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No)
                if reply != QtWidgets.QMessageBox.Yes:
                    return
                self._shortcut_manager.remove(
                    key, entry_type="preset", mouse_button="left")

            if self._shortcut_manager.has_conflict(
                    key, entry_type="node", mouse_button="left"):
                existing = self._shortcut_manager.get_entry(
                    key, "left", entry_type="node") or {}
                reply = QtWidgets.QMessageBox.question(
                    self, "Shortcut Conflict",
                    "'{}' is already assigned to '{}'.\n\nOverwrite?".format(
                        key, existing.get("node_name", "JSON node")),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No)
                if reply != QtWidgets.QMessageBox.Yes:
                    return
                self._shortcut_manager.remove(
                    key, entry_type="node", mouse_button="left")
        else:
            # Selection-key workflow with visible detection and manual routing.
            detected_target = "preset" if len(selected) > 1 else "node"
            values = {
                "target": detected_target,
                "mouse_button": (
                    "right" if detected_target == "preset" else "left"),
                "json_key": "",
                "preset_name": self._preset_name_edit.text().strip(),
                "preset_group": self._preset_group_edit.text().strip(),
                "preset_key": self._preset_key_edit.text().strip(),
            }
            while True:
                result = self._prompt_shortcut_with_detection(
                    label, uid, len(selected), detected_target,
                    defaults=values)
                if result is None:
                    return
                values = result
                target = values["target"]
                key = (values["preset_key"] if target == "preset"
                       else values["json_key"])

                key = key.strip().upper()
                if not key:
                    QtWidgets.QMessageBox.information(
                        self, self._tr("Shortcut Key"), self._tr(
                            "Enter a shortcut key for the selected creation type."))
                    continue
                if not self._is_valid_preset_key(key):
                    QtWidgets.QMessageBox.warning(
                        self, self._tr("Invalid Shortcut"), self._tr(
                            "Use one letter, number, symbol, or an F-key such as F1."))
                    continue

                blocked = {"F", "Z", "H", "V", "F9"}
                if key in blocked:
                    QtWidgets.QMessageBox.warning(
                        self, self._tr("Reserved Key"), self._tr(
                            "'{key}' is reserved by Substance Designer and cannot be used.\n\nPlease choose a different key.",
                            key=key))
                    continue  # back to input dialog

                mouse_button = values.get(
                    "mouse_button",
                    "right" if target == "preset" else "left")
                gesture_conflict = self._shortcut_manager.get_gesture_conflict(
                    key, mouse_button, target)
                if gesture_conflict is not None and target == "node":
                    reply = QtWidgets.QMessageBox.question(
                        self, "Replace Mouse Gesture",
                        "Shortcut '{} + {} click' is assigned to SBS preset "
                        "'{}'.\n\nReplace it with this JSON node?".format(
                            key, mouse_button,
                            gesture_conflict.get(
                                "preset_name",
                                gesture_conflict.get("node_name", "SBS preset"))),
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                        QtWidgets.QMessageBox.No)
                    if reply != QtWidgets.QMessageBox.Yes:
                        continue
                    self._shortcut_manager.remove(
                        key, entry_type="preset", mouse_button=mouse_button)

                if target == "preset":
                    if not self._shortcut_manager.preset_mode_enabled():
                        QtWidgets.QMessageBox.warning(
                            self, self._tr("SBS Extension Disabled"),
                            "The detected/manual target is SBS Preset. Enable "
                            "the SBS Preset Extension or choose JSON Single Node.")
                        continue
                    preset_name = values["preset_name"].strip()
                    if not preset_name:
                        QtWidgets.QMessageBox.information(
                            self, self._tr("Preset Name"),
                            self._tr("Enter a preset name first."))
                        continue
                    self._preset_name_edit.setText(preset_name)
                    self._preset_group_edit.setText(
                        values["preset_group"].strip())
                    self._preset_key_edit.setText(key)
                    print(
                        "[UEStyleNodeCreator] Set Shortcut routing: {} nodes -> "
                        "SBS preset ({})".format(
                            len(selected),
                            "detected" if target == detected_target else "manual"))
                    self._save_preset_from_selection(
                        prompt_if_empty=False,
                        mouse_button=mouse_button)
                    return

                if self._shortcut_manager.has_conflict(
                        key, entry_type="node", mouse_button=mouse_button):
                    existing = self._shortcut_manager.get_entry(
                        key, mouse_button, entry_type="node") or {}
                    reply = QtWidgets.QMessageBox.question(
                        self, "Shortcut Conflict",
                        "'{} + {} click' is already assigned to '{}'.\n\n"
                        "Overwrite?".format(
                            key, mouse_button,
                            existing.get("node_name", "JSON node")),
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                        QtWidgets.QMessageBox.No
                    )
                    if reply != QtWidgets.QMessageBox.Yes:
                        continue  # back to input dialog
                    self._shortcut_manager.remove(
                        key, entry_type="node", mouse_button=mouse_button)

                break  # valid key, proceed

            extra_data["mouse_button"] = mouse_button
            print(
                "[UEStyleNodeCreator] Set Shortcut routing: {} nodes -> JSON "
                "single node ({})".format(
                    len(selected),
                    "detected" if target == detected_target else "manual"))

        # Register (saves to disk with extra_data included)
        self._shortcut_manager.register(key, uid, label, **extra_data)
        self._create_mode._sm.reload()
        self._refresh_table()
        if self._shortcut_manager.preset_mode_enabled():
            self._refresh_preset_table()
        if use_form:
            self._node_name_edit.clear()
            self._node_key_edit.clear()
        print("[UEStyleNodeCreator] Shortcut set: [{}] → {} ({})".format(key, label, uid))

    def _prompt_shortcut_with_detection(
            self, node_label, node_uid, selected_count, detected_target,
            defaults=None):
        """Show detected node count and allow per-save routing override."""
        defaults = dict(defaults or {})
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(self._tr("Set Shortcut"))
        dialog.setMinimumWidth(390)
        layout = QtWidgets.QVBoxLayout(dialog)

        detected_name = self._tr(
            "SBS Preset" if detected_target == "preset" else
            "JSON Single Node")
        detected_label = QtWidgets.QLabel(
            self._tr(
                "Detected: <b>{count}</b> valid graph node{suffix} → <b>{target}</b>",
                count=selected_count,
                suffix="s" if selected_count != 1 else "",
                target=detected_name))
        detected_label.setWordWrap(True)
        layout.addWidget(detected_label)

        node_info = QtWidgets.QLabel(
            self._tr(
                "First node: {name}\nUID: {uid}",
                name=node_label, uid=node_uid))
        node_info.setWordWrap(True)
        node_info.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(node_info)

        form = QtWidgets.QFormLayout()
        target_combo = QtWidgets.QComboBox()
        target_combo.addItem(self._tr("JSON Single Node"), "node")
        preset_text = self._tr(
            "SBS Preset" if self._shortcut_manager.preset_mode_enabled()
            else "SBS Preset (extension disabled)")
        target_combo.addItem(preset_text, "preset")
        target_index = target_combo.findData(
            defaults.get("target", detected_target))
        target_combo.setCurrentIndex(max(0, target_index))
        form.addRow(self._tr("Creation:"), target_combo)

        mouse_combo = QtWidgets.QComboBox()
        mouse_combo.addItem(self._tr("Left Click"), "left")
        mouse_combo.addItem(self._tr("Right Click"), "right")
        default_mouse = defaults.get(
            "mouse_button",
            "right" if detected_target == "preset" else "left")
        mouse_index = mouse_combo.findData(default_mouse)
        mouse_combo.setCurrentIndex(max(0, mouse_index))
        form.addRow(self._tr("Mouse Button:"), mouse_combo)
        layout.addLayout(form)

        json_widget = QtWidgets.QWidget()
        json_form = QtWidgets.QFormLayout(json_widget)
        json_form.setContentsMargins(0, 0, 0, 0)
        json_key_edit = QtWidgets.QLineEdit(defaults.get("json_key", ""))
        json_key_edit.setMaxLength(3)
        json_key_edit.setPlaceholderText(self._tr("Required, e.g. 1, A or F2"))
        json_form.addRow(self._tr("JSON Key:"), json_key_edit)
        layout.addWidget(json_widget)

        preset_widget = QtWidgets.QWidget()
        preset_form = QtWidgets.QFormLayout(preset_widget)
        preset_form.setContentsMargins(0, 0, 0, 0)
        preset_name_edit = QtWidgets.QLineEdit(
            defaults.get("preset_name", ""))
        preset_group_edit = QtWidgets.QLineEdit(
            defaults.get("preset_group", ""))
        preset_key_edit = QtWidgets.QLineEdit(
            defaults.get("preset_key", ""))
        preset_name_edit.setPlaceholderText(self._tr("Required"))
        preset_group_edit.setPlaceholderText(self._tr("Optional"))
        preset_key_edit.setPlaceholderText(self._tr("Required, e.g. 1, A or F2"))
        preset_key_edit.setMaxLength(3)
        preset_form.addRow(self._tr("Preset Name:"), preset_name_edit)
        preset_form.addRow(self._tr("Group:"), preset_group_edit)
        preset_form.addRow(self._tr("SBS Key:"), preset_key_edit)
        layout.addWidget(preset_widget)

        previous_target = [target_combo.currentData()]

        def update_fields(index=0):
            is_preset = target_combo.currentData() == "preset"
            current_target = target_combo.currentData()
            if current_target != previous_target[0]:
                default_button = "right" if is_preset else "left"
                button_index = mouse_combo.findData(default_button)
                mouse_combo.setCurrentIndex(max(0, button_index))
                previous_target[0] = current_target
            json_widget.setVisible(not is_preset)
            preset_widget.setVisible(is_preset)
            if is_preset:
                preset_name_edit.setFocus()
            else:
                json_key_edit.setFocus()
            dialog.adjustSize()

        target_combo.currentIndexChanged.connect(update_fields)
        update_fields()

        note = QtWidgets.QLabel(self._tr(
            "Shortcut usage:\n"
            "• Mouse button is saved independently for each shortcut\n"
            "• Defaults: JSON = left-click, SBS = right-click\n\n"
            "Reserved by Substance Designer:\n"
            "• F / Z / H / V — viewport navigation\n"
            "• F9 — engine switching"))
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Save).setText(
                self._tr("Save"))
        buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(
                self._tr("Cancel"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        return {
            "target": target_combo.currentData() or detected_target,
            "mouse_button": mouse_combo.currentData() or (
                "right" if detected_target == "preset" else "left"),
            "json_key": json_key_edit.text().strip().upper(),
            "preset_name": preset_name_edit.text().strip(),
            "preset_group": preset_group_edit.text().strip(),
            "preset_key": preset_key_edit.text().strip().upper(),
        }

    def _on_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        menu = QtWidgets.QMenu(self)
        if row >= 0:
            menu.addAction("Duplicate").triggered.connect(lambda: self._duplicate_row(row))
            menu.addAction("Delete").triggered.connect(lambda: self._delete_row(row))
        menu.addSeparator()
        menu.addAction("Clear All").triggered.connect(lambda: [self._table.setRowCount(0), self._auto_save()])
        menu.addAction("Reload from Config").triggered.connect(self._refresh_table)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _duplicate_row(self, row):
        uid = self._table.item(row, 1).text() if self._table.item(row, 1) else ""
        name = self._table.item(row, 2).text() if self._table.item(row, 2) else ""
        # Preserve extra data in pending store (written when user fills key)
        shortcuts = self._shortcut_manager.get_all()
        original_key = self._table.item(row, 0).text() if self._table.item(row, 0) else ""
        original_button = (
            self._table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)
            if self._table.item(row, 0) else "left") or "left"
        extra = {}
        gesture_key = "{}|{}".format(original_key.upper(), original_button)
        if original_key and gesture_key in shortcuts:
            src = shortcuts[gesture_key]
            extra = {k: v for k, v in src.items() if k not in ("node_uid", "node_name")}
        new_row = self._table.rowCount()
        self._table.insertRow(new_row)
        new_key_item = QtWidgets.QTableWidgetItem("")
        new_key_item.setData(
            QtCore.Qt.ItemDataRole.UserRole, original_button)
        self._table.setItem(new_row, 0, new_key_item)
        self._table.setItem(new_row, 1, QtWidgets.QTableWidgetItem(uid))
        self._table.setItem(new_row, 2, QtWidgets.QTableWidgetItem(name))
        self._set_delete_button(new_row, "Delete this shortcut")
        if extra:
            self._pending_extra[new_row] = extra
        self._table.editItem(self._table.item(new_row, 0))

    # ------------------------------------------------------------------
    # Node Scanning
    # ------------------------------------------------------------------

    def _scan_nodes(self):
        nodes = self._node_database.scan_current_graph()
        print("[UEStyleNodeCreator] Scanned {} node definitions.".format(len(nodes)))

    def _on_search_changed(self, text):
        text = text.strip()
        if len(text) < 2:
            self._search_list.setVisible(False)
            return

        # Auto-scan if database is empty
        if not self._node_database.get_all():
            count = len(self._node_database.scan_current_graph())

        results = self._node_database.search(text)
        self._search_list.clear()
        if not results:
            self._search_list.addItem(self._tr(
                "(no results — try Scan Nodes)"))
        for node in results[:50]:
            label = "{}  —  {}".format(node["label"], node["uid"])
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, node)
            self._search_list.addItem(item)
        self._search_list.setVisible(True)

    def _on_search_result_double_click(self, item):
        node = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if node is None:
            return

        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QtWidgets.QTableWidgetItem(""))
        self._table.setItem(row, 1, QtWidgets.QTableWidgetItem(node["uid"]))
        self._table.setItem(row, 2, QtWidgets.QTableWidgetItem(node["label"]))
        self._set_delete_button(row, "Delete this shortcut")
        # Preserve instance data — store for when user edits the key
        extra = {}
        for k in ("instance_url", "instance_graph", "instance_pkg"):
            if node.get(k):
                extra[k] = node[k]
        if extra:
            self._pending_extra[row] = extra
        self._table.editItem(self._table.item(row, 0))

        self._search_input.clear()
        self._search_list.setVisible(False)
        self._auto_save()
        self._table.editItem(self._table.item(row, 0))

        self._search_input.clear()
        self._search_list.setVisible(False)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _on_language_changed(self, index):
        language = self._language_combo.itemData(index) or "en"
        if language == self._language:
            return
        self._language = language
        self._config.set_setting("language", language)
        self._apply_language()

    def _apply_language(self):
        """Refresh all persistent controls without rebuilding the dock."""
        self._preset_extension_check.setText(
            self._tr("Enable SBS Preset Extension"))
        self._table.setHorizontalHeaderLabels([
            self._tr("Key"), self._tr("UID"), self._tr("Name"), ""])
        self._preset_table.setHorizontalHeaderLabels([
            self._tr("Key"), self._tr("Preset ID"), self._tr("Preset"),
            self._tr("Group"), self._tr("Load"), ""])

        self._set_form_label(
            self._settings_form, self._language_combo,
            self._tr("Language:"))
        for widget, label in (
                (self._color_btn, "Glow:"),
                (self._row_color_btn, "Row Color:"),
                (self._shape_combo, "Shape:"),
                (self._size_spin, "Size:"),
                (self._delay_spin, "Delay:"),
                (self._sel_key_btn, "Sel.Key:")):
            self._set_form_label(
                self._settings_form, widget, self._tr(label))
        for widget, label in (
                (self._node_name_edit, "Name:"),
                (self._node_group_edit, "Group:"),
                (self._node_key_edit, "Key:")):
            self._set_form_label(self._shared_form, widget, self._tr(label))

        for index, (text, data) in enumerate((
                ("Orb", "orb"), ("Cross Star", "cross_star"))):
            combo_index = self._shape_combo.findData(data)
            if combo_index >= 0:
                self._shape_combo.setItemText(combo_index, self._tr(text))

        self._open_properties_check.setText(self._tr("Open Properties"))
        self._node_name_edit.setPlaceholderText(self._tr(
            "Optional for one node; required for node group"))
        self._node_group_edit.setPlaceholderText(self._tr("Optional group"))
        self._node_key_edit.setPlaceholderText(self._tr(
            "Quick-create key (required for one node)"))
        self._search_input.setPlaceholderText(self._tr(
            "Search…" if self.width() < 240 else
            "Search node definitions…"))
        self._btn_save_shared.setText(self._tr(
            "Save" if self.width() < 170 else "Save Selection"))
        self._btn_refresh_shared.setText(self._tr("Refresh"))
        self._preset_group_label.setText(self._tr("Group:"))
        self._settings_toggle.setText(
            "⚙ {} {}".format(
                self._tr("Settings"),
                "▾" if self._settings_scroll.isVisible() else "▸"))

        self._color_btn.setToolTip(self._tr("Indicator glow color"))
        self._row_color_btn.setToolTip(self._tr(
            "Custom alternate shortcut-table row color"))
        self._shape_combo.setToolTip(self._tr("Glow indicator shape"))
        self._size_spin.setToolTip(self._tr("Indicator glow diameter"))
        self._delay_spin.setToolTip(self._tr(
            "Long-press duration to activate"))
        self._sel_key_btn.setToolTip(self._tr(
            "Click then press desired key combo (e.g., Ctrl+Shift+D)"))
        self._open_properties_check.setToolTip(self._tr(
            "Select the newly created node and show its parameters in the Properties panel."))
        self._btn_save_shared.setToolTip(self._tr(
            "One selected node saves to JSON; two or more selected nodes save as an SBS preset when the extension is enabled."))
        self._btn_refresh_shared.setToolTip(self._tr(
            "Reload both JSON shortcuts and SBS presets."))

        current_key = self._preset_group_combo.currentData()
        self._preset_group_combo.blockSignals(True)
        if self._preset_group_combo.count() >= 2:
            self._preset_group_combo.setItemText(
                0, self._tr("(All Groups)"))
            self._preset_group_combo.setItemText(
                1, self._tr("(Ungrouped)"))
        restore = self._preset_group_combo.findData(current_key)
        if restore >= 0:
            self._preset_group_combo.setCurrentIndex(restore)
        self._preset_group_combo.blockSignals(False)

        self._update_node_library_toggle()
        self._update_preset_library_toggle()
        self._update_shared_storage_label()
        self._apply_content_mode()
        self._on_mode_changed(self._create_mode._state, "")
        QtCore.QTimer.singleShot(0, self._update_dock_language)

    def _update_dock_language(self):
        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, QtWidgets.QDockWidget):
                widget.setWindowTitle(self._tr("UE Style Node Creator"))
                return
            widget = widget.parentWidget()

    def _toggle_settings(self):
        visible = not self._settings_scroll.isVisible()
        self._settings_scroll.setVisible(visible)
        self._settings_toggle.setText(
            "⚙ {} {}".format(self._tr("Settings"), "▾" if visible else "▸"))
        self._refresh_adaptive_layout()

    def _pick_color(self):
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self._config.get_setting("indicator_color", "#288CFF")),
            self, "Indicator Glow Color"
        )
        if color.isValid():
            hex_color = color.name()
            self._config.set_setting("indicator_color", hex_color)
            self._color_btn.setStyleSheet(
                "background: {}; border: 1px solid #555; border-radius: 4px;".format(hex_color))
            self._create_mode.update_indicator_color(hex_color)

    @staticmethod
    def _palette_alternate_role():
        try:
            return QtGui.QPalette.ColorRole.AlternateBase
        except AttributeError:
            return QtGui.QPalette.AlternateBase

    def _apply_table_alternate_color(self):
        """Apply a custom alternate row color, if one has been saved."""
        custom = self._config.get_setting("table_alternate_color", "")
        color = QtGui.QColor(custom)
        if not custom or not color.isValid():
            return
        for table in (self._table, getattr(self, "_preset_table", None)):
            if table is None:
                continue
            palette = table.palette()
            palette.setColor(self._palette_alternate_role(), color)
            table.setPalette(palette)

    def _update_row_color_button(self):
        custom = self._config.get_setting("table_alternate_color", "")
        custom_color = QtGui.QColor(custom)
        if custom and custom_color.isValid():
            color_name = custom_color.name()
        else:
            color_name = self._table.palette().color(
                self._palette_alternate_role()).name()
        self._row_color_btn.setStyleSheet(
            "background: {}; border: 1px solid #555; border-radius: 4px;".format(color_name))

    def _pick_table_alternate_color(self):
        custom = self._config.get_setting("table_alternate_color", "")
        custom_color = QtGui.QColor(custom)
        current = custom_color if custom and custom_color.isValid() else self._table.palette().color(
            self._palette_alternate_role())
        color = QtWidgets.QColorDialog.getColor(
            current, self, "Shortcut Table Alternate Row Color")
        if not color.isValid():
            return
        hex_color = color.name()
        self._config.set_setting("table_alternate_color", hex_color)
        for table in (self._table, self._preset_table):
            palette = table.palette()
            palette.setColor(self._palette_alternate_role(), color)
            table.setPalette(palette)
        self._update_row_color_button()

    def _on_cell_changed(self, row, col):
        """Auto-save when user edits a cell directly."""
        self._auto_save()

    def _auto_save(self, extra_data=None, skip_row=None):
        """Silently persist current table to config, preserving extra fields."""
        old_shortcuts = self._config.get_shortcuts()
        old_by_gesture = {}
        for storage_key, entry in old_shortcuts.items():
            old_key = self._config.shortcut_logical_key(storage_key, entry)
            old_button = self._config.shortcut_mouse_button(
                storage_key, entry, "left")
            old_by_gesture[(old_key, old_button)] = entry
        self._config.clear_shortcuts()
        for row in range(self._table.rowCount()):
            key_item = self._table.item(row, 0)
            uid_item = self._table.item(row, 1)
            name_item = self._table.item(row, 2)
            key = key_item.text().strip().upper() if key_item else ""
            uid = uid_item.text().strip() if uid_item else ""
            name = name_item.text().strip() if name_item else uid
            mouse_button = (
                key_item.data(QtCore.Qt.ItemDataRole.UserRole)
                if key_item else "left") or "left"
            if key and uid:
                # Preserve extra fields from old shortcut data
                extra = {}
                if row == skip_row and extra_data:
                    extra = extra_data
                elif row in self._pending_extra:
                    extra = self._pending_extra.pop(row)
                elif (key, mouse_button) in old_by_gesture:
                    old = old_by_gesture[(key, mouse_button)]
                    extra = {k: v for k, v in old.items()
                             if k not in (
                                 "node_uid", "node_name", "shortcut_key")}
                extra["mouse_button"] = mouse_button
                self._config.set_shortcut(key, uid, name, **extra)
        self._shortcut_manager.reload()

    def _on_delay_changed(self, value):
        self._config.set_setting("hold_delay_ms", value)
        self._create_mode._hold_timer.setInterval(value)

    def _capture_sel_key(self):
        """Start capturing the next key combo press."""
        if self._sel_key_capturing:
            return
        self._sel_key_capturing = True
        self._sel_key_btn.setText(self._tr("... press combo ..."))
        self._sel_key_btn.setStyleSheet("QPushButton { background: #448; color: #fff; font-weight: bold; }")
        self._capture_filter = _KeyCaptureFilter(self)
        QtWidgets.QApplication.instance().installEventFilter(self._capture_filter)

    def _on_sel_key_captured(self, combo_str):
        self._sel_key_capturing = False
        if self._capture_filter:
            QtWidgets.QApplication.instance().removeEventFilter(self._capture_filter)
            self._capture_filter = None
        if combo_str:
            self._config.set_setting("selection_shortcut", combo_str)
            self._sel_key_btn.setText(combo_str)
        else:
            self._sel_key_btn.setText(self._config.get_setting("selection_shortcut", ""))
        self._sel_key_btn.setStyleSheet("")

    def _reload_plugin(self):
        print("[UEStyleNodeCreator] Reloading event filters…")
        self._create_mode.shutdown()
        self._node_database.scan_current_graph()
        self._create_mode.initialize()
        self._shortcut_manager.reload()
        self._refresh_table()
        if self._shortcut_manager.preset_mode_enabled():
            if (self._preset_module is None or
                    not self._preset_module.ensure_loaded(refresh_cache=True)):
                error = self._preset_module.error if self._preset_module else "Not configured"
                self._preset_storage_label.setText(
                    "SBS load failed: {}".format(error))
                self._preset_storage_label.setVisible(True)
            self._refresh_preset_table()
        if self._reload_callback:
            self._reload_callback()
        print("[UEStyleNodeCreator] Reload complete.")

    def _on_size_changed(self, value):
        self._config.set_setting("indicator_size", value)
        self._create_mode.update_indicator_size(value)

    def _on_shape_changed(self, index):
        shape = self._shape_combo.itemData(index) or "orb"
        self._create_mode.update_indicator_shape(shape)

    def _on_open_properties_toggled(self, checked):
        self._config.set_setting("open_properties_after_create", bool(checked))

    def _on_preset_extension_toggled(self, enabled):
        self._create_mode.cancel()
        if enabled:
            # Re-enabling during the grace period keeps the package resident.
            self._preset_unload_timer.stop()
        self._shortcut_manager.set_preset_mode_enabled(enabled)

        load_ok = True
        if enabled:
            load_ok = bool(
                self._preset_module and
                self._preset_module.ensure_loaded(refresh_cache=True))
        self._apply_content_mode()
        if self._reload_callback:
            self._reload_callback()

        if enabled:
            detail = (
                "NodePresets.sbs is now kept resident while this extension is "
                "enabled.\n\nIf you close it manually, the next preset refresh, "
                "save, or load will find and reopen it automatically.")
            if not load_ok:
                error = self._preset_module.error if self._preset_module else "Not configured"
                detail += "\n\nThe initial load failed: {}\nPreset operations will retry.".format(
                    error)
            QtWidgets.QMessageBox.information(
                self, "SBS Preset Extension", detail)
        else:
            self._preset_storage_label.setText(
                "SBS extension disabled; package unload is pending.")
            self._preset_unload_timer.start()

    def _unload_disabled_preset_extension(self):
        """Unload only after the extension has stayed disabled for the grace period."""
        if self._shortcut_manager.preset_mode_enabled():
            return
        unloaded = True
        if self._preset_module is not None:
            unloaded = self._preset_module.unload_package()
        if unloaded:
            self._preset_storage_label.setText(
                "SBS extension disabled; NodePresets.sbs unloaded.")
        else:
            error = self._preset_module.error if self._preset_module else "Unknown error"
            self._preset_storage_label.setText(
                "SBS delayed unload failed: {}".format(error))

    def _apply_content_mode(self, initial=False):
        enabled = self._shortcut_manager.preset_mode_enabled()
        self._update_shared_storage_label()
        for widget in self._node_mode_widgets:
            widget.setVisible(True)
        self._preset_library_toggle.setVisible(enabled)
        self._preset_panel.setVisible(enabled)
        self._title_label.setText(
            "<b>{}</b>".format(self._tr(
                "Node + Group Creator" if enabled else
                "UE Style Node Creator")))
        if enabled:
            if self._preset_module is None or not self._preset_module.is_available():
                error = self._preset_module.error if self._preset_module else "Not configured"
                self._preset_storage_label.setText("SBS module unavailable: {}".format(error))
                self._preset_storage_label.setVisible(True)
            else:
                self._preset_storage_label.setToolTip(
                    "SBS resident while extension is enabled: {}".format(
                        self._preset_module.package_path()))
                self._preset_storage_label.setVisible(False)
                self._refresh_preset_table()
            self._set_preset_library_content_visible(
                self._preset_library_expanded)
        self._refresh_adaptive_layout()

    # ------------------------------------------------------------------

    def _on_mode_changed(self, state, description):
        if state == self._create_mode.PLACING:
            self._status_label.setText(
                "● {}: {}".format(self._tr("CREATE"), description))
            self._status_label.setStyleSheet("color: #4af; font-weight: bold;")
        else:
            self._status_label.setText("● " + self._tr("Idle"))
            self._status_label.setStyleSheet("color: #888;")


class _KeyCaptureFilter(QtCore.QObject):
    """Temporary event filter to capture a single key combo press."""

    def __init__(self, owner):
        super().__init__()
        self._owner = owner
        self._mods = None

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Type.KeyPress:
            return self._handle(event)
        if event.type() == QtCore.QEvent.Type.ShortcutOverride:
            return self._handle(event)
        return False

    def _handle(self, event):
        key = event.key()
        if key in (QtCore.Qt.Key.Key_Shift, QtCore.Qt.Key.Key_Control,
                    QtCore.Qt.Key.Key_Alt, QtCore.Qt.Key.Key_Meta):
            return False
        mods = event.modifiers()
        try:
            mod_val = int(mods.value)
        except AttributeError:
            mod_val = int(mods)
        parts = []
        if mod_val & int(QtCore.Qt.KeyboardModifier.ControlModifier.value):
            parts.append("Ctrl")
        if mod_val & int(QtCore.Qt.KeyboardModifier.ShiftModifier.value):
            parts.append("Shift")
        if mod_val & int(QtCore.Qt.KeyboardModifier.AltModifier.value):
            parts.append("Alt")
        if mod_val & int(QtCore.Qt.KeyboardModifier.MetaModifier.value):
            parts.append("Meta")
        try:
            char = QtGui.QKeySequence(key).toString()
        except BaseException:
            char = ""
        if char:
            parts.append(char.upper())
        combo = "+".join(parts) if parts else ""
        self._owner._on_sel_key_captured(combo)
        if hasattr(event, "accept"):
            event.accept()
        return True
