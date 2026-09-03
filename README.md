# UE Style Node Creator

面向 Adobe Substance 3D Designer 的 UE 风格快捷节点创建插件。按住键盘快捷键进入创建状态，再在 Graph View 中用左键或右键放置单节点或整组节点预设。

插件同时支持两套相互独立、可并行工作的存储后端：

- **JSON 单节点快捷创建**：节点定义和快捷键保存在 `config.json`。
- **SBS 节点组预设**：节点、参数、连接及函数图等内容保存在 `NodePresets.sbs`，界面分组信息保存在 `NodePresets_ui.json`。

界面支持 English / 简体中文切换。

## 主要功能

- UE Material Editor 风格的“按住快捷键 + 鼠标点击”创建流程。
- 一个键盘按键可分别绑定左键和右键，两侧可以任意设置为 JSON 或 SBS。
- 默认手势为 JSON 左键、SBS 右键，但不强制绑定。
- 单节点自动识别为 JSON，选择两个及以上节点时自动识别为 SBS 节点组。
- `Set Shortcut` 中可手动切换 JSON / SBS 类型、鼠标按键、名称、分组和快捷键。
- SBS 扩展可独立开启；开启后与 JSON 功能并行运行。
- 节点组预设记录组内节点、节点参数、内部连接、动态属性及相关函数图。
- SBS 预设创建纳入 Designer 撤销历史，创建后可使用一次撤销恢复画布。
- 手动 Load 会将预设放置到当前可见 Graph View 的中心，并进行二次位置校正。
- 可选“创建后打开 Properties”，自动选中新节点并显示参数面板。
- 长按提示支持荧光 Orb 和 Cross Star 两种形状，并可自定义颜色、尺寸与触发延迟。
- JSON 与 SBS 列表均可折叠；SBS 预设支持按组筛选与组内折叠。
- 快捷键表交替行颜色可自定义。
- Dock 窗口支持窄宽度自适应、低高度滚动，并记录上次窗口尺寸。
- `NodePresets.sbs` 在 Explorer 中加载后默认折叠。
- 插件目录可整体移动：SBS 后端、预设包和界面元数据均优先从当前插件目录解析。

## 兼容性

- Adobe Substance 3D Designer **16.0.3 或更高版本**
- Python 3.13
- Qt 6 / PySide6
- 插件代码保留 PySide2 导入回退，但主要测试目标为 Designer 16.0.x

## 安装

1. 下载或克隆整个 `UEStyleNodeCreator` 目录。
2. 保持以下核心文件的相对位置不变：

   ```text
   UEStyleNodeCreator/
   ├─ UEStyleNodeCreator.py
   ├─ NodePreset.py
   ├─ NodePresets.sbs
   ├─ NodePresets_ui.json
   ├─ config.json
   ├─ pluginInfo.json
   └─ core/
   ```

3. 在 Substance 3D Designer 中打开 `Tools → Plugin Manager`。
4. 点击 `Browse`，选择 `UEStyleNodeCreator.py`。
5. 启用插件后，界面中会出现 **UE Style Node Creator** Dock。

> 请复制整个目录，不要只复制入口脚本。SBS 扩展依赖同目录下的 `NodePreset.py` 和 `NodePresets.sbs`。

## 快速使用

### 保存单节点快捷方式

1. 在 Graph View 中选择一个节点。
2. 使用设置中的 Selection Shortcut，或点击界面的 `Save Selection`。
3. 在 `Set Shortcut` 弹窗中确认类型为 `JSON Single Node`。
4. 选择 Left Click 或 Right Click，并设置键盘快捷键。
5. 保存后，按住该键并点击对应鼠标按键即可创建节点。

### 保存 SBS 节点组预设

1. 在 Settings 中开启 `Enable SBS Preset Extension`。
2. 在 Graph View 中选择两个或以上节点。
3. 点击 `Save Selection` 或使用 Selection Shortcut。
4. 在 `Set Shortcut` 中确认类型为 `SBS Preset`。
5. 设置预设名称、可选分组、键盘快捷键和鼠标按键。
6. 保存后，可用快捷手势创建，也可在 SBS Presets 列表中点击 Load。

### 创建手势

```text
按住快捷键
   ├─ 到达长按延迟后显示荧光提示
   ├─ 左键：执行该键的 Left Click 绑定
   ├─ 右键：执行该键的 Right Click 绑定
   ├─ 按住期间可以连续创建
   └─ 松开按键退出创建状态
```

