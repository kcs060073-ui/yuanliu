# 源流 · 园区短视频草稿工作台

**从招商文案到剪映专业版可编辑草稿，让素材查找、字幕切分和时间线搭建更省时。**

源流面向园区招商和信息流短视频制作：输入文案，结合本地素材库的园区、语义和画面方向，生成包含画面、字幕等轨道的剪映草稿。你可以在剪映中继续调整、配音和导出，保留人工审核与创作空间。

> 当前源码发布清单版本：**261102-P06**。本仓库提供项目源码、配置和模板；使用时需自行准备剪映专业版、脚本文案和本地素材。

## 能做什么

| 能力 | 说明 |
| --- | --- |
| 文案转草稿 | 将文本切分为字幕，组合素材、字幕、背景音乐和花字，写入剪映草稿目录。 |
| 语义素材匹配 | 根据文案语义、园区及画面方向，从本地素材库选择候选画面；支持视频和图片。 |
| 分镜预览与调整 | 先生成分镜计划，预览候选素材并选择，再按确认后的计划生成草稿。 |
| 横竖屏适配 | 探测视频显示方向，并结合素材标注筛选画面。 |
| 多种制作风格 | 提供「三连否」「招商」「没想到」三种基础风格及对应模板配置。 |
| 工作台与批量处理 | 通过 Windows 图形界面输入文案、设置素材和方向，支持批量生成与任务回执。 |
| 素材包管理 | 为团队素材包生成清单，校验并安装到本地素材库。 |
| 草稿保护 | 默认避免覆盖已有草稿；素材库为空时默认拦截生成。 |

## 工作流程

```text
准备文案和本地素材
        ↓
选择园区、方向与风格 → 预览分镜并调整候选素材（可选）
        ↓
生成剪映可编辑草稿 → 在剪映中检查、调整和导出
```

默认生成**无配音**草稿。需要配音时，可启用占位配音轨，在剪映中使用「文本朗读」，再使用归位脚本整理时间线。背景音乐从本机可用音乐中选择；找不到可用音乐时会提示，草稿可能没有背景音乐。

## 环境要求

- **主要运行环境：** Windows、剪映专业版和 Python 3.8 或更高版本。图形工作台需要 Python 支持 `tkinter`。
- **剪映安装：** Windows 草稿加解密依赖剪映安装目录中的 `videoeditor.dll`，程序会尝试自动查找，也可手动指定安装目录。
- **素材：** 准备本地视频或图片素材库。仓库没有附带实际的园区素材库或音乐素材。
- **macOS：** 路径适配已有基础支持，但草稿加解密尚未闭环，当前使用明文方式；加密版剪映可能无法打开生成的草稿。Windows 桌面快捷方式安装器不支持 macOS。

### 已知兼容问题

