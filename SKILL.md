---
name: yuanliu-park-video
description: 制作和管理园区信息流剪映草稿。用于脚本语义切分、完整本地素材库匹配、分镜候选预览、素材替换、草稿生成、任务回执、素材版本包安装与安全升级；仅适用于源流园区信息流工作流。
metadata:
  version: 260916-P05
  openclaw:
    emoji: "🏭"
    category: "video"
    tags: ["剪映", "短视频", "园区招商", "信息流", "草稿生成", "TTS", "竖屏"]
---

# 园区信息流短视频自动制作

根据脚本文本自动生成剪映专业版草稿，包含完整时间线（素材+字幕+TTS配音+BGM+免责花字），回加密后放入剪映草稿目录，用户打开剪映即可进一步智能包装。

## P05 演示前稳定性要点（260916-P05）

**一句话**：连续生成 / 批量生成 / 多窗口并发，任何已有草稿都不会被静默覆盖；
跨电脑使用不再有路径、线程和误操作坑。

| # | 要点 | 落点 |
|---|---|---|
| 1 | 草稿目录**独占预留**（`os.mkdir` 原子判定），任务ID随机段 8 位；已存在有效草稿默认绝对禁止覆盖，仅 `--name` + `--overwrite` 显式放行 | `scripts/draft_naming.py` |
| 2 | GUI 与 CLI **共用同一命名算法**；自检/弹窗/回执一律用子进程打印的真实目录 `DRAFT_DIR=` | `draft_naming` / `_run_cmd` |
| 3 | 工作台后台线程只跑 Python/子进程 + 回传队列；参数启动前冻结；Tk 操作全在主线程；关窗后不碰已销毁控件；异常必恢复按钮 | `workbench/workbench_gui.py` |
| 4 | 空素材库**默认拦停**（GUI/CLI/分镜预览同一判据）；调试需显式 `--allow-empty-materials` | `material_matcher.validate_materials_dir` |
| 5 | 普通空行保留为段落，**只有单独一行 `---`** 才切分多条脚本 | `scripts/script_split.py` |
| 6 | 草稿内素材名 = 原文件名 + 源路径哈希 8 位；同源只复制一次；原子替换 | `make_park_video.copy_materials_into` |
| 7 | 模板瘦身为纯元数据并去个人化；发布包全文不含管理员用户名/设备标识 | `templates/*.json` / `sanitize_platform` |
| 8 | BGM 只从本机真实存在的音频里选，找不到就明确报「本机未找到可用BGM」；字体运行时探测，不写死盘符与版本号 | `bgm_pool` / `jianying_path.find_font` |
| 9 | 安装入口统一为 `install_p05.py` / `安装或升级P05.bat`，版本号读发布清单不硬编码 | `install_p05.py` |

回归测试（新增）：`tests/test_draft_collision.py`、`tests/test_gui_thread_safety.py`、
`tests/test_script_split.py`、`tests/test_make_copy.py`、`tests/test_template_privacy.py`。

## P05 二轮补丁（2026-09-20，演示前实测报障）

| # | 问题 | 修复 |
|---|---|---|
| 1 | BGM 只扫「剪映安装目录\\User Data\\Cache\\music」，剪映装在 D 盘时缓存却在 `%LOCALAPPDATA%\\JianyingPro\\User Data\\Cache\\music` → 明明有曲子却报「本机未找到可用BGM」 | `bgm_pool.music_cache_dirs()` 列出**所有**候选位置，运行时展开环境变量、规范化绝对路径去重、目录不存在不报错 |
| 2 | `make_park_video` 调 `resolve_bgm()` **没传** `style['bgm']` → fixed 模式不知道要找哪首，只能拿候选列表第一个，"固定选择"名存实亡 | 传 `style_bgm=style.get('bgm', {})` + `warnings=`；匹配顺序 resource_id → 名称 → 文件名 → 归一化；匹配不到**安全降级并记警告**，绝不因 BGM 导致出片失败 |
| 3 | 真实命令 `--name 固定草稿 --overwrite` 却写进 `固定草稿-A1B2C3D4`，真正的 `固定草稿` 没被覆盖（**显式覆盖契约不一致**） | `reserve_draft_dir(exact=True)`：显式 `--name` 用**清洗后的准确名字、不追加任务尾码**；`--overwrite` 只认那一个候选；显式名无 `--overwrite` 时目录存在就换名，绝不写进去 |
| 4 | 覆盖路径下 `park_lock.json` 写在草稿之前 → 中途失败会在**别人的草稿目录**里留下痕迹 | 改为草稿原子落盘**之后**再写；失败运行零痕迹（测试 E4e 锁死） |
| 5 | 用户可见文案残留旧版本：`00-部署说明.txt` 写着 `install_p03.py`、doctor 建议写着「用 P03 完整安装包升级」、`SKILL.md` 标题还是「P03 团队版运行原则」 | 逐处修好；`pack_release.sync_deploy_text()` 补上**安装器文件名**里的版本段（老正则只认大写 `Pxx`，漏掉小写 `install_p03.py`）；打包时**多一道拦截**：包内出现指向旧版本的有效操作指令直接拒绝交付 |

回归测试（二轮新增）：`tests/test_bgm_lookup.py`、`tests/test_explicit_overwrite.py`、
`tests/test_release_wording.py`。

## P05 三轮补丁（2026-09-20，BGM 选曲实测报障）

| # | 问题 | 修复 |
|---|---|---|
| 1 | **出片 BGM 选中试听碎片**：时长下限（`MIN_DURATION_US`）只作用在「草稿库」来源，「剪映音乐缓存」来源漏了 —— 实测本机缓存 54 首里 42 首是 0.19~3.77 秒的试听片段，**约 86% 概率选中**，被循环铺满全片（实测一首 0.34s 的曲子被铺了 117 段，声音完全不能听） | `resolve_bgm()` 在**选曲前**统一过滤（判定与草稿库来源一致：`duration < MIN_DURATION_US` 即剔除，含 0/探测不出）；下限保持团队的 **15s**（实测碎片 <3.8s、真曲子 >67s，15s 落在空档中间两侧都留足余量）。修复后实测 54 首候选 → 稳定选出 8 首完整曲目（67~713s） |
| 2 | **mp3 时长解析越界拖垮整目录**：MPEG 采样率索引 3 是保留值（非法），代码直接拿去索引只有 3 个元素的表 → `IndexError`；而上层是「整目录 try/except」，**一个假同步字节就能让整个缓存目录 50+ 首全被跳过** | 判越界后跳到下一个候选同步字节（`i = head.find(b'\xff', i + 1)`）；缓存目录改为**逐文件** try/except，单个损坏文件只跳过并打印，不再拖垮整目录 |
| 3 | **大封面 ID3v2 导致时长算 0**：带几百 KB 封面的 mp3，第一个音频帧远在 64KB 读取窗口之外 → 找不到同步字节 → 时长 0 → 曲目被当成无效丢掉（实测本机 4 首正常 BGM 因此探测失败） | 先按 ID3v2 头长度 `seek` 到音频数据起点。⚠️ **无 ID3 时也必须 `seek(0)`** —— 漏了会让普通 mp3 从第 10 字节读起、时长反而全变 0（这个坑被 `test_cross_machine` 4d 用例抓到） |

回归测试（三轮调整）：`tests/test_bgm_lookup.py`、`tests/test_cross_machine.py` 的
音频夹具 `fake_mp3` 由 128KB（≈8s）改为 256KB（≈16s）、受控候选时长由 `100` 改为
`100*1000*1000`，与新下限自洽（**夹具要造「正常长度」的音频**，否则会与产线规则冲突）。

## P06 补丁（2026-11-02，横竖屏串味 + 并发丢更新）

| # | 问题 | 修复 |
|---|---|---|
| 1 | **竖版草稿混入横屏素材**：方向只看目录名/文件名，无标注素材（如 `南京/专业设施/` 8 条横竖混装）一律放行 → 竖版捞到 1280×720，成片被过度裁切/构图错位（实测 `261030-南京-竖-招商` 12s 处即中招） | 新增 `probe_media_orientation()`：读 tkhd 的**旋转矩阵**判定真实朝向（编码 1920×1080 + 旋转 90° = 竖屏，只看宽高会判反），**以画面为准、标注兜底**；标注与实际不符时打印 `[方向校正]` 清单 |
| 2 | **tkhd v0 偏移少算 8 字节**：v0 分支 base 写 76（应为 84），尺寸读出 `(0, 16384)` 这类垃圾值 → 视频尺寸探测基本失效，竖版被迫按 1920×1080 兜底 | 修正为 84（v1 的 96 本来就对）；尺寸探测加**尾部 moov 兜底**（DJI 机型 moov 在文件尾，只读头 2MB 必然 miss） |
| 3 | **画布缩放按编码尺寸算**：竖拍素材编码 1920×1080、显示 1080×1920，按前者算 `scale` 会把竖屏放大 1.78 倍、裁切过头 | 新增 `probe_media_display_size()`（旋转后尺寸），`arrange_park_video` / `draft_builder` / `replace_seg` 三处缩放计算统一改用它 |
| 4 | **并发写同一状态文件丢更新**：`FileLock.acquire()` 的 `open()` 写在重试循环外，Windows 上另一线程持锁时抛 `PermissionError` 直接逃出 → 4 线程各 25 次自增只得到 76（应 101） | `open()`+加锁整体纳入重试，失败先关句柄再等下一轮；超时才报 `LockTimeout` |
| 5 | **解密失败伪装成"写出 0 字节"**：剪映 11.7 起 `decrypt` 对任何输入（含它自己刚加密的内容）都返回 `ok=True` + 空串，旧代码静默写出空文件 | `decrypt` 空结果显式抛错、CLI 以 rc=2 退出且**不落盘**，自检输出带原因（见下「已知问题」） |