同一个键允许使用以下任意组合：

| Left Click | Right Click | 是否支持 |
|---|---|---|
| JSON | SBS | 是 |
| SBS | JSON | 是 |
| JSON | JSON | 是 |
| SBS | SBS | 是 |

冲突判断以完整手势“键盘按键 + 鼠标按键”为单位。同一手势只能指向一个目标；保存冲突项时，插件会提示替换对应一侧，不会误删另一侧绑定。

## JSON 与 SBS 的区别

| 项目 | JSON Single Node | SBS Preset |
|---|---|---|
| 主要用途 | 快速创建单个节点 | 保存并创建完整节点组 |
| 存储文件 | `config.json` | `NodePresets.sbs` |
| UI 元数据 | `config.json` | `NodePresets_ui.json` |
| 默认鼠标按键 | Left Click | Right Click |
| 记录内容 | 节点 UID、名称、实例 URL 等 | 节点、参数、连接、动态属性、函数图等 |
| 是否需要扩展开关 | 否 | 是 |

SBS 扩展关闭时，JSON 创建逻辑保持不变。扩展开启时，两套系统同时运行，而不是互斥模式。

## SBS 扩展生命周期

- 开启扩展后，`NodePresets.sbs` 会作为预设存储包常驻加载。
- 如果用户手动关闭该 SBS，插件会在需要时重新查找并加载。
- 重载插件时，如果扩展上次处于开启状态，会自动恢复 SBS 包。
- 关闭扩展后，SBS 包会按设置延迟卸载，避免切换过程中立即中断操作。
- Explorer 中的 `NodePresets.sbs` 默认保持折叠，减少界面占用。
- 因该包用于存储预设而不是输出材质，Designer 可能显示“无 Output”的黄色警告图标；这不影响预设保存或加载。

## 设置

展开 Dock 底部的 `Settings` 可调整：

| 设置 | 说明 |
|---|---|
| Language | English / 简体中文；修改后立即刷新主要界面和常用弹窗 |
| Glow | 长按创建提示的荧光颜色 |
| Row Color | 快捷键列表交替行颜色 |
| Shape | `Orb` 或 `Cross Star` |
| Size | 光点显示尺寸 |
| Delay | 进入长按创建模式所需时间 |
| Sel.Key | 保存当前选择的快捷键，支持组合键 |
| Open Properties | JSON 节点创建成功后自动选中并打开 Properties |
| Enable SBS Preset Extension | 启用或关闭 SBS 节点组预设模块 |

窗口宽度变窄时，搜索框、按钮和表格会切换到紧凑布局；窗口高度不足时，内容区域使用纵向滚动，不压缩所有控件。窗口尺寸会保存到配置，并在插件重载时恢复。

## 列表与分组

- `JSON Shortcuts` 和 `SBS Presets` 可分别折叠。
- JSON 收起后释放其内容区域，SBS 折叠标题会贴近 JSON 标题。
- 两个列表同时展开时，各自使用可用高度的一半。
- SBS 列表可通过 Group 下拉框筛选。
- SBS 预设在列表中按组显示，并可折叠单个组。
- Load 与删除按钮使用独立列，避免窄窗口下重叠。

## 配置与数据文件

### `config.json`

保存界面设置、JSON 快捷键和 SBS 快捷键引用。快捷项使用如下结构区分左右鼠标按键：

```json
{
  "shortcuts": {
    "B|left": {
      "node_uid": "sbs::compositing::blend",
      "node_name": "Blend",
      "shortcut_key": "B",
      "mouse_button": "left"
    }
  },
  "preset_shortcuts": {
    "B|right": {
      "preset_id": "example_preset_id",
      "preset_name": "Example Group",
      "group": "Examples",
      "shortcut_key": "B",
      "mouse_button": "right",
      "entry_type": "preset"
    }
  }
}
```

### `NodePresets.sbs`

SBS 节点组预设的实际存储包。删除 SBS 预设时，插件会从该包移除对应图并保存文件。

### `NodePresets_ui.json`

保存 SBS 预设名称、分组、列表显示及快捷引用等 UI 元数据。它不替代 `NodePresets.sbs` 中的节点数据。

## 保留按键

以下按键由 Substance 3D Designer 的视口或引擎操作占用，不能作为创建快捷键：

