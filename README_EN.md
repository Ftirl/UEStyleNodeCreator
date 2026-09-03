# UE Style Node Creator

English | [简体中文](README.md)

UE Style Node Creator is an Adobe Substance 3D Designer plugin that brings a UE Material Editor-style workflow to the Graph View. Hold a keyboard shortcut, then use the configured left or right mouse button to place either one node or a complete node-group preset.

The plugin runs two storage backends side by side:

- **JSON single-node shortcuts** store node definitions and shortcut mappings in **config.json**.
- **SBS node-group presets** store nodes, parameters, connections, and function graphs in **NodePresets.sbs**, with list and group metadata in **NodePresets_ui.json**.

The interface supports English and Simplified Chinese.

## Features

- UE Material Editor-style hold-key-and-click node creation.
- Independent left- and right-click bindings for every keyboard shortcut.
- Either mouse side can target JSON or SBS, including JSON/JSON and SBS/SBS combinations.
- Defaults to JSON on left click and SBS on right click without enforcing that layout.
- Automatically detects one selected node as JSON and two or more selected nodes as an SBS preset.
- Manual type, mouse button, name, group, and key selection in the Set Shortcut dialog.
- Optional SBS extension that runs in parallel with the original JSON workflow.
- Node-group presets preserve nodes, parameters, internal connections, dynamic properties, and related function graphs.
- SBS creation is recorded as one Designer undo operation.
- Manual Load targets the center of the visible Graph View and performs a second position correction.
- Optional Open Properties behavior after creating a JSON node.
- Customizable fluorescent hold indicator with Orb and Cross Star shapes.
- Collapsible JSON and SBS lists, SBS group filtering, and per-group collapsing.
- Custom alternate-row color for shortcut tables.
- Responsive dock layout, low-height scrolling, and persistent window dimensions.
- NodePresets.sbs is collapsed in Explorer after loading.
- Portable local backend resolution when the complete plugin directory is moved.

## Compatibility

- Adobe Substance 3D Designer **16.0.3 or later**
- Python 3.13
- Qt 6 / PySide6
- A PySide2 import fallback remains, but Designer 16.0.x is the primary target

## Installation

1. Download or clone the complete UEStyleNodeCreator directory.
2. Keep the relative layout of the core files intact:

~~~text
UEStyleNodeCreator/
├─ UEStyleNodeCreator.py
├─ NodePreset.py
├─ NodePresets.sbs
├─ NodePresets_ui.json
├─ config.json
├─ pluginInfo.json
└─ core/
~~~

3. Open **Tools → Plugin Manager** in Substance 3D Designer.
4. Click **Browse** and select **UEStyleNodeCreator.py**.
5. Enable the plugin. The UE Style Node Creator dock appears in the UI.

> Copy the entire directory rather than the entry script alone. The SBS extension depends on the adjacent NodePreset.py and NodePresets.sbs files.

## Quick Start

### Save a single-node shortcut

1. Select one node in the Graph View.
2. Use the configured Selection Shortcut or click **Save Selection**.
3. Confirm **JSON Single Node** in Set Shortcut.
4. Select Left Click or Right Click and enter a keyboard shortcut.
5. Hold the key and press the assigned mouse button to create the node.

### Save an SBS node-group preset

1. Enable **Enable SBS Preset Extension** in Settings.
2. Select two or more nodes in the Graph View.
3. Click Save Selection or use the Selection Shortcut.
4. Confirm **SBS Preset** in Set Shortcut.
5. Enter a preset name, optional group, keyboard shortcut, and mouse button.
6. Create the group with the shortcut gesture or click Load in the SBS Presets list.

### Creation gesture

~~~text
Hold a shortcut key
   ├─ The fluorescent indicator appears after the hold delay
   ├─ Left click runs the Left Click binding
   ├─ Right click runs the Right Click binding
   ├─ Keep holding to place more instances
   └─ Release the key to leave creation mode
~~~

| Left Click | Right Click | Supported |
|---|---|---|
| JSON | SBS | Yes |
| SBS | JSON | Yes |
| JSON | JSON | Yes |
| SBS | SBS | Yes |

Conflicts are evaluated using the complete keyboard-key-plus-mouse-button gesture. One exact gesture can have only one target. Replacing a conflict affects only that mouse side.

## JSON and SBS Storage

| Item | JSON Single Node | SBS Preset |
|---|---|---|
| Purpose | Create one node quickly | Save and recreate a complete node group |
| Data file | config.json | NodePresets.sbs |
| UI metadata | config.json | NodePresets_ui.json |
| Default mouse button | Left Click | Right Click |
| Recorded data | UID, name, instance URL, and related fields | Nodes, parameters, connections, dynamic properties, and function graphs |
| Extension required | No | Yes |

Disabling the SBS extension leaves JSON unchanged. Enabling it runs both systems simultaneously; it does not switch the plugin into a separate mode.

## SBS Extension Lifecycle