回归测试：`tests/test_media_orientation.py`（构造 v0/v1 tkhd 与旋转矩阵，锁死偏移、显示尺寸、
标注冲突以画面为准）；`tests/test_team_runtime.py` 的并发自增用例（101）由偶发变为稳定通过。

### 已知问题（2026-11-02，未闭环）

**剪映 11.7.0.14652 的 `EncryptUtils::decrypt` 不兼容**：同一进程内 `encrypt` 正常，
但 `decrypt` 对任意输入（包括它自己刚加密的字节、以及历史 11.6 生成的草稿）
在所有已试重载（2 参 / 3 参）与参数组合下都返回空串，`ok` 仍为 `True`。
密钥库 `%LOCALAPPDATA%\JianyingPro\User Data\Config\crypto_key_store.dat` 自 2026-08-10 未变，
排除"换密钥"。

- **影响面**：`verify_draft.py` 自检、`inspect_draft`、任何读草稿的脚本会报「解密失败」；
  `bgm_pool.scan_pool()` 的「草稿库曲目」来源会变 0（自动退回到剪映音乐缓存，仍能选到曲子）；
  **出片本身不受影响**（`encrypt` 正常，草稿照常写入）。
- **临时处置**：出片后直接在剪映里打开草稿目视确认；如需自检，回退剪映到 11.6 及以下。
- **待办**：分析 11.7 的密钥加载入口（可能新增了 store 初始化调用）。
  在此之前不要在批量产线上依赖自检的 PASS/FAIL 结论。

## 团队版运行原则

> 标题**不带版本号**：这节讲的是"团队版一直成立的原则"，不是某一版的临时事项。
> 写死版本号的结果是每发一版都得改标题，漏改时同事就会以为装错了版本
> （2026-09-20 用户报障：升到 P05 后标题还写着 P03）。
> 版本相关的内容放「P05 演示前稳定性要点」那节，标题本身保持中性。

- 剪映团队云空间只负责分发管理员筛选后的完整素材包；匹配与切分全部在本地完整素材库执行。
- 首次使用下载完整包，以后下载增量包；用 `scripts/material_manifest.py install` 校验并原子安装。
- 正式生成前优先打开工作台的「分镜预览与候选替换」，候选直接预览完整本地素材。
- 每次生成自动创建唯一任务 ID 和 `output/jobs/<任务ID>/receipt.json`，不得复用其他任务状态。
- 测试必须使用 `YUANLIU_RUN_MODE=test`；测试写生产 `output/` 或 `state/` 会被硬阻断。
- 草稿、状态、清单和素材使用次数必须经原子写入/文件锁组件更新，禁止自行直接覆盖共享 JSON。

### 管理员发布素材包

```bash
python scripts/material_manifest.py build "E:/源流素材发布包" --version 2026.09.16-1
```

将包含 `materials_manifest.json` 的目录上传剪映团队云空间。团队成员下载后安装：

```bash
python scripts/material_manifest.py install "D:/剪映下载/素材增量包" --target "E:/园区素材"
python scripts/material_manifest.py verify "E:/园区素材"
```

### 分镜计划

```bash
python scripts/storyboard.py create 脚本.txt --materials "E:/园区素材" \
  --park 嘉定 --orientation 竖屏 --out plan.json
python scripts/make_park_video.py 脚本.txt --materials "E:/园区素材" \
  --orientation 竖屏 --plan plan.json
```

预览阶段不累计素材使用次数；只有草稿成功落盘后才记录最终选中的素材。

## 前置条件

- Windows + 剪映专业版（安装目录**动态探测**：取含 `videoeditor.dll` 的最新版本目录。
  剪映会自动升级（实测 11.3.0.14362 → 11.4.0.14410），旧目录 dll 被改名成 `.dll_d_xxx`
  残留，硬编码版本路径会加解密失败。CLI `--install` 可省略；示例中的路径仅作参考）
- 剪映草稿根目录：`D:/JianyingPro Drafts/`
- Python 3.8+（草稿生成本身仅标准库 + ctypes，无第三方依赖）
- `videoeditor.dll` 允许导出（草稿加解密需要）

## 跨平台支持状态（2026-10-16 起，macOS 移植）

源流 skill 已做 macOS 移植改造。**核心结论：路径 / 环境适配层已全平台化，唯一未闭环的是
macOS 的草稿加解密层。** 代码通过 `scripts/platform_paths.py` 收敛所有平台相关路径，
其余模块只做薄封装，`Windows 分支返回值与历史硬编码逐字一致`（由 `tests/test_platform_paths.py` 锁死）。

### 各层状态

| 层 | Windows | macOS |
|---|---|---|
| 路径 / 环境抽象（`platform_paths.py`） | 不变 | ✅ `~/Movies/JianyingPro`、`.app/Contents`、`.dylib`、`~/Desktop`、`.command` |
| 剪映安装目录探测（`jianying_path`） | 不变 | ✅ 找 `libvideoeditor.dylib` |
| BGM 音乐缓存（`bgm_pool`） | 不变 | ✅ `~/Movies/JianyingPro/User Data/Cache/music` |
| 草稿加解密（`jy_crypto`） | ✅ `videoeditor.dll` 原实现逐字不变 | ⚠️ **默认明文直通 + 一次性警告**；加密版剪映可能打不开 |
| 打包器（`pack_release`） | 不变 | ✅ 桌面目录走抽象层 |
| 桌面安装器（`workbench/install.py`） | 不变 | ❌ 未实现（PowerShell/.lnk 专属），非 Windows 直接告知退出 |
| 工作台 GUI（tkinter） | ✅ | 🟡 理论跨平台，未实测（中文/字体需真机） |

### macOS 上怎么跑（当前可行路径）

1. **环境自检**：`python scripts/doctor.py`（已平台化，Mac 上不再误报 `videoeditor.dll` 缺失）。
2. **出片**：命令与 Windows 完全一致，`--materials` 指向 Mac 上的素材目录、`--out` 指向
   `~/Movies/JianyingPro Drafts` 或任意目录。
3. **关于加密**：Mac 版 `jy_crypto` 当前**不碰 Mach-O 加密库**（libc++ 的 `std::string`
   布局与 MSVC 不同，强行复用会崩溃/乱码）。它直接「明文写、明文读」：
   - 若你本机剪映草稿是**明文**（旧版或特定版本）→ 草稿可直接在剪映打开，**零问题**；
   - 若是**加密**版本 → 生成的草稿可能打不开，运行时会打印一次醒目警告。
4. **验证你本机剪映是明文还是加密**（在 Mac 上跑）：
   ```bash
   head -c 40 ~/Movies/JianyingPro/User\ Data/Projects/com.lveditor.draft/*/draft_content.json
   ```
   - 输出 `{` 开头 → 明文，**现在就能用**；
   - 输出乱码 → 需先在真机实现 Mac 加密层（见下）。

### macOS 彻底解法（TODO，需真机）

在装了剪映的 Mac 上：
1. 逆向 Mach-O 加解密库的真实符号名与字符串 ABI（Mach-O 同用 Itanium C++ mangling，
   符号名可能一致，但 `std::string` 内存布局需按 libc++ 重写 `MsvcString` 封送）；
2. 在 `jy_crypto._MacCrypto` 里接上真加密，失败再回退明文直通；
3. 跑 `tests/test_platform_paths.py` + 出片端到端回归。

### 不推荐的真机验证命令（确认有没有加密库可复用）

```bash
find /Applications -iname "*videoeditor*" -o -iname "*crypt*" 2>/dev/null
```

把上面两条命令（明文验证 + 库探测）的结果发回，即可判断能否闭环 Mac 加密层。

## 三种基础风格

| 风格 | 名称 | 特点 | TTS 默认 | BGM |
|---|---|---|---|---|
| 1 | 三连否/修辞排比 | 开场排比钩子，节奏激进，默认语速统一 1.09 | 激昂解说(男) | 大气震撼 史诗宣传 励志 |
| 2 | 招商/直接介绍 | 无钩子，直接"火热招商中"，语速统一 1.09 | 大促广告(女) | Why not have everything |
| 3 | 没想到/惊讶钩子 | "真没想到！"开场，语速统一 1.09 | 直播间小哥(男) | 新闻传播序曲 |

**TTS 音色策略**：默认从 `config/tone_pool.json` 候选池随机挑选；也可在对话中指定具体音色（如"用女声/用直播间小哥"）。

## 字幕时长与配音

### 默认：纯字幕模式（无配音）

字幕 + 画面 + BGM，**没有人声**。一键出片，打开剪映即可预览/导出，零人工步骤。
（2026-09-03 用户拍板："以后不需要你来配音"。）

### 可选：`--with-tts` 配音模式

生成占位配音轨 → 在剪映点「文本·朗读」批量配音（约 2 分钟，顺便检查断句）→
跑 `arrange_park_video.py` 一键闭合空隙。适合需要人声的成片。