> `F` / `Z` / `H` / `V` / `F9`

完整快捷键说明会保留在 `Set Shortcut` 弹窗中。

## 节点创建与连接

- Compositing、Function、Input Value 等节点可通过 UID 创建。
- Instance 节点通过 `pkg://` URL 解析；对应依赖包需要可用。
- 启用自动连接时，插件会根据当前选择尝试连接新创建节点。
- 开启 `Open Properties` 后，插件会对新 JSON 节点执行轻量级选中，并将 Properties Dock 提到前台。

## 撤销与位置

- SBS 节点组创建被包装为一个 Designer Undo Group，一次撤销可移除整组新建节点。
- 快捷创建发生在鼠标点击位置。
- SBS 列表中的手动 Load 使用当前 Graph View 可见视口中心，而不是图的初始原点。
- 加载完成后会进行一次位置校正，以处理 Designer 创建节点时的异步布局偏移。

## 常见问题

### 快捷键没有响应

- 先点击 Graph View，使其获得焦点。
- 确认按住时间超过 Settings 中的 Delay。
- 检查该键对应的 Left Click / Right Click 是否已绑定。
- 必要时重载插件，以重新安装事件过滤器。

### SBS 预设无法创建

- 确认 `Enable SBS Preset Extension` 已开启。
- 确认 `NodePreset.py` 与 `NodePresets.sbs` 位于当前插件目录。
- 如果 SBS 被手动关闭，重新执行一次创建或 Load，插件会尝试重新加载。

### 复制到其他目录后 SBS 读取失败

- 必须复制整个插件目录。
- 不要在设置中保留旧目录的手动后端路径。
- 重新加载插件；模块会优先使用当前目录中的 `NodePreset.py` 与 `NodePresets.sbs`。

### Load 后节点位置不正确

- 确保当前 Graph View 是已聚焦的主要视图。
- 手动 Load 以可见视口中心为目标；快捷创建则以鼠标点击位置为目标。

### Explorer 中出现黄色感叹号

`NodePresets.sbs` 是数据存储包，本身不需要 Output。Designer 的警告仅表示图没有输出，不代表预设损坏。

## 项目结构

```text
UEStyleNodeCreator/
├─ UEStyleNodeCreator.py       # Designer 插件入口与生命周期
├─ NodePreset.py               # SBS 预设读写后端
├─ NodePresets.sbs             # 节点组预设数据
├─ NodePresets_ui.json         # SBS 列表与分组元数据
├─ config.json                 # 设置及快捷键映射
├─ pluginInfo.json             # 插件元信息
└─ core/
   ├─ config.py                # 配置迁移与持久化
   ├─ create_mode.py           # 键盘/鼠标创建状态与视觉提示
   ├─ graph_manager.py         # 节点创建、连接及 Properties 交互
   ├─ i18n.py                  # English / 简体中文翻译
   ├─ node_database.py         # 节点定义扫描与搜索
   ├─ preset_module.py         # SBS 后端加载、保存、卸载与定位
   ├─ shortcut_manager.py      # JSON/SBS 双手势路由和冲突处理
   └─ ui.py                    # Dock、列表、分组、弹窗和自适应布局
```

## 开发与验证

插件运行在 Substance 3D Designer 内置 Python 环境中。修改后建议至少完成以下检查：

1. 使用 Python 编译检查所有 `.py` 文件。
2. 在 Designer 中 Reload 插件。
3. 分别验证 JSON 左/右键和 SBS 左/右键组合。
4. 验证 SBS 保存、Load、删除和一次撤销。
5. 缩放 Dock 的宽度与高度，检查折叠、滚动和尺寸恢复。
6. 切换 English / 简体中文并检查主要弹窗。

## English Summary

UE Style Node Creator is a Substance 3D Designer 16.0.3+ plugin for keyboard-and-mouse node placement. It supports JSON-backed single-node shortcuts and SBS-backed node-group presets at the same time. Each keyboard key has independent left- and right-click bindings, and either side can target JSON or SBS. The plugin also includes undoable preset creation, viewport-centered manual loading, optional Properties focus, customizable glow indicators, collapsible grouped lists, responsive scrolling, persistent window size, portable local SBS storage, and an English / Simplified Chinese interface.

## Author

- Fitr
- [ArtStation](https://www.artstation.com/ftir)