- While enabled, NodePresets.sbs stays loaded as the resident preset storage package.
- If the package is closed manually, the plugin finds and reloads it when needed.
- Reloading the plugin restores the SBS package when the extension was previously enabled.
- Disabling the extension unloads the package after a short delay.
- NodePresets.sbs is collapsed in Explorer by default.
- Designer may show a yellow no-Output warning because this is a storage package, not a material output graph. The warning does not prevent preset operations.

## Settings

| Setting | Description |
|---|---|
| Language | English or Simplified Chinese; refreshes the main UI and common dialogs immediately |
| Glow | Fluorescent hold-indicator color |
| Row Color | Alternate shortcut-table row color |
| Shape | Orb or Cross Star |
| Size | Indicator display size |
| Delay | Hold duration required to enter creation mode |
| Sel.Key | Shortcut for saving the current selection; combinations are supported |
| Open Properties | Select a newly created JSON node and show Properties |
| Enable SBS Preset Extension | Enable or disable SBS node-group presets |

At narrow widths, fields, buttons, and tables switch to compact layouts. At low heights, the dock uses vertical scrolling instead of squeezing controls. The last dock dimensions are saved and restored after reload.

## Lists and Groups

- JSON Shortcuts and SBS Presets can be collapsed independently.
- Collapsing JSON releases its content area and moves the SBS header below it.
- When both lists are expanded, they share the available height evenly.
- The SBS list includes a Group filter.
- Individual SBS groups can be collapsed.
- Load and delete use separate columns to prevent overlap.

## Configuration Files

### config.json

Stores UI settings, JSON shortcuts, and references to SBS preset shortcuts. Storage keys separate the two mouse sides, for example **B|left** and **B|right**.

### NodePresets.sbs

The actual SBS storage package for node-group presets. Deleting a preset removes the matching graph from this package and saves the file.

### NodePresets_ui.json

Stores SBS preset names, groups, list presentation, and shortcut references. It does not replace the node data in NodePresets.sbs.

## Reserved Keys

These Designer viewport or engine keys cannot be used as creation shortcuts:

> F / Z / H / V / F9

The shortcut reminder remains visible in Set Shortcut.

## Node Creation and Connections

- Compositing, Function, and Input Value nodes can be created by UID.
- Instance nodes are resolved through a pkg:// URL; the dependency package must be available.
- When auto-connect is enabled, the plugin attempts to connect a new node to the current selection.
- Open Properties performs a lightweight selection of the new JSON node and raises the Properties dock.

## Undo and Placement

- SBS group creation is wrapped in one Designer Undo Group, so one Undo removes the complete new group.
- Shortcut creation uses the mouse click position.
- Manual SBS Load uses the visible center of the current Graph View rather than the graph origin.
- A second correction pass handles asynchronous layout offsets.

## Troubleshooting

### A shortcut does not respond

- Click the Graph View first to give it focus.
- Hold the key longer than the configured Delay.
- Check whether the key has a binding for the mouse side being pressed.
- Reload the plugin if the event filters need to be installed again.

### An SBS preset cannot be created

- Confirm that the SBS extension is enabled.
- Confirm that NodePreset.py and NodePresets.sbs are in the current plugin directory.
- If the SBS package was closed manually, run Create or Load again so it can reload.

### SBS loading fails after moving the plugin

- Copy the complete plugin directory.
- Remove any backend path that still points to the old directory.
- Reload the plugin; local backend and SBS files are preferred automatically.

### Loaded nodes appear in the wrong place

- Make sure the intended Graph View is active.
- Manual Load targets the visible viewport center; shortcut creation targets the click position.

### Explorer shows a yellow warning icon

NodePresets.sbs is a data storage package and does not require an Output. The warning does not indicate damaged presets.

## Project Structure

~~~text
UEStyleNodeCreator/
├─ UEStyleNodeCreator.py       # Plugin entry point and lifecycle
├─ NodePreset.py               # SBS read/write backend
├─ NodePresets.sbs             # Node-group preset data
├─ NodePresets_ui.json         # SBS list and group metadata
├─ config.json                 # Settings and shortcut mappings
├─ pluginInfo.json             # Plugin metadata
└─ core/
   ├─ config.py                # Configuration persistence
   ├─ create_mode.py           # Keyboard/mouse creation state
   ├─ graph_manager.py         # Node creation and Properties handling
   ├─ i18n.py                  # English / Simplified Chinese translations
   ├─ node_database.py         # Node-definition scanning and search
   ├─ preset_module.py         # SBS lifecycle and placement
   ├─ shortcut_manager.py      # Gesture routing and conflicts
   └─ ui.py                    # Dock, lists, groups, and dialogs
~~~

## Development and Verification

1. Compile-check every Python file.
2. Reload the plugin in Designer.
3. Test JSON and SBS on both mouse buttons.
4. Test SBS Save, Load, Delete, and one-step Undo.
5. Resize the dock and verify collapsing, scrolling, and size restoration.
6. Switch both interface languages and inspect the main dialogs.

## Author

- Fitr
- [ArtStation](https://www.artstation.com/ftir)