**「一键闭合空隙」是什么**：用户在剪映里点「朗读」批量生成配音后，每段音频之间会留下空隙（字幕按估算时长排、配音是真实时长，两者不对齐，且剪映会把 17 条拆散到多条轨道）。阶段二 `arrange_park_video.py` 的「一键闭合空隙」就是把这些空隙全部消除——字幕和配音各归位成一条连续轨道、逐条对齐、删占位、重切画面、BGM 铺满。这一步是自动的，用户不用再手动拖。

**为什么改用剪映音色而不是 edge-tts**（2026-09-02 用户拍板原话）：
> 一键闭合空隙是指的我把你的文本配音朗读后每个音频中间会有空隙，所以一键闭合就会很简单。中间我点击朗读确实会浪费 1-2 分钟，但是对于这种成片我也可以做一次检查，并且一条视频的预计时长是 30 分钟，所以我觉得如果你的 edge-tts 音色单调并且我不好操控的话还是会浪费时间，这样最简单。

### 字幕停留时长（2026-09-03 校准，重要）

| 模式 | 每字时长 | 说明 |
|---|---|---|
| 纯字幕（默认） | **280 ms/字** | 用户 2026-09-03 拍板（对比 230 版后定）：宁长勿短，无配音时留足默读时间 |
| 配音模式 | 250 ms/字 ÷ 1.09 ≈ 229 ms/字 | 贴近真实配音时长 |

每条字幕最短 **1.2s**（原 0.9s 偏短，短句闪太快）。

⚠️ **校准史（勿回退）**：200（模板展示时长，偏短 30%）→ 280（一版修正）→
230（据配音 SRT 反推真实语速 211）→ **280（用户看片对比后最终定）**。
数字串按 0.5 权重计字的规则保留，与该值无关。

所有段时长都会再 `snap_to_frame()` 对齐 30fps 帧网格（33333.33us 的整数倍）。
不做对齐的话每段攒几毫秒误差，十几段后字幕与画面渐进错位。

## 使用流程

### 纯字幕模式（默认，零人工）

```
输入：脚本 + 风格 + 素材目录(可选)
  ↓
1. make_park_video.py 生成草稿（约 5 秒；字幕 + 画面 + BGM + 免责花字，无配音轨）
  ↓
2. 打开剪映 → 直接预览/导出
   （字幕停留已按朗读速度校准，观众默读节奏自然，无需任何人工步骤）
```

### 配音模式（`--with-tts`，需 2 分钟人工）

```
输入：脚本 + 风格 + 素材目录
  ↓
1. make_park_video.py --with-tts 生成草稿（字幕 + 占位配音轨 + 画面 + BGM + 免责花字）
  ↓
2. 【人工 2 分钟】打开剪映 → Ctrl+A 全选字幕 → 「文本·朗读」→ 选音色 → 开始朗读
   （顺便逐句检查断句，不满意可先改文字再朗读）
  ↓
3. 保存并【关闭】草稿
  ↓
4. 一键闭合空隙：
   python arrange_park_video.py "D:/JianyingPro Drafts/<草稿名>"
   自动完成：字幕/配音按真实时长归位（无空隙无重叠）→ 删占位 → 重切画面 → BGM 铺满
  ↓
5. 打开剪映看成品 → 导出
```

### 配音模式详情（阶段二展开）

**阶段一：生成草稿**（脚本 → 占位时间线）

```
输入：脚本文件 + 风格(1/2/3) + 素材目录 + 音色(可选/随机)
  ↓
1. 解析脚本 → 按风格模板切分为 N 条字幕（句号/叹号/问号断句，钩子+正文+CTA）
  ↓
2. 挑选 TTS 音色（随机或指定）→ 生成 TTS 素材参数
  ↓
3. 素材匹配：从素材目录按语义挑选横版素材（园区门口/交通/厂房/宿舍/办公...）
  ↓
4. 生成明文草稿 JSON（tracks/materials 完整结构，复制模板参数）
  ↓
5. jy_crypto.py 回加密 → 写入 D:/JianyingPro Drafts/<草稿名>/
```

**阶段二：一键闭合空隙**（人工点「朗读」→ 脚本自动整理）

```
6. 用户在剪映里打开草稿 → Ctrl+A 全选字幕 → 「文本 - 朗读」→ 选音色 → 开始朗读
   ⚠️ 剪映会按并行生成批次把字幕/配音拆散到多条轨道（实测 17 条 → 4 条字幕轨 + 3 条配音轨），
      且各段之间有空隙/重叠（字幕按估算时长排，配音是真实时长）
      但每段的 target_timerange.start 仍保留，顺序信息没丢
  ↓
7. 保存并【关闭】草稿（不关会有文件写冲突）
  ↓
8. python arrange_park_video.py <草稿目录>  自动完成：
     a. 按 start 还原脚本顺序，字幕 + 配音归位成各一条连续轨道，逐条对齐（闭合全部空隙）
     b. 删除阶段一的占位 TTS 素材（无声垃圾段）
     c. 总时长 = 全部真实配音时长之和（帧网格对齐后累加）
     d. 重跑语义匹配，按新时长重切画面铺满时间线（含容量适配调速）
     e. BGM 随机换曲并循环铺满新总时长
     f. 免责花字延长到新总时长 → 回加密写回
  ↓
输出：用户打开剪映 → 轨道已整齐，配音与字幕对位，画面铺满，BGM 切好 → 导出
```

## 工作台（GUI，团队剪辑师用，2026-09-07）

给不依赖 AI 对话/命令行的团队用。位置 `workbench/`（`workbench_gui.py` +
`启动工作台.bat`），双击 bat 启动（需系统 Python 带 tkinter，python.org 官方版自带）。

界面只收集参数并拼装成 `make_park_video.py` 命令行再调用（**不重复实现逻辑**），
生成后自动补跑 `verify_draft.py` 自检。参数与命令行逐字节一致。

v2（2026-09-07）：**全自动识别 + 轻盈科技蓝主题**，剪辑师只填 脚本/方向/剪辑者
- **风格自动判定**（不手动选）：开头「真没想到」→ 没想到；前段设问钩子
  （？/有没有/…吗）或排比连击 → 三连否；直入式开场 → 招商；识别依据显示界面
- **园区自动匹配 → 命名自动定**（不手动选 0/7式）：脚本前 200 字找「XX产业园」短语
  或开头已知园区词 → 城市类（嘉定/苏州/南京/贵阳/嘉兴/淮南）0 式；实拍园区
  （乌鲁木齐/新疆/两河 → 两河园区、北京 → 北京园区）7 式（必填剪辑者，缺则拦截）
- 识别为「两河园区」且素材库为空 → 自动填预设 `E:\园区素材\新疆园区\两河园区`
- 手动调整（折叠区：风格/命名/园区段）后该字段**锁定，不再被脚本改动覆盖**；
  「恢复自动」一键还原；草稿名实时预览行常驻；脚本改动 500ms 防抖自动重识别
- **批量模式（v3，2026-09-07）**：文本框一次贴多个脚本（脚本间空一行或一行 `---`），
  自动识别条数 N → 主按钮变「批量生成 N 条」→ 弹窗**逐条确认**：每条显示自动识别的
  园区/命名/风格 + 横屏/竖屏选择 +（7式行）剪辑者输入 → 全部开始生成（主线程预拼
  命令 → 后台串行逐条跑 + 自检 + 行内状态 PASS/FAIL + 汇总清单）
- 素材体检按钮：扫素材库显示各语义桶数量/横竖屏数/过短素材/版本号（拷没拷全一眼看）
- 高级设置：字幕上移 `--sub-y`、字号 `--sub-size`、BGM、复制素材、占位配音轨
- 配置持久化到 `config/gui_settings.json`，退出自动记忆
- 线程安全：后台任务（生成/体检/环境探测）一律走事件队列，主线程轮询更新 UI
- `启动工作台.bat` 自动检测 python/tkinter，缺 tkinter 给明确提示

**装到桌面（2026-09-09）**：安装 skill 后桌面自动多一个「源流工作台」图标
（品牌图 `workbench/源流.ico`）。三个自动入口，都幂等（已有快捷方式则跳过）：

1. 第一次打开 GUI：启动体检时检测桌面没有快捷方式 → 自动调 `install.bat` 装上
2. 一键升级：`upgrade.py` 升级成功后顺手补一次（换了机器/删过图标也能找回来）
3. 手动：双击 `workbench/install.bat`（或 `python workbench/install.py`）

实现：`install.py`（纯 ASCII 源码 + PowerShell `-EncodedCommand` base64 传参，
绕开 .bat/.ps1 的中文编码雷区）。创建后自动 `Unblock-File` 去掉 MOTW ——
否则 Windows 会把 .lnk 当"来自 Internet"拦掉（报"无法打开这些文件"）。
卸载 = 删桌面快捷方式 + `workbench/源流.ico`。

> CLI 小增强（配套）：0 式命名现在支持 `--park-name` 作为城市段
> （`YYMMDD-<park-name>-<风格名>`），不传则保持旧 `-园区-` 行为。

## 命令行用法（由 Agent 调用，用户不需要直接执行）

```bash
python 源流park-video-skill/scripts/make_park_video.py \
  "脚本.txt" \
  --style 3 \
  --materials "D:/园区素材" --park 苏州 \
  --name "260901-苏州-没想到" \
  --out "D:/JianyingPro Drafts" \
  --install "<剪映安装目录，可省略：留空即动态探测>" \
  --copy-materials
```

