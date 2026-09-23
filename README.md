# flick ⚡

**flick** 是面向 iOS 模拟器的快速手势决策与动作执行工具。

它专为大语言模型（如 Codex、Claude Code）的移动端 Computer Use 场景设计，作为 Agent 的“底层手势小脑”：大模型负责高阶规划与复杂推理，flick 在底层接收单步意图，在 500 毫秒内完成候选元素匹配、防遮挡校验并执行真实手势（点击、滑动、输入）。

---

## 核心特性

- **低时延决策 (~500 ms)**：采用 TypeSafe Jev 离散决策模型替代通用大模型做微观手势选择，单步决策时延相比常规 LLM 降低约 85%。
- **无截图开销**：直接读取并清洗 iOS 底层辅助功能树（Accessibility Tree），将整屏数百个原始视图压缩为十余个紧凑的结构化候选元素，无需截图上传与多模态视觉计算。
- **内置安全链**：
  - **动态重定位 (Relocalize)**：克服移动端视图无持久句柄的问题，在执行前对目标进行几何与属性一致性重校验。
  - **防遮挡与防抢点校验 (Hit-Check)**：下发手势前通过点命中测试验证实际触控层，防止通知横幅、加载弹窗遮挡导致误触。
  - **敏感操作拦截**：命中支付、删除、关机等关键词时自动拒绝执行并上报。
- **连续置信度支持**：原生输出 0.0~1.0 连续归一化的置信度分布，支持外部系统配置安全门禁（如低于 0.60 自动暂停并交还人工或大模型干预）。
- **单文件独立运行**：支持编译为独立的二进制文件，无需 Python 或虚拟环境依赖，自带 API Key (BYOK) 即插即用。

---

## 协作架构

```
┌──────────────────────────────────────┐
│  父 Agent (Codex / Claude Code 等)   │  负责跨应用规划、复杂推理与文本生成
└──────────────────┬───────────────────┘
                   │  单步意图: flick run "<goal>" --completion "<condition>"
                   ▼
┌──────────────────────────────────────┐
│                flick                 │
│  ┌────────────────────────────────┐  │
│  │ 1. 结构化观测 (AX 树原子清洗)   │  │
│  │ 2. Jev 决策 (多 Head 并行分类)  │  │  ~500 ms 单步闭环
│  │ 3. 安全校验 (重定位 + 命中测试) │  │
│  │ 4. 物理执行 (idb tap/swipe/cmd)│  │
│  └────────────────────────────────┘  │
└──────────────────┬───────────────────┘
                   │  真实手势 / 读回校验
                   ▼
┌──────────────────────────────────────┐
│        iPhone 模拟器 (iOS 26)        │
└──────────────────────────────────────┘
```

---

## 快速开始

### 依赖要求
- macOS 系统与 Xcode Command Line Tools
- 已安装并启动的 iOS 模拟器
- [Facebook idb](https://github.com/facebook/idb) (`idb_companion` 与 `idb-cli`)
- TypeSafe API Key（在 [TypeSafe AI](https://typesafe.ai) 获取）

### 安装

直接使用预编译的单文件二进制（或从源码编译）：

```bash
# 从源码构建独立二进制 (依赖 uv)
git clone https://github.com/szupzj18/flick.git
cd flick
uv sync
uv run pyinstaller --noconfirm --clean --onefile --name flick main.py

# 安装至系统路径
cp dist/flick ~/.local/bin/flick
```

验证安装：
```bash
flick --help
```

---

## 使用方式

设置目标设备 UDID（或通过 `--udid` 传参）：
```bash
export IDB_UDID="<your-simulator-udid>"
```

### 1. 作为 Agent 工具单步执行 (`flick run`)

这是提供给外部大模型或自动化脚本调用的核心命令。每次调用执行单步操作，并向标准输出打印最新的界面状态 JSON：

```bash
flick run "点击通用设置" \
  --completion "进入了包含关于本机菜单的子页面" \
  --api-key "your-typesafe-key"
```

**输出格式示例**：
```json
{
  "status": "success",
  "action_taken": "Executed TAP on '通用'",
  "page_changed": true,
  "goal_satisfied_probability": 0.04,
  "current_screen": {
    "front_app": "Preferences",
    "elements": [
      {
        "index": "1",
        "role": "Button",
        "label": "关于本机",
        "value": "",
        "operations": ["TAP"]
      }
    ],
    "page_text": "设置\n通用\n关于本机..."
  }
}
```

如需输入文本，提供 `--text` 参数，底层自动通过剪贴板与模拟器热键安全粘贴：
```bash
flick run "在搜索框输入查询词" \
  --completion "搜索框内容已输入" \
  --text "李永乐老师"
```

### 2. 交互式调试与观测 (`flick observe`)

用于人机调试，查看 flick 底层提取清洗后的可交互元素表：

```bash
# 打印当前屏幕清洗后的决策元素列表
flick observe

# 切换指定应用并输出完整快照 JSON
flick observe --app com.apple.Preferences --json
```

---

## 实测性能对比

在真实应用界面（包含复杂无障碍树与横向滑动栏的 App）下，flick (Jev 1.13.0) 与前沿通用大模型 (gemini-3.8-flash) 的单步决策对比数据：

| 评估维度 | flick (Jev 1.13.0) | 通用大模型 (LLM) | 表现差异 |
|---|---:|---:|---|
| **平均端到端时延** | **593.8 ms** | 4143.4 ms | **flick 提速 7.0 倍** |
| **操作类型准确率** | **90.0% (9/10)** | **90.0% (9/10)** | 持平 |
| **手势物理语义理解** | **正确 (SCROLL_UP)** | 错误 (SCROLL_DOWN) | 大模型混淆了“向下翻看”与触控手势方向 |
| **确定性置信度** | **原生提供 (0.0~1.0)** | 不支持 (自回归文本) | flick 支持直接配置阈值拦截异常 |
| **格式解析故障风险** | **零风险 (Typed API)** | 存在 JSON 解析异常风险 | flick 后端强约束，无字段幻觉 |

---

## 许可证

[MIT](LICENSE)