项目在 **剪映 11.7.0.14652** 上记录了草稿解密返回空结果的问题：草稿读取和解密自检可能失败，部分依赖读取历史草稿的功能也会受影响。项目记录显示该版本的加密写入仍可运行，但请在自己的环境中打开草稿并核实；在问题解决前，不建议将解密自检结果作为批量生产的唯一判断依据。详见 [SKILL.md 的已知问题](SKILL.md#已知问题2026-11-02未闭环)。

## 快速开始：图形工作台

1. 在 GitHub 页面点击 **Code → Download ZIP** 下载源码并解压，或使用 Git 克隆：

   ```powershell
   git clone https://github.com/kcs060073-ui/yuanliu.git
   cd yuanliu
   ```

2. 安装 Python 和剪映专业版，准备好文案及本地素材目录。
3. 在解压或克隆后的目录中，双击 `workbench/启动工作台.bat`。如需创建桌面快捷方式，可双击 `workbench/install.bat`。
4. 在工作台输入文案，选择素材目录和成片方向，检查自动识别的风格与园区；需要时先预览分镜并调整候选素材。
5. 生成后打开剪映，检查画面、字幕和音频，再按需编辑与导出。

多条文案批量处理时，使用**单独一行 `---`** 分隔脚本；普通空行保留为同一脚本内的段落。

## 命令行使用

以下命令在仓库根目录的 PowerShell 中运行。请将示例路径替换为自己电脑上的实际路径。

### 1. 检查运行环境

```powershell
python scripts/doctor.py --materials "D:/园区素材" --out "D:/JianyingPro Drafts"
```

自检会检查 Python、剪映安装目录、草稿目录、素材库与关键配置等。根据输出提示处理缺失项后再生成草稿。

### 2. 生成草稿

```powershell
python scripts/make_park_video.py "D:/文案/脚本.txt" `
  --materials "D:/园区素材" `
  --orientation 竖屏 `
  --style 2 `
  --out "D:/JianyingPro Drafts"
```

常用选项：

| 参数 | 用途 |
| --- | --- |
| `--materials` | 本地素材根目录，支持普通素材文件夹或集中素材库。 |
| `--orientation` | 成片方向，如 `竖屏` 或 `横屏`。 |
| `--style` | `1` 三连否、`2` 招商、`3` 没想到。 |
| `--park` | 指定园区；集中素材库可按园区和通用素材进行匹配。 |
| `--out` | 剪映草稿根目录；请按本机实际目录填写。 |
| `--copy-materials` | 将选用素材复制到草稿目录；默认引用原素材路径。 |
| `--with-tts` | 生成占位配音轨，供后续在剪映中使用「文本朗读」。 |
| `--plan` | 使用已确认的分镜计划生成草稿。 |

完整参数可运行 `python scripts/make_park_video.py --help` 查看。已有草稿默认不会被覆盖；只有同时明确指定 `--name` 和 `--overwrite` 时才允许覆盖指定目标。

### 3. 先生成分镜计划（可选）

```powershell
python scripts/storyboard.py create "D:/文案/脚本.txt" `
  --materials "D:/园区素材" `
  --orientation 竖屏 `
  --out "D:/文案/plan.json"

python scripts/make_park_video.py "D:/文案/脚本.txt" `
  --materials "D:/园区素材" `
  --orientation 竖屏 `
  --plan "D:/文案/plan.json" `
  --out "D:/JianyingPro Drafts"
```

分镜计划可先通过工作台预览并调整候选素材，再用于生成草稿。更多命令见 `python scripts/storyboard.py --help`。

### 4. 配音后整理时间线（可选）

生成时加入 `--with-tts`，在剪映中对字幕执行「文本朗读」，保存并关闭草稿，再运行：

```powershell
python scripts/arrange_park_video.py "D:/JianyingPro Drafts/你的草稿名" `
  --materials "D:/园区素材"
```

脚本会尝试按配音时长整理字幕、音频和画面。该步骤需要读取草稿，因此会受到上文所述剪映版本解密兼容问题的影响。

## 团队素材管理

团队管理员可为已整理好的素材目录生成版本清单；成员下载素材包后，可校验并安装到本地素材库：

```powershell
# 管理员：为素材包生成清单
python scripts/material_manifest.py build "D:/素材发布包" --version 2026.11.02-1

# 成员：安装并检查素材库
python scripts/material_manifest.py install "D:/下载/素材包" --target "D:/园区素材"
python scripts/material_manifest.py verify "D:/园区素材"
```

素材包的上传和下载需由团队自行完成；这些命令负责本地清单、校验和安装。

## 项目结构

```text
.
├── config/                 # 风格、语义、园区规则等配置
├── scripts/                # 草稿生成、素材匹配、分镜、诊断与素材管理
├── templates/              # 三种基础风格模板
├── tests/                  # 回归与功能检查脚本
├── workbench/              # Windows 图形工作台及快捷方式安装器
├── 语义规则分配表.xlsx      # 园区与语义规则表
├── SKILL.md                # 功能说明、使用细节和已知问题
├── PROJECT_STATUS.md       # 项目历史与开发记录
└── release_manifest.json   # 源码发布清单及文件校验信息
```

本地运行时可能产生 `output/`、`state/` 等数据。提交或共享时应注意区分源码与本机任务记录、素材和配置。

## 验证与排查

```powershell
# 校验发布清单中记录的文件
python scripts/release_manifest.py .

# 运行部分无需剪映真机操作的回归检查
python tests/test_manifest_packaging.py
python tests/test_template_privacy.py
```

如生成失败，建议先运行 `python scripts/doctor.py`，检查素材路径、Python 的 `tkinter`、剪映安装位置和草稿目录权限。实际草稿效果仍需在目标版本的剪映中打开检查。

## 更多说明

- [详细使用说明与已知问题](SKILL.md)
- [项目开发和历史记录](PROJECT_STATUS.md)
- [发布清单](release_manifest.json)

本仓库目前未提供许可证文件；代码及素材的使用授权请向项目维护者确认。