参数说明：
- 第一个位置参数 = 脚本文本文件（自动识别编码：utf-8-sig → utf-8 → gbk）
- `--style` 1三连否 / 2招商 / 3没想到（默认 3，也接受中文名）
- `--materials` 素材目录：集中根目录（`_shared`+`_parks`，配合 `--park`）或普通素材文件夹。
  缺省则时间线只有字幕+BGM，素材后续手动补
- `--park` 园区名：从 `--materials` 根目录的 `_parks/<名>` 取专属素材，缺失自动回退 `_shared` 通用素材
- `--naming {0,7}` 草稿命名方式（**2026-09-04 用户定义，默认 0**）：
  - **0式**（嘉兴/嘉定/苏州/南京/贵阳等城市脚本）：`YYMMDD-城市-风格名`（如 `260901-南京-没想到`）
  - **7式**（新疆/北京等实拍园区）：`YYMMDD-园区-横|竖-剪辑者`（如 `260904-两河园区-竖-铠畅`）。
    需配合 `--editor 剪辑者` 与 `--orientation 横/竖`；**跑新疆/北京前必须先问剪辑者姓名**
- `--editor` 剪辑者姓名（7式命名用；若剪辑者让存记忆，下次见到先问候：
  "XX你好，很高兴再次认识你，我等你很久了"）
- `--park-name` 园区显示名（7式自动名用；缺省取 `--park` 或素材目录末段，如 两河园区）
  （支持 `_manifest.json` 里的 aliases 友好名，如 `--park 上海嘉定`）
- `--tone` 指定音色（中文名或 tone_speaker，缺省按风格从 tone_pool 随机；纯字幕模式下不生效）
- `--with-tts` 生成占位配音轨（供点「朗读」+ 阶段二归位）。**默认不生成** = 纯字幕视频
- `--name` 草稿名（缺省自动生成 `YYMMDD-园区-风格名`）
- `--out` 草稿根目录（默认 `D:/JianyingPro Drafts`；生成子目录 `<out>/<草稿名>/`）
- `--install` 剪映安装目录（缺省自动探测 D:/JianyingPro 等常见路径）
- `--copy-materials` 把素材复制进草稿 `materials/`，生成自包含草稿。
  默认**不复制**：草稿引用素材库原路径（快、省 355MB+；素材库集中管理不乱动时推荐）
- `--orientation 竖屏|竖版|横屏|横版` 成片方向（横竖屏隔离过滤，见素材规则节）
- `--sub-y 0.06` 字幕位置**全局统一偏移**（画布归一化，正=上移；0=模板原位置）。
  2026-09-04 用户需求：整体平移避手机 UI/头像遮挡，一次到位，不必剪映逐条拖
- `--sub-size 10` 覆盖字幕字号（素材层 int 化，请传整数；默认模板值如 8.0）
- `--bgm` BGM 模式：`random`（默认）从本机剪映音乐池随机选曲——扫描草稿库收集曾用过且
  `Cache/music/` 缓存仍在的音乐（约 14 首），无需任何云端鉴权；`fixed` 用风格默认曲。
  曲子比视频短时自动循环铺满时间线
- `--bgm-seed` 随机选曲种子，传入同一数字可复现同一首曲子

输出：`<草稿名>/draft_content.json`（加密）+ `draft_meta_info.json`（加密）+ `timeline_layout.json`（明文）+ `textReading/`（空目录，用户在剪映点「朗读」后生成配音 wav）+ `materials/`（加 `--copy-materials` 时）。

### 局部替换素材（换第 N 段画面，2026-09-04）

用户精调时最痛的两件事之一：某段画面不满意。**不用翻素材手动拖**，
告诉我"第几段画面换掉"，脚本局部替换，其他段落/字幕/配音/BGM 一律不动：

```bash
python 源流park-video-skill/scripts/replace_seg.py \
  "D:/JianyingPro Drafts/<草稿目录>" <段号 1-based> \
  --materials "E:/园区素材/新疆园区/两河园区" \
  --orientation 竖屏 [--seed N]
```

- 语义自动判定：取该段覆盖的第一条字幕 → classify_subtitle → 同 bucket 候选
- 同方向过滤（与 --orientation 一致）；候选随机抽一个能撑住段长的（fit 0.5~2x）
- 只改：该段 material_id / source_timerange / speed / clip.scale（新素材真实时长重算）
- 自动备份 `draft_content.json.pre-replace.bak`；改完回加密
- ⚠️ 只在**脚本自己生成的、未在剪映编辑过**的草稿上替换；用户在剪映里手动
  配音/改过的草稿结构不同，替换前会先核对字幕结构，异常即中止
- 若该语义只剩当前素材 → 报错，建议整稿重抽

### 阶段二：配音后归位（仅 jianying 模式需要）

```bash
python 源流park-video-skill/scripts/arrange_park_video.py \
  "D:/JianyingPro Drafts/260901-苏州-没想到" \
  --materials "D:/素材/园区横版/" \
  --bgm random \
  --install "<剪映安装目录，可省略：留空即动态探测>"
```

参数说明：
- 第一个位置参数 = 草稿目录（必须是已配音、已保存、**已关闭**的草稿）
- `--materials` 素材目录（缺省用草稿内 `materials/`）
- `--bgm` `random`（默认）随机换曲 / `keep` 保留现有 BGM
- `--bgm-seed` 随机选曲种子
- `--fps` 时间轴帧率，段时长对齐到整数帧（默认 30，与剪映一致）
- `--install` 剪映安装目录
- `--dry-run` 只预览不写回

行为细节：
- 运行前自动备份 `draft_content.json` 为 `draft_content.json.pre-arrange.bak`
- 写回后自动解密回读自检：单轨连续、无重叠、不超总时长、无悬空引用、占位素材清零、视频铺满
- **幂等**：对已归位的草稿再跑一次结果不变（可安全重跑）
- 若某条字幕没有配音会直接报错并列出文本，需要回剪映补朗读后重跑

## 素材命名规范

格式：**`<语义>_<横版|竖版>.mp4`**

语义词（用这些词，识别最准）：

| 语义词 | 含义 | 例 |
|---|---|---|
| `全景` | 航拍/俯拍/鸟瞰全景 | `全景_横版.mp4` |
| `大门` | 园区门口/入口/门头 | `大门_横版.mp4` |
| `交通` | 道路/路口/主干道/高速/公路/高架/收费站/匝道 | `交通_横版.mp4` |
| `地铁` | 地铁站/地铁口 | `地铁_横版.mp4` |
| `高铁站` | 高铁/火车站 | `高铁站_横版.mp4` |
| `厂房` | 厂房外景/厂区 | `厂房_横版.mp4` |
| `厂房内部` | 车间/生产线/层高/户型 | `厂房内部_横版.mp4` |
| `宿舍` | 宿舍/公寓/住宿 | `宿舍_横版.mp4` |
| `办公` | 办公楼/写字楼 | `办公_横版.mp4` |
| `食堂` | 食堂/餐厅 | `食堂_横版.mp4` |
| `配套` | 环境/绿化/广场 | `配套_横版.mp4` |

横版优先（竖版会降权，因为画布是 9:16 需要放大裁切）。
旧命名（`3栋厂房-横版.mp4`、`一楼厂房-横版.mp4`）仍然兼容，会按关键词归入对应语义。

## 目录语义素材库（DJI 原片，2026-09-04 落地）

用户实拍素材语义写在**文件夹名**上，文件名是无意义的 DJI 时间戳：

```
E:/园区素材/新疆园区/两河园区/        ← --materials 直接指向园区文件夹
├── 停车场/                          DJI_*.MP4（无横竖层）
├── 大门口/  高速/  宿舍/             （无横竖层）
├── 食堂货梯卸货/
│   ├── 横屏/                        ← 可选 横屏/竖屏 分层，仅辅助横竖识别
│   └── 竖屏/
├── 厂房外部/{横屏,竖屏}/
└── 厂房内部/{横屏,竖屏}/
```

**规则**：
- `--materials` 指向**园区文件夹**（普通目录模式，递归扫描，不带 `--park`）
- 语义判定**只用目录名**（2026-09-14 用户拍板）：从叶子目录向上命中
  （`厂房内部/竖屏/xx.mp4` → inside；`厂房外部/横屏/xx.mp4` → factory）。
  **文件名不再参与语义判定**——文件名兜底会制造"看起来对、其实错"的归类：
  实测 118 条被判成「航拍」的素材里 103 条是文件名 `DJI_*` 判的，北京那 92 条
  其实全躺在 `医械城/专业设施/` 目录里。真航拍只认 全景/俯拍/航拍/鸟瞰
- 目录没写语义 → 进**未分类池**（哪里缺补哪里），不再猜
- 以「园区 / 产业园」结尾的目录层级（`新疆园区`、`两河园区`）**跳过不判**，
  否则里面的"园区"二字会命中 environment 关键词
- 文件夹名 ↔ 语义桶自动对应：停车场→配套(environment)、大门口/门口→entrance、
  高速/公路→traffic（2026-09-14 合并，用户：**高速与公路是一个意思**，原 highway 桶已取消）、
  宿舍→dormitory、食堂货梯卸货→canteen、厂房内部→inside、厂房外部→factory
- **bucket 内随机抽取**（用户拍板 2026-09-04）：不再按"横版优先/名字长加分"排序，
  每组 shuffle 一次后轮转取用 = 每次成片画面组合不同，又不邻近重复。
  横屏/竖屏都进同一随机池，9:16 画布按 scale 放大裁切适配
- 548 素材扫描 + 时长探测 ≈5s（2026-09-04 优化：纯 mp4 box 解析优先，ffmpeg 兜底）

### 素材切换与语义映射规则（用户拍板 2026-09-04）

字幕→画面语义 class（`classify_subtitle`，按优先级）：

| class | 判定 | 画面 |
|---|---|---|
| cta | 触发词 `CTA_TRIGGERS`（现为：点击下方链接、现在入驻；**用户模板更新中，之后随时补**） | 厂房外部 factory，**触发后传播到片尾** |
| param | 含数字+参数词（层高/承重/吨/米/平米/户型…），或纯数字句 | 厂房内部 inside |
| kw | 命中语义词（宿舍/大门/高速/食堂…） | 对应 bucket |
| price | 花销政策词（租金/免租/便宜/免费/补贴…） | 厂房外部 factory |
| desc | 无数字的描述句（水电齐全/供暖/不停歇） | 厂房内部 inside |

注意：param 在 kw **之前**判定（"厂房层高4.5米"先命中参数→inside，不被"厂房"抢到 factory）。
param 与 desc 同为 inside 但**必须换素材**（用户："层高承重是一个素材，水电供暖是另一个"）。

切换时机（`match_materials`）：
1. 语义 class 变化（kind 或 bucket 任一变化）
2. **！句的下一句**强制切换（情绪高点换画面；用户明确"！句的下一句换"，非 ！句本身）
3. CTA 触发词出现 → factory 并传播到结尾
4. 素材容量不足（0.5x 放慢仍撑不满当前语义段）→ **换同语义素材**（用户拍板，不再硬撑）
5. 每段 6s 是软目标：**语义优先顺延**（用户拍板），语义没结束不硬切，等语义切换点才换
6. 事实效果：同脚本素材段从 4 段（旧规则）→ 9 段（新规则），画面密度翻倍

已废弃：CTA 段结尾定格（用户后撤回："定格这个功能不要了"）。

### 语义判定优先级：居住实体词先于价格词（2026-09-16 用户拍板，方案 A）

**当前引擎是 `tag_subtitle`**（上面 `classify_subtitle` 那张表是旧引擎的描述，仅作历史参考）。
真实判定顺序**写死在 `scripts/material_matcher.py`，配置改不动**：

```
cta > policy > residence > price > location > interior_fac(严格) > support
    > interior_fac > interior_area > interior_struct > exterior
```

`residence`（居住实体词）= `config/semantics.json → keywords.residence`
= 宿舍 / 公寓 / 人才公寓 / 住宿。

**为什么加它**：新仓脚本「精装人才公寓月租**500元**起」里的"500元"会命中价格词「元」，
而 price 排在 support 前面 → 整句被判成**价格块**（桶 = `厂房外部 → 厂房内部`），
画面配到厂房素材、**永远不查宿舍桶**。居住实体词才是画面语义的主体，价格只是修饰，故优先。

- 只影响"含居住实体词**又**带金额"的句子。`这样的厂房租金仅需3毛起`、`租金低至18元/㎡起！`、
  `镇区房价仅5000-6000元/㎡` 等真价格句**不受影响**（不含居住实体词，仍走价格块/厂房素材）。
- 老配置缺 `keywords.residence` 时给空表 = 退回旧行为，不拦停出片（兼容队友的旧包）。
- 回归：`python tests/test_residence_priority.py`（6 项，含"清空 residence 应退回旧行为"的反证）。

### 图片也参与选片（2026-09-15 用户拍板，原"图片不参与选片"作废）

**图片（jpg/jpeg/png/webp/bmp/jfif）与视频同池竞争同一个语义桶，不再被排除。**

- 扫描：`material_matcher._scan_one_dir` 收图片，item 带 `kind: 'image' | 'video'`
- 草稿：图片素材写成剪映的 **`type: 'photo'`**（不是 video），时长按 **1 倍速铺满**
  （图片无固有时长 → `fit_clip_speed(0, need)` 走"时长未知按 1 倍速"分支）
- **可用性下限**（防止放大糊 / 缩放崩，2026-09-15 抽样定）：
  `MIN_IMAGE_SIDE=540`（短边）、`MAX_IMAGE_SIDE=12000`、`MAX_IMAGE_RATIO=4.0`；
  探测不到尺寸的图片**不进池**。实测全库 445 张图 → 入库 196 张
- **尺寸探测有两处坑**（都已在代码里处理）：
  1. 用户库里多数"jpg"前面带 ~4KB 非标准头（SOI 不在偏移 0），
     `_probe_image` 改为在头 1MB 内找签名；失败再走 `_ffmpeg_image_size` 兜底
  2. 118 张只能走 ffmpeg 兜底（250ms/张 ≈ 29s）→ 尺寸结果落**磁盘缓存**
     `output/media_size_cache.json`（键 = 路径|mtime|size）。首次扫描 27s，之后 0.2s
- 实际影响：苏州园区原来"全景目录里只有图，航拍素材 0 条"，现在有 23 张可用；
  北京/贵阳等园区同样补上了航拍与厂房内部
- 出片日志会明示：`[xx.jpg] 图片素材，按 1 倍速铺满 5.6s`

### 图片的段落级使用约束（2026-09-15 用户拍板）

两条硬规则，在 `match_materials` 选片时按**语义块**（不是单条字幕）生效：

1. **段落总时长 > 5s → 优先视频素材**（`LONG_SEG_US = 5_000_000`）：
   静态图铺 5 秒以上太闷，长段一律先找视频；池里确实没有视频才退回图片。
2. **同一段落最多用 2 个图片素材**（`MAX_IMG_PER_BLOCK = 2`）：
   图片是静帧，一段里堆多了像 PPT 翻页。

实现要点：每个语义块开头重置 `block_prefers_video` / `block_img_left`；
候选过滤 `_cand_ok()` 在「该段优先视频」或「图片额度用尽」时跳过图片，
命中图片则额度 −1；全落空的兜底**也优先挑视频**（无视频才退回任意素材）。
向后兼容：不传 `sub_durations`（旧调用路径）不启用"优先视频"，图片照常可用、上限仍 2。
回归：`tests/test_image_constraint.py`（4 项，monkeypatch 扫描层跑真实选片路径）。

### 横竖屏隔离（用户拍板 2026-09-04；整桶回退 2026-09-15）

**竖屏/竖版 标注的素材只能剪竖版视频；横屏/横版 只能剪横版；无标注的通用。**
- 每次剪辑**用户会提前告知成片方向**，CLI 传 `--orientation 竖屏|竖版|横屏|横版`
  （内部归一化 portrait/landscape）；不传则不过滤（兼容旧行为）
- 过滤在分配期执行（`match_materials(video_orientation=...)`）：方向不符的素材
  直接出局，即使语义再贴也不用
- **方案 B（2026-09-15 用户拍板）：整桶同方向素材为 0 → 回退用另一方向。**
  某语义桶在本园区**一条同方向素材都没有**时，隔离的代价是该块彻底换不了画面
  （实测嘉定竖版：60 条过滤后只剩 3 条门口，画面长时间不换、还要外借）。
  此时该桶退回**另一方向**的素材；**同方向有素材的桶行为完全不变**。
  裁切效果由用户在剪映自行判断。跨园区借来的桶同样适用（借来若方向全不符 →
  整池回退，不白借）。
  日志：`[方向回退] 桶 xxx：本园区该桶无 portrait 素材，回退用另一方向 N 条`
  回归：`tests/test_orient_fallback.py`
- 实测两河园区库：竖屏 548→292（剔除横屏 256）、横屏 548→302（剔除竖屏 246），
  宿舍等无标注文件夹两个方向都可用
- 实测嘉定园区竖版：回退前只剩 3 条（画面 3 段、全程借嘉兴）；回退后用回自己的
  厂房内部 25 + 厂房外部 32（画面 5 段，不再外借）
- 待办：用户模板更新中，CTA 触发词清单等模板定稿后补

## 园区优先选片（2026-09-11 用户拍板，硬规则）

**先锁定园区，再选素材。识别不到园区就拦停，绝不允许"多园区大混池"。**

来源：`260911-贵阳-横-招商` 事故——`--materials` 传了素材总根，5 个园区 900 条
素材混成一个池，贵阳只占 41 条（4.6%），7 段画面**全部**被新疆/北京/嘉兴抽走，
其中 4 段是乌鲁木齐宿舍。随机抽 7 段全落空的概率约 72%，属必然结果。

规则：

1. **园区识别优先级**（2026-09-14 修正）：
   `--park` > **界面/命令行填的园区名（`--park-name`）** > 脚本文本里的园区地名 > 素材目录名。
   - `--park-name` 过去只用于草稿命名、**不参与选片**：界面填"新仓"、脚本里"嘉兴平湖"
     的"嘉兴"出现更早 → 锁成嘉兴园区，用户以为按新仓出片，素材却全是嘉兴的
     （2026-09-14 新仓事故）。现在它参与锁定，与脚本推断不一致时会打印 `[!] 注意` 告警。
   - 脚本多个地名词命中时，**更具体的词（更长）优先**：`沈海高速新仓出口` 里
     "新仓出口"(4字) 赢过 "嘉兴"(2字)；同长度再看表格「命中优先级」列，最后才看出现位置。
   - **脚本地名只认表里 C 列的识别关键词**；显示名只用于 `--park` 匹配与草稿命名
     （2026-09-14 修正：此前显示名也参与脚本匹配，`新仓` 会命中"全**新仓**库"）。
   - 当前 9 个园区：北京 / 嘉兴 / 新疆（两河）/ 苏州 / 贵阳 / 南京 / 嘉定 / 德州 / 新仓。
   - 回归：`python tests/test_park_locking.py`（13 条用例，含界面园区名优先、长词优先）。
2. **只在本园区文件夹里随机选素材**（`scan_materials` 只扫锁定后的园区目录）。
3. **跨园区借用必须查表**：只有标记"可跨园区通用"的语义桶才许借，
   且要遵守该桶的"通用例外"（点名排除的园区不借）。
   当前默认：`inside`（厂房内部）通用；`dormitory`（宿舍）通用**但排除新疆园区**；
   `factory` 等园区门面桶一律不借（`NO_CROSS_BUCKETS` 硬红线，规则也改不动）。
   借用时**先从"邻近园区"取**（2026-09-14 用户拍板，表里人工维护，如
   嘉定↔苏州/嘉兴/新仓、德州↔北京/南京）——不放开新桶，只降低穿帮概率，
   取完邻园再取远处的。
4. **识别不到园区 → 抛错拦停**（`match_materials` 抛 `ValueError`，
   `make_park_video` 直接 `SystemExit`），并提示三种解决方式；
   阶段二 `arrange_park_video` 改为沿用草稿里既有素材所属园区，锁不到就保留原画面并告警。

### 语义规则分配表 / 使用手册（规则的人工编辑入口）

表格第 4 个 sheet「**使用手册**」写了全部使用方式与规则（改规则、加园区、
列含义、当前默认、命令速查、变更记录）。代码这边只记要点：

```bash
# 1) 生成/刷新表格（含各园区素材覆盖矩阵 + 使用手册页）
python scripts/build_rules_xlsx.py                 # 默认：D列有未导入的改动会拦停
python scripts/build_rules_xlsx.py --config-wins   # 以配置为准刷新 D 列（AI 改完配置后用）

# 2) 在 Excel 里改：
#    语义规则分配表 G列(可跨园区通用) / H列(通用例外) / D列(素材侧关键词) / J列(备注)
#    园区识别词表   C列(脚本识别关键词) / E列(邻近园区)

# 3) 写回配置并生效（有校验，校验不过不落盘）
python scripts/import_rules.py                     # 园区识别词/邻近/桶通用性 → park_rules.json
python scripts/import_rules.py --keywords-from-table   # 另把 D 列 → semantics.json（会回显 diff）
```

**D 列（素材侧关键词）是双向编辑的，带冲突拦停**（2026-09-14）：

- D 列与 `config/semantics.json` 表达同一件事，必须明确谁说了算。
  **默认以配置为准**；要按表生效必须显式加 `--keywords-from-table`。
  理由：否则"AI 改完配置 → 有人拿旧表导入"会把配置改动悄悄覆盖回去。
- 生成表格时若发现两者不一致 → **停下报错**，并给出两条路
  （先导入 / 加 `--config-wins` 以配置为准），不会静默覆盖。
- 导入时会**回显 diff**（哪个桶加了/删了哪些词）+ 原文件备份 `semantics.json.bak-kw`，
  并提示跑回归。关键词写错不会报错、只会让素材悄悄落进兜底池，所以 diff 必须人工核对。

其他防呆：
- **重跑表格不吃标注**：G/H/J 列、识别词表的 显示名/关键词/邻近/备注 都会合并回来
- **表格被 Excel 占用**（读不出）→ 拒绝写盘，防止覆盖标注
- **园区目录不存在 → 拦停**：脚本写了已注册但目录还没建的园区，`resolve_park_dir`
  返回失败而不是回退去扫素材总根（否则又走回"多园区混池"的老路）
- 校验：园区文件夹必须真实存在、通用例外/邻近园区名必须在园区表里、邻近不能写自己

新增园区：建目录 → 重跑 `build_rules_xlsx.py`（自动补一行）→ 填 C 列识别关键词、
E 列邻近园区 → `import_rules.py` 生效。


### 素材来源体检（怀疑画面串园区时用）

成片打开发现画面不对（出现别园区的地标/宿舍），按这个顺序查，别猜：

```bash
# 1) 看产出台账：materials_dir 字段 = 实际使用的素材目录
#    （2026-09-11 起记的是"锁定后的园区目录"，不再是命令行原样传入值）
python scripts/production_log.py --summary

# 2) 解密草稿时间线，把每段素材按园区归类
#    P05 §7 起第 4 个参数（安装目录）可以省略 —— 脚本会动态探测
#    含 videoeditor.dll 的最新版本目录，不再需要写死盘符与版本号。
python scripts/jy_crypto.py -d "<草稿>/draft_content.json" out.json
```

再从 out.json 里正则抓 `E:/园区素材/<园区>/...` 路径做统计即可。
判断标准：正常成片应**全部**来自锁定园区，只允许规则开放的桶（inside/dormitory）
出现兄弟园区素材。

### 素材来源审计（每次出片自动跑，2026-09-11 起）

`tests/verify_draft.py` 现在会自动多输出一段：

```
素材来源审计：
  预期园区：贵阳园区（依据 脚本地名）
  素材来源：贵阳园区 7 段 / 嘉兴园区 1 段　合规借用：嘉兴园区 dormitory 1 段
   OK   [贵阳园区|aerial] 视频_pgc_...航拍.MP4
   OK   [嘉兴园区|dormitory] 小宿舍走廊-横版.mp4
```

- 预期园区读草稿目录里的 `park_lock.json`（出片时写入：锁定园区、依据、规则版本、
  当时开放的通用桶）；老草稿没有该文件时回退查产出台账；两者都没有则**跳过并告警**
  （不给假 PASS）。
- 判定：本园区素材 → OK；兄弟园区的桶在规则里开放了通用、且**来源园区**不在该桶
  的通用例外里 → OK（记为"合规借用"）；其余 → FAIL 并指出越界的园区与桶。
  注意"通用例外"写的是**来源**园区（"不从新疆借宿舍"），不是"本园区不能用宿舍"。
- 成本：只读草稿 JSON、不解码视频，≈0.1 秒。
- 为什么不用 ffmpeg 信号层 QA（黑场/冻结/静音/削波）替代它：实测对贵阳那条片子
  跑一遍要 **43 秒**（出片本身才 10~15 秒），结果全 PASS —— 那条片子画面全是干净
  素材，错的只是**来源**。信号层查不出"配错"，来源层能。

## 集中素材根目录（多园区 + 通用回退）

**2026-09-03 落地**：按你「不同园区建文件夹、通用素材共享、缺失自动通用」的需求，
采用**集中本地根目录 + 一份清单**，不依赖剪映云（剪映云是私有账号格式、无 API、
本地缓存还是哈希文件名，无法按语义自动归类，长期不稳）。

目录结构：

```
D:/园区素材/                      ← 集中根目录（--materials 指向这里）
├── _manifest.json               ← 园区别名与通用 bucket 声明（可选但推荐）
├── _shared/                    ← 通用素材，所有园区可用，只存一份（交通/厂房内部/航拍…）
│   ├── 交通_横版.mp4
│   └── 厂房内部_横版.mp4
└── _parks/
    ├── 嘉定/                    ← 嘉定专属素材
    │   ├── 嘉定大门_横版.mp4
    │   └── 嘉定厂房外部_横版.mp4
    └── 乌鲁木齐/
        └── 乌市厂房外部_横版.mp4
```

**规则（脚本自动执行，无需手动维护）**：
- `--materials D:/园区素材 --park 嘉定` → 只扫 `_shared/` + `_parks/嘉定/`，
  **绝不会**混入乌鲁木齐等其他园区的专属素材（跨园区隔离）。
- 嘉定缺失的语义 bucket（如交通、厂房内部）**自动回退到 `_shared` 通用素材**；
  若 `_shared` 也没有，则回退到未分类素材 / 当前素材（沿用旧兜底逻辑）。
- `_manifest.json` 用 `aliases` 把友好名映射到文件夹名，例如 `"嘉定": {"aliases": ["上海嘉定"]}`，
  这样 `--park 上海嘉定` 也能正确命中 `_parks/嘉定/`。

**初始化与体检**（直接跑 `material_matcher.py`，无需写代码）：
```bash
# 建好骨架（_shared/_parks + 空 _manifest.json）
python 源流park-video-skill/scripts/material_matcher.py --init D:/园区素材

# 体检：某园区每个语义 bucket 是「专属✅ / 通用🔄 / 缺失❌」
python 源流park-video-skill/scripts/material_matcher.py --report --root D:/园区素材 --park 嘉定
```

**注意**：
- 通用素材放在 `_shared/` 只存一份，避免各园区各拷一份导致更新错位。
- 想加新园区：在 `_parks/` 下建同名文件夹丢素材即可，无需改代码。
- 单目录旧用法仍兼容：`--materials D:/某素材文件夹`（不带 `--park` 时按普通目录扫描）。

## 素材容量适配（核心稳定性设计）

素材时长和需要展示的时长很少刚好对上，靠**播放速度**消化，绝不出现定格画面：

| 情况 | 处理 | 例 |
|---|---|---|
| 素材短（容量不足） | **放慢播放**，0.5x~1.0x | 12.3s 素材撑 16.9s → 0.72x |
| 素材长，2 倍速能放下 | **完整播完**，不截断 | 12.3s 素材撑 11.0s → 1.11x |
| 素材长，2 倍速也放不下 | **2 倍速 + 截取前段** | 35.5s 素材只需 15.1s → 2.0x 取前 30.3s |
| 素材严重不足（<0.5x） | 分配阶段提前**换素材**，不硬撑 | — |

速度边界：`MIN_SLOW=0.5`（再慢会拖沓）、`MAX_FAST=2.0`（再快画面会跳）。
分配时还带容量约束：一个素材最多承担 `素材时长 / 0.5` 的展示时长，超了就换下一个。

`--materials` 目录里素材总时长建议 ≥ 视频时长 × 1.5，留足冗余。

## 语义规则配置（人人可改，改完必跑回归）

2026-09-09 落地（P0）。语义关键词原先硬编码在 `scripts/material_matcher.py`，
改一个词要改代码、再重发 15 份。现在外置到 **`config/semantics.json`**。

**能改什么**

| 字段 | 作用 |
|---|---|
| `keywords.*` | 各语义的关键词（cta / policy / price / location / support / interior_* / exterior / park） |
| `block_buckets` | 块类型 → 素材桶候选（按优先级，第一个没素材就往下退） |
| `traffic_bucket_hints` | 交通词 → 桶（"轻轨"→metro，避免文案讲轻轨、画面给高速） |
| `no_cross_buckets` | 禁止跨园区借用的桶（厂房外部是园区门面，混用一眼看出来） |
| `semantic_buckets` | 素材目录名/文件名 → 桶 |
| `thresholds` | 短外部句合并阈值 |

**不能改什么**：判定优先级与分块算法在 `material_matcher.py` 里 —— 那是引擎，不是配置。

**改完必做（两步，少一步都危险）**

```bash
python scripts/semantics_config.py --check    # 1 结构自检：拼错桶名/写空词组/漏桶 都会报
python tests/test_semantic_blocks.py          # 2 回归：5 条定稿脚本块数 12/10/9/10/8
```

> ⚠️ **为什么必须跑回归**：配置写错不会让程序崩溃，只会让**画面配错**
> （"水电蒸汽"配成宿舍素材），成片导出后才发现，比崩溃难查得多。
> 自检只管"结构对不对"，回归才管"语义对不对"，两个都要跑。
>
> ⚠️ **回归没报警 ≠ 改动没生效**：关键词冗余度高（"办公楼/食堂/宿舍/生活"
> 互相兜底），删掉单个词常常不改变判定结果。想确认改动生效，先跑
> `python tests/test_semantic_blocks.py --print` 看实际序列。

**配置损坏时**：`semantics_config.py` 报错并**停止出片**，不用默认值悄悄兜底 ——
悄悄用旧规则出片，比停下来更难查。

## 产出台账（P0）

每次生成草稿自动往 `output/production_log.csv` 追加一行（UTF-8 带 BOM，
Excel 双击打开中文不乱码）：

时间 / 剪辑者 / 机器 / 园区 / 风格 / 草稿名 / 时长 / **素材库版本** /
素材路径 / 方向 / 字幕数 / 素材数 / BGM / **自检结果** / skill 版本

```bash
python scripts/production_log.py --summary                # 汇总：PASS/FAIL 数、按人/园区/风格分布
python scripts/production_log.py --summary --fail-only     # 只列问题条目 → 秒定位"谁的哪一条"
python scripts/production_log.py --summary --last 20       # 最近 20 条
python scripts/production_log.py --merge D:\台账汇总        # 合并 15 人各交上来的台账
python scripts/production_log.py --version-of E:\园区素材\嘉兴园区  # 查素材库指纹
```

两个容易被忽略的价值：

- **素材库版本**（`v<文件数>-<hash8>`）：同一脚本上周正常、这周画面不对，
  先对比这个字段 —— 变了就说明素材被换过，不用去猜代码。
- **skill 版本**（`s-xxxx`）：是**代码指纹**不是人工版本号，谁还没升级一眼看出
  （人工维护的版本号最容易忘改，而"谁没升级"恰恰最需要台账来发现）。

> 台账写入失败**不会**中断出片（`append_record` 内部吞掉异常），
> 自检失败也只记账不回滚 —— 草稿已经写好，要不要重出由人判断。

## 素材治理（P1-①，2026-09-09）

15 人批量出片，素材会被用滥、会被换 —— 三个工具各治一个：

**1) 使用频次记录**：每次出片自动记下用了哪些素材 → `output/material_usage.json`。

```bash
python scripts/material_governance.py --report E:\园区素材\嘉兴园区   # 体检：素材数/指纹/分桶/使用分布
python scripts/material_governance.py --top 20                        # 用得最多的素材全局榜
python scripts/material_governance.py --reset-usage                   # 换素材库后清空频次
```

**2) 大库降权**：高频素材降低被选中概率，让没露过面的素材先上。
**⚠️ 按池（bucket）判断，不是按整个库** —— 池内素材 ≥ 30 个才降权。
为什么：按库判断要么永不生效（嘉兴 123 条但每池只有十几个）、要么误伤
只有几个素材的小池（贵阳 41 条）。当前只有新疆园区的 factory(332)/
inside(128)/canteen(41) 三个池会降权。seed 复现模式不受影响（usage 是
确定输入）。素材轮转数据：实测降权把第二条片的素材复用率从 100% 压到 20%。

**3) 素材库基准版本**：素材库每人本地一份、没有中心可比对。负责人导出一份
基准指纹，其他人本地比对 —— 不一致说明素材被换过/拷漏，**出片前**就知道，
而不是等画面不对才发现。

```bash
python scripts/material_governance.py --set-baseline E:\园区素材\嘉兴园区   # 负责人设定基准
python scripts/material_governance.py --check E:\园区素材\嘉兴园区          # 本地比对（OK/DRIFT）
python scripts/doctor.py                                          # 环境自检里含基准项
```

出片时若素材与基准不一致，主流程会打醒目提示（不拦出片）。

## 环境自检 doctor（P1-②，2026-09-09）

15 人环境各异（盘符/剪映版本/Python），一人一坑。开跑前先体检：

```bash
python scripts/doctor.py            # 全量检查 + 修复建议
python scripts/doctor.py --quiet    # 只看 WARN/FAIL
python scripts/doctor.py --json     # 机器可读
```

检查项：Python 版本 / tkinter（只影响工作台 GUI，命令行出片不受影响，故只算
WARN）/ 剪映安装目录 / 草稿根目录可写 / 素材库存在且非空（带指纹）/ 语义配置
校验 / 核心模块能否 import / 素材库与基准比对 / output 可写。
退出码：0 全 PASS / 1 有 FAIL / 2 只有 WARN。

**首启向导**：工作台启动时若剪辑者姓名没填过会弹窗引导填写（姓名进草稿名和
台账）；同时后台自动跑 doctor，FAIL/WARN 显示在日志区，不弹阻塞窗。

## 一键升级（P1-③，2026-09-09）

改一条规则/修一个 bug，不用再重发 15 份 zip。负责人打包一份「新版目录」，
15 人各跑一条命令：

```bash
python scripts/upgrade.py --from D:\pack\park-video-v3.6     # 目录或 zip 都行
python scripts/upgrade.py --from D:\pack\park-video-v3.6 --dry-run   # 先看计划
python scripts/upgrade.py --rollback                          # 回滚到最近一次备份
```

安全保证（**本地资产绝不覆盖**）：

| 资产 | 处理 |
|---|---|
| output/production_log.csv、material_usage.json | **保留**（产出台账是排查凭证） |
| config/gui_settings.json、materials_baseline.json | **保留**（个人设置） |
| config/semantics.json | 新旧不同 → 本地改动先备份成 `.pre-upgrade-<时间戳>` 再覆盖；新版没动它则保留本地 |
| scripts/tests/templates/SKILL.md | 覆盖为官方新版 |

升级流程：**备份 → 比对 → 复制 → 自检（编译 + 语义配置 + 回归）→ 任一失败
自动回滚**。出问题也不至于把能用的旧版弄坏。

## 打包发版（2026-09-20，P05 §8）

```bash
python scripts/pack_release.py                        # 版本号读 release_manifest.json，不硬编码
python scripts/pack_release.py --version 260916-P05   # 显式指定
```

产出 `桌面\源流迭代\源流park-video-skill-<版本>.zip`（外层 7 项 + 技能本体）：

| 外层文件 | 作用 |
|---|---|
| `00-部署说明.txt` | 安装步骤；写进 zip 时**版本串自动同步成本次版本**，不会出现"包是 P05、说明写 P03" |
| `install_p05.py` | 一键安装/升级（保留 `output\`/`state\`/个人设置，装完自带检，失败自动回滚） |
| `安装或升级P05.bat` | 双击入口（纯 ASCII + CRLF，自动找 `py`/`python`） |
| `源流园区短视频素材分配规则使用手册-<YYMMDD>.docx` | 说明书 —— **自动取桌面上最新那份**，包内按当天日期改名 |
| `语义规则分配表.xlsx` | 表格（规则的人工编辑入口） |
| `创建桌面快捷方式.bat` | 双击 → 桌面生成「源流工作台」图标（在 `.workbuddy\skills` 下自动找工作台，**不写死路径**，同事解压到哪台机器都能用） |
| `shortcut-help.txt` | 快捷方式说明 |

排除本机资产：`output\`、`state\`、`__pycache__`、`tests/_tmp`、`_tts_probe`、
`gui_settings.json`、`*.bak`。

**两道隐私把关**（P05 §6）：
1. 打包前扫描技能本体，发现管理员用户名/设备标识字段**直接拒绝打包**
   （本体文件受 `release_manifest` 哈希保护，不能在 zip 层偷偷改写字节）。
2. 打包后再验一遍成品 zip（含 docx/xlsx 内部 xml），仍有敏感串就拒绝交付。

发版前验证：把包解到别处跑全部回归（P05 起 15 套）应全 PASS。

## 文件结构

```
源流park-video-skill/
├── SKILL.md                      # 本文件
├── config/
│   ├── default_styles.json       # 三风格模板参数（画布/轨道/音色/BGM/字幕数/CTA/免责花字）
│   ├── tone_pool.json            # TTS 音色候选池（confirmed 3 + reference 28）
│   ├── semantics.json            # 【可改】语义规则（关键词/块→素材桶/交通 hint/阈值）
│   └── materials_baseline.json   # 素材库团队基准指纹（负责人 --set-baseline 生成）
├── templates/                    # 三份模板元数据（style1_jiaxing/2_zs/3_nanjing.json）
│                                  # P05 §6 起已瘦身为**纯元数据**（version/new_version/
│                                  # color_space/config/platform/render_index_track_mode_on），
│                                  # 每份约 1 KB；不再保存任何本机缓存路径或设备标识
├── scripts/
│   ├── make_park_video.py        # 主 CLI（读脚本→生成→回加密→写入草稿库）
│   ├── arrange_park_video.py     # 阶段二 CLI（jianying 模式的配音后归位）
│   ├── **draft_naming.py**       # 【P05 §1】草稿命名 + 目录独占预留（GUI/CLI 单一实现）
│   │                              #   显式 --name 走 exact=True：不追加任务尾码
│   ├── **script_split.py**       # 【P05 §4】多脚本切分（只有单独一行 --- 才分隔）
│   ├── **jianying_path.py**      # 剪映安装目录/字体探测（单一实现，兼容 Apps\<版本>）
│   ├── bgm_pool.py               # BGM 选曲（P05 §7 + 二轮：多缓存目录 + fixed 匹配）
│   ├── draft_builder.py          # 草稿生成器（时间线/素材/字幕/TTS/BGM/免责花字）
│   ├── subtitle_split.py         # 脚本切分模块（硬断句+意群+CTA）
│   ├── material_matcher.py       # 素材匹配模块（11 语义桶，含 validate_materials_dir）
│   ├── semantics_config.py       # semantics.json 加载 + 结构校验（写错即停，不静默）
│   ├── production_log.py         # 产出台账（CSV 追加 + 汇总/合并/素材库指纹）
│   ├── material_governance.py    # 素材治理（频次记录/大库降权/基准版本校验）
│   ├── atomic_io.py              # 原子写入 + 跨进程文件锁
│   ├── runtime_context.py        # 运行模式/任务ID/路径隔离（测试禁写生产）
│   ├── job_receipt.py            # 任务回执
│   ├── release_manifest.py       # 发布清单生成/校验
│   ├── pack_release.py           # 打包发版（P05 §6：两道隐私把关）
│   ├── doctor.py                 # 环境自检（Python/剪映/素材库/语义配置/模块完整性）
│   ├── upgrade.py                # 一键升级（备份→复制→自检→失败回滚）
│   └── jy_crypto.py              # 剪映草稿加解密（核心依赖，videoeditor.dll）
├── tests/
│   ├── verify_draft.py           # 草稿自检（时间轴/配音引用/时长一致性/帧网格）
│   ├── test_semantic_blocks.py   # 【回归基线】5 条定稿脚本块数 12/10/9/10/8
│   ├── test_draft_collision.py   # 【P05 §1】草稿目录独占预留/防覆盖（10 类场景）
│   ├── test_gui_thread_safety.py # 【P05 §2】工作台线程安全静态检查
│   ├── test_script_split.py      # 【P05 §4】多脚本切分
│   ├── test_make_copy.py         # 【P05 §5】同名素材不覆盖/同源只复制一次
│   ├── test_template_privacy.py  # 【P05 §6】模板与发布包隐私
│   ├── test_bgm_lookup.py        # 【P05 二轮】BGM 多缓存目录 + fixed 匹配与降级
│   ├── test_explicit_overwrite.py # 【P05 二轮】--name/--overwrite 契约（真实 CLI）
│   ├── test_release_wording.py   # 【P05 二轮】用户可见旧版本文案扫描（含包外层）
│   └── run_arrange_e2e.py        # 阶段二端到端回归（对照人工归位结果）
├── workbench/
│   ├── workbench_gui.py          # 工作台（启动即环境体检 + 首启填剪辑者向导）
│   ├── 启动工作台.bat            # GUI 启动器（自动找带 tkinter 的 python）
│   ├── install.bat / install.py  # 桌面快捷方式安装（幂等，GUI 首启/升级自动调）
│   ├── 源流.jpg / 源流.ico       # 应用品牌图 + 桌面图标（.ico 由 install.py 生成）
└── output/                       # production_log.csv 台账 + material_usage.json 频次
```

## 时间线结构

纯字幕模式（默认）**4 条轨道**；加 `--with-tts` 时 5 条（多一条配音轨）。

| 轨道 | type | attribute | 内容 |
|---|---|---|---|
| 视频 | video | 1 | 连续同素材合并为一段；source=源文件时长、target=展示时长 |
| 字幕 | text | 2 | 每条字幕一段，sub_type=4/add_type=2。纯字幕模式下 `text_to_audio_ids=[]`、`tts_auto_update=false`（必须解除音频关联，否则剪映会把字幕标成"待合成"） |
| TTS 配音 | audio | 0 | **仅 `--with-tts`**：type=text_to_audio，path 为占位符，剪映打开时自动合成 |
| BGM | audio | 0 | type=music，默认从本机音乐池随机选曲（`--bgm fixed` 换风格默认），音量 0.3，短曲自动循环 |
| 免责花字 | text | 2 | 常驻左下角，clip.scale≈1.076 |

## 注意事项

- 生成草稿命名遵循 `YYMMDD-城市-风格名`，如 `260901-南京-没想到`（用 `--name` 指定）
- 草稿加密由 `videoeditor.dll` 完成，需保证安装目录可写；`EncryptUtils` 内部会 `os.chdir(install_dir)`，外部调用务必用绝对路径

### jianying 模式（剪映音色）

- TTS 配音 wav **不会**在打开草稿时自动合成（实测 textReading 目录为空）：音色与字幕↔音频双向关联已预配好，用户在剪映中框选全部字幕 → 点「朗读」即可按预配音色批量生成配音；`tts_auto_update` 只在字幕文本被编辑时触发重合成
- 段的 `target_timerange.start` 必须按字幕顺序累积（剪映缺省按 0 解析——曾导致 7 个版本字幕/配音全部堆叠在时间线起点）
- **段时长必须对齐到 30fps 帧网格**：剪映里字幕/配音段的 `target_timerange.duration` 永远是 33333.33us 的整数倍。不做对齐的话每段攒下几毫秒误差，17 段后总时长偏长约 40ms，末尾字幕与配音渐进错位
- 解析剪映路径宏 `##_draftpath_placeholder_<uuid>_##/textReading/xxx.wav` 时，去掉宏后剩下的是 `/textReading/...`（以 `/` 开头），**不能用 `os.path.isabs` 判断**（Windows 下会误判为绝对路径，拼上草稿目录失败）——必须用 `^[A-Za-z]:/` 判断
- 任何以子进程方式跑的模块（bgm_pool 等）往 stdout 输出结构化数据时，必须**用哨兵行包裹**：videoeditor.dll 在加载和退出时都会往 stdout 打印 `V bytenn espresso version: x.y.z` / `mobilecv2: x.y.z`，直接 `json.loads(stdout)` 会失败
- 主进程里绝不能先加载 `videoeditor.dll` 再用 cv2 探测视频——会破坏 cv2 的视频探测，导致素材匹配全部失败（v9 踩过的坑）。凡是带 DLL 的操作一律子进程执行
- 智能包装（关键词大字卡/音效/贴纸/B-roll）由用户在剪映编辑器里点一次完成
- 三风格画布均为 9:16 竖屏但尺寸不同：Style1/2 = 1920×3414，Style3 = 1280×2276；横版素材 `scale = max(canvas_w/w, canvas_h/h, 1.0)` 铺满
- 明文 JSON 序列化为紧凑格式（`separators=(',',':')`，无空格无换行），与剪映格式一致
- TTS 素材 path 用 `##_draftpath_placeholder_<uuid>_##/textReading/xxx.wav` 占位，剪映打开时解析为真实草稿路径
- 字幕素材与 TTS 素材必须双向关联：`text_to_audio_ids` 指向 **TTS audio material id**，`audio material.text_id` 指回 **subtitle material id**

### 剪映保存草稿时会做的两处规范化（已实测）

用剪映打开脚本生成的草稿并保存后，解密回读会发现：

1. **字幕轨的 `attribute` 从 2 抹成 None**
   → 判断"哪条是字幕轨"不能用 `attribute == 2`，要按素材类型 `sub_type == 4` 判断
   （免责花字也是 text 轨，但 sub_type 不同）
2. **素材 `duration` 被对齐到 30fps 帧网格**
   如 wav 实际 1752000us，剪映保存后素材记 1766666us（53 帧）
   → 校验"文件时长 vs 素材时长"必须留 1 帧容差，否则会误报

两者都不影响播放，但会让"字节级对比"的测试失败。写测试时要考虑进去。
