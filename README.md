# Motion Sticker Pack｜动态表情包制作器

[English](README.en.md) · [MIT License](LICENSE)

> 上传一张角色图，选出风格和表情，确认静态图板后，生成可发送、可打包的透明循环动态表情包。

`motion-sticker-pack` 是一个 [Agent Skill](https://agentskills.io)。安装后，按对话使用即可：上传图片 → 选择风格 → 选择 Emoji 或短描述 → **确认静态图** → 生成视频 → 自动切图、去背、导出 WebP/GIF/PNG 并打包 ZIP。

普通使用不需要手跑 Python 脚本，也不需要理解 FFmpeg 或 Provider 路由。Agent 应读取 [`SKILL.md`](SKILL.md) 完成整条链路。

```text
$motion-sticker-pack
```

## 一键安装

```bash
npx skills add kobingogo/motion-sticker-pack -g -y
```

这条命令会把 Skill 装到当前机器已检测到的 Agent（Grok Build、Codex、Claude Code、Cursor 等）的**用户级**目录。只要装 Grok：

```bash
npx skills add kobingogo/motion-sticker-pack -g -y -a grok
```

更新：

```bash
npx skills update motion-sticker-pack -g -y
```

## 当前状态

媒体处理主链路已经可用：网格检测、哈希绑定的静态审批、整板切图、去背、Animated WebP、循环 GIF、首帧 PNG 和 ZIP 都有实现和测试。仓库内验证过三条视频路径：

```text
本机 Grok Build（image_to_video）
        ↓ 失败或不可用
直连 xAI Videos API
        ↓ 失败或不可用
本地 transform-local 备用动效
```

Skill 要在其他 Agent 上稳定复现，靠的是统一工作目录和审批合同，而不是某一次手工跑通。关键约定：

- 静态生图必须使用**当前宿主里真正能接收参考图**的工具；不要假设一定存在 `image_edit` 或 `image_gen`。
- 生成的静图必须先给用户确认。用户自己提供的现成图板用 `--source-type user-supplied`，不要再要求一次 approve。
- 任何动画（宿主 native 视频、外部 Provider、关键姿态、本地 transform）之前都要 `manage_job_state.py verify`。
- `probe` → `route` → `execute` 必须使用同一份 `work/video-providers.json` 和 `work/video-task.json`。
- 独立贴纸走 `scripts/process_independent_stickers.py`，不要为每张图伪造 `1×1 layout.json`。
- `native-video` 是工作模式；配置里的 driver 名叫 `native-tool`。两者不是两条路。

威胁模型、已修复问题和剩余边界见 [`docs/adversarial-audit.md`](docs/adversarial-audit.md)。

## 30 秒开始使用

### 1. 安装 Skill

推荐一键安装（全局，自动检测本机 Agent）：

```bash
npx skills add kobingogo/motion-sticker-pack -g -y
```

只装到指定宿主：

```bash
npx skills add kobingogo/motion-sticker-pack -g -y -a grok
npx skills add kobingogo/motion-sticker-pack -g -y -a grok -a codex -a claude-code
```

这会把带 `scripts/` 的完整 Skill 装到例如 `~/.grok/skills/motion-sticker-pack`、`~/.codex/skills/motion-sticker-pack`、`~/.claude/skills/motion-sticker-pack`。仓库在 [github.com/kobingogo/motion-sticker-pack](https://github.com/kobingogo/motion-sticker-pack)。

从源码开发时也可以自己链接：

```bash
git clone https://github.com/kobingogo/motion-sticker-pack.git
ln -s "$PWD/motion-sticker-pack" ~/.grok/skills/motion-sticker-pack
ln -s "$PWD/motion-sticker-pack" ~/.codex/skills/motion-sticker-pack
ln -s "$PWD/motion-sticker-pack" ~/.claude/skills/motion-sticker-pack
```

Codex 可使用 `$motion-sticker-pack`；其他宿主直接说“使用 motion-sticker-pack Skill”。

### 2. 安装本地媒体依赖

完整交付需要 Python 3.10+、Pillow、NumPy、FFmpeg 和 FFprobe：

```bash
python3 -m pip install -r requirements.txt
```

macOS：`brew install ffmpeg`。Ubuntu/Debian：`sudo apt update && sudo apt install ffmpeg`。

验证：

```bash
python3 -c "import PIL, numpy; print(PIL.__version__, numpy.__version__)"
ffmpeg -version && ffprobe -version
```

当前验证环境：Python 3.10.12、Pillow 12.3.0、NumPy 2.2.6、FFmpeg 8.1.2。

若要使用仓库内置的 xAI / Kling / Seedance / Wan / FAL 执行器，再在 Skill 根目录执行 `npm ci`（需要 Node 20+）。只探测本地 Agent 工具或只用本地动画时可以不装 Node 依赖。

### 3. 开始对话

```text
$motion-sticker-pack
```

上传角色参考图，按提示选择风格并输入 Emoji 或短描述。宿主已经有可调用的图生视频工具时，不必再配外部模型。

使用 **Grok Build** 时，请先看下面的 [隐私 Opt in](#grok-build-隐私opt-in-与-zdr)。未 Opt in 时，本机 `image_to_video` 常会直接报 ZDR/隐私错误，这不是提示词问题。

## 对话流程

如果宿主支持表单或选项卡，Agent 应用结构化控件；否则用编号列表。流程相同：

```text
1. 调用 Skill
        ↓
2. 上传一张角色参考图
        ↓
3. 选择风格（见下方八种预设，或自定义）
        ↓
4. 选择 Emoji，或输入简短表情描述
        ↓
5. 组装提示词，用「能接收该参考图」的宿主工具生成静态网格图
        ↓
6. 展示静图和实际网格检测结果
        ↓
   ┌─────────────┴─────────────┐
   ▼                           ▼
确认，继续生成视频           重新生成并说明修改
   │                           │
   │                           └── 废弃旧审批与下游产物，回到第 5 步
   ▼
7. verify 审批哈希 → 生成整板视频（或约定降级）
        ↓
8. 切图、去背、导出 WebP/GIF/PNG，装配报告后打包 ZIP
```

风格预设（与 CLI / `references/style-presets.json` 一致，**没有 `meme`**）：

1. `3d` — 3D 卡通风（默认）
2. `hand-drawn` — 手绘风
3. `chibi` — Q 版
4. `manga` — 漫画风
5. `pixel-art` — 像素艺术
6. `realistic` — 写实还原
7. `cute` — 可爱风
8. `retro` — 复古风
9. `custom` — 自定义短描述

一次典型对话：

```text
用户：$motion-sticker-pack

Agent：请上传一张角色参考图。

用户：[上传图片]

Agent：请选择风格：1. 3D 卡通风  2. 手绘风  3. Q 版  4. 漫画风
      5. 像素艺术  6. 写实还原  7. 可爱风  8. 复古风  9. 自定义

用户：3D

Agent：请输入希望融入的 Emoji 或简短表情描述。

用户：🎸😍🥹😘🥰

Agent：[生成并展示静态网格表情图]
检测结果：3 列 × 3 行，共 9 格，置信度 0.99。
请选择：
- 确认，继续生成视频
- 重新生成，并告诉我需要修改什么

用户：确认，继续生成视频

Agent：[verify → 选择视频能力 → 生成 → 切图去背 → 输出 ZIP]
```

第一句话里已经带上图片、风格和表情时，跳过重复询问，直接生成静图。**只要静图是 Skill 生成的，确认步骤都不能跳过。** 用户上传的现成图板除外。

## 你能用它做什么

| 你提供的内容 | Agent 怎么处理 |
|---|---|
| 一张角色参考图 | 生成静图板 → 检测网格 → 等你确认 → 动画化并打包 |
| 一张现成静图板 | 检测网格，`--source-type user-supplied`，不再二次 approve |
| 多张独立透明贴纸 | `process_independent_stickers.py`，不伪造九宫格 |
| 一段整板动画视频 | 必要时先抽代表帧做网格检测，再切分、去背、打包 |
| 多段独立视频 | 跳过网格切分，逐个后处理 |

它不负责从零建立角色身份，也不是通用剪辑器。输入里最好已经有一个可识别的角色。

建议同时告诉 Agent：情绪或 Emoji、视觉风格、是否允许外部付费 API、是否必须完全本地、布局偏好、时长和帧率。未提供的参数用保守默认值：小动作、固定镜头、可循环、6 fps、透明或便于去背的纯色背景。

Grok 宿主上的 `image_to_video` 只接受 **6 或 10 秒**；工作流默认写 6 秒。不要把 3 秒写进将交给 Grok 的 task。

## Grok Build 隐私：Opt in 与 ZDR

Grok Build 的视频工具受账户隐私策略约束。报 `video tools are unavailable under ZDR` 时，先查隐私设置，不要改提示词、也不要手改 `~/.grok`。

这里有两件不同的事。

### 1. 个人账户：`/privacy` Opt in

Grok CLI 会把 `/privacy` 里的**数据保留 Opt out** 当成与团队 ZDR 类似的限制，即使 `authenticate.is_zdr` 仍为 false。官方说明：[Video Output Storage under ZDR](https://docs.x.ai/build/settings/zdr-video-storage) —— *Video tools will be enabled if the privacy setting is off (`/privacy`).*

要在**不配置 S3** 的情况下使用本机 `image_to_video`：

1. 打开已登录的 Grok Build。
2. 运行 `/privacy`（也可在 `/settings` 里看同一项）。
3. 选择 **Opt in**，允许编码/会话数据保留。
4. 确认后 `coding_data_retention_opt_out` 应为 `false`。
5. 重新开一轮会话后再生成视频。

本仓库的验证记录：Opt in 之后，Grok CLI `image_to_video` 在没有 S3 的情况下成功；**没有修改**原有 `~/.grok` 配置文件，只改了账户隐私项。

含义对照：

| `/privacy` 选择 | 内部状态 | 本机 `image_to_video` |
|---|---|---|
| **Opt in**（允许保留） | `coding_data_retention_opt_out = false`，官方所说 privacy setting off | 可用，不必配 S3 |
| **Opt out**（拒绝保留） | `coding_data_retention_opt_out = true`，被当成类 ZDR | 拒绝，除非配置了控制台同步的 ZDR 视频存储 |

Opt in 会让 Grok Build 按 xAI 当时策略保留相关数据。需要更强隐私时请保持 Opt out，并改走下面的团队 ZDR 存储，或使用 `xai-direct`。切换 `/privacy` 可能删除此前已同步的编码数据，以 xAI 当时说明为准。

### 2. 团队 Zero Data Retention（ZDR）

团队开启 ZDR 后，生成视频必须落到用户自己的存储。在控制台配置 S3 兼容桶，让 `[tools.zdr_video_output_s3]` **同步进** `managed_config.toml`。字段与步骤见 [xAI ZDR Video Storage](https://docs.x.ai/build/settings/zdr-video-storage)。

注意：

- Grok Build 的 `image_to_video` **没有** `output.upload_url` 参数；不能靠提示词把视频传到任意 URL。
- 只在本机放一份未经控制台签发的 `managed_config.toml` 不够。Grok CLI 1.0.10 在服务端没有 managed policy 时会清掉这份本地文件。
- S3 endpoint 必须能被 xAI 经 HTTPS 访问，并应支持 path-style URL（`https://endpoint/bucket/key`）。
- 改完配置后重启 Grok Build。

### 3. 同一账户仍可走直连 API

`scripts/xai_rest_video_adapter.py`（配置 id：`xai-direct`）走 xAI Videos REST，**不经过** Grok Build 的 `image_to_video`。因此：Grok Build 因 `/privacy` Opt out 或团队 ZDR 拒绝视频工具时，同一账户的直连 API 仍可能成功。

直连需要 `XAI_API_KEY`。API 侧若也要求用户存储，再设 `XAI_VIDEO_UPLOAD_URL`，并配 `XAI_VIDEO_LOCAL_OUTPUT_PATH` 或 `XAI_VIDEO_DOWNLOAD_URL`。轮询中断时用 `XAI_VIDEO_REQUEST_ID` 恢复同一个任务，不会重新提交、也不会再计一次费用。

默认不要把环境里的 `XAI_API_KEY` 传给 Grok Build 适配器，以免静默改走 API 登录；只有在有意为之时装 `GROK_USE_XAI_API_KEY=1`。

### 4. 这不是图片或提示词失败

| 症状 | 先查什么 |
|---|---|
| Grok Build：`video tools are unavailable under ZDR` | `/privacy` 是否 Opt in；团队账号是否配了控制台同步的 S3 |
| 直连 API 成功、Grok Build 仍失败 | 正常。两条路的隐私/存储要求不同 |
| 改了本机 `managed_config.toml` 立刻又消失 | CLI 清掉了未签发文件，去控制台同步 |
| 想完全本地、不上传 | 请求里写明禁止外部 API，走 `transform-local` |

对应实现：[`scripts/grok_build_video_adapter.py`](scripts/grok_build_video_adapter.py)、[`scripts/xai_rest_video_adapter.py`](scripts/xai_rest_video_adapter.py)。

## 视频能力怎么选

Skill 按下面顺序选路，除非你点名某个 Provider：

1. 当前会话里**可调用**、且接受参考图的图生视频工具（工作模式名 `native-video`，配置 driver 名 `native-tool`）
2. 已配置且满足任务的外部 Provider，按 `priority` 降序
3. 有生图能力时：关键姿态 + 本地编排（`keypose-local`）
4. 仅有 Pillow/NumPy 时：整张贴纸的仿射循环（`transform-local`）
5. 以上都没有：`prompt-only`，只交付提示词和路由审计并**明确停止**，不声称已经生成视频

仓库附带的 Grok 示例把 fallback 设为 `transform-local`，因此「没有视频」时默认落到本地轻量动效，而不是关键姿态。需要 keypose 时，在配置里把 `routing.fallback` 写成 `keypose-local`，并提供真实的 `runtime-tools.json`。

只支持文生视频、不能吃参考图的工具，不算本任务的图生视频能力。

探测和选路不会产生费用。只有显式执行某一个编号的 route attempt 才会提交生成。外部路径在第一次付费调用前，Agent 必须说明将使用哪个 Provider，以及可能产生费用。

内置可执行的 AI SDK 适配器：xAI、Kling AI、ByteDance/Seedance、Alibaba/Wan、FAL。Google/Veo、Replicate、MiniMax 等可用同一协议注册，但需要宿主原生工具或 `command` Adapter。

## 可直接复制的请求

### 从角色图做完整动态包

```text
$motion-sticker-pack 使用附件角色制作一套动态表情包。
融入 🎸😍🥹😘🥰，圆润 3D 玩具贴纸风。
每个动作都要轻微、独立、可循环，禁止镜头运动和跨格。
优先使用当前 Agent 的视频能力，最后输出透明 WebP、GIF、PNG 和 ZIP。
```

先看静图。回复「确认，继续生成视频」后才进入视频；回复「重新生成」则废弃上一版审批、布局和视频计划。

### 动画化已有图板

```text
$motion-sticker-pack 动画化这张表情图板。
这是我选定的源图，不要再生成静图，也不要再要求我确认一次。
先检测实际行列，再为每格设计不同的小动作。
```

### 处理整板视频

```text
$motion-sticker-pack 把附件视频切成独立动态表情。
没有对应静图时，先抽一帧做网格检测，再按检测结果切图。
输出 6 fps 透明 Animated WebP、GIF、首帧 PNG 和 ZIP。
```

### 多张独立贴纸

```text
$motion-sticker-pack 这几张是彼此独立的透明贴纸，不要拼成九宫格。
逐张做成可循环动态表情，最后打一个 ZIP。
```

### 完全本地

```text
$motion-sticker-pack 只使用本地能力处理这张图，不调用任何外部 API。
如果没有本地视频模型，就使用本地轻量循环动画，并告诉我用了哪种降级。
```

### 指定外部模型

```text
$motion-sticker-pack 使用我配置的 seedance-primary 生成视频。
失败后最多再尝试一个已配置 Provider，不要重复产生付费请求。
```

## 可选：配置外部视频 Provider

宿主已经有图生视频工具时可以跳过本节。否则：

```bash
cp assets/video-providers.example.json video-providers.json
```

启用需要的 Provider，只写环境变量**名**，不要把密钥写进 JSON：

```json
{
  "id": "xai-direct",
  "driver": "command",
  "provider": "xai",
  "model": "grok-imagine-video",
  "enabled": true,
  "priority": 80,
  "credentials": {
    "env": ["XAI_API_KEY"]
  },
  "capabilities": ["image-to-video"]
}
```

```bash
export XAI_API_KEY='your-key'
npm ci
```

把配置路径告诉 Agent。完整字段与 Adapter 协议见：

- [`assets/video-providers.example.json`](assets/video-providers.example.json)
- [`assets/video-task.example.json`](assets/video-task.example.json)
- [`references/video-providers.schema.json`](references/video-providers.schema.json)
- [`references/video-task.schema.json`](references/video-task.schema.json)
- [`references/runtime-routing.md`](references/runtime-routing.md)

自定义中转站请写 `command` Adapter，接收 `--task` / `--output` 两个绝对路径，并归一化结果 JSON。Skill 不会假装只改一个 `baseURL` 就能兼容所有中转站。

## 隐私、费用与凭证

- 要求完全本地时，在请求里写明「不要调用外部 API」
- 外部视频模型会上传参考图和提示词，可能计费；失败重试也可能计费
- 默认限制尝试次数，不会无限重试
- 配置文件只保存环境变量名；子进程只继承基础运行变量和当前 Provider 声明的凭证变量
- 密钥不得进入提示词、报告、命令行或 Git
- Grok `/privacy` Opt in 与团队 ZDR 是账户级策略，见上一节

## 最终会得到什么

```text
output/
├── 01.webp ... NN.webp
├── 01.gif  ... NN.gif
├── 01.png  ... NN.png
├── layout.json
├── job-state.json               # 发生静态审批时
├── prompts.json                 # 发生生成时
├── route.json                   # 发生路由时
├── processing.json
└── sticker-pack.zip
```

- `.webp`：循环 Animated WebP，保留较完整 Alpha
- `.gif`：循环 GIF，供不接受 WebP 的聊天应用；透明为调色板二值透明
- `.png`：第一帧透明 PNG
- `layout.json`：实际检测布局
- `job-state.json` / `prompts.json` / `route.json`：审批、提示词与路由审计；由 `assemble_delivery.py` 拷入最终目录和 ZIP
- `processing.json`：尺寸、帧率、Alpha、越界和循环质量

文件按行优先编号。`NN` 等于 `detected_layout.count`，不等于最初口头请求的格数。

不要相信视频模型里看起来像透明的棋盘格。有真实 Alpha 就保留；否则用统一高对比纯色背景，只去掉与裁切边缘连通的相似色，避免挖空角色内部。

## 为什么不固定 3×3

请求布局只是偏好。模型可能少生成、多生成或改排列。后续全部读取 `detected_layout`：

- `3x3` = 3 列、3 行、9 格
- `4x3` = 4 列、3 行、12 格

置信度低于 `0.75` 时先看叠线图，确认或使用 `--override`，不要盲切。

## 常见问题

### 安装后 Agent 没有自动使用？

确认项目在宿主 Skill 目录并重开会话。也可显式 `$motion-sticker-pack`，或让 Agent 读 `SKILL.md`。

### 必须配置视频模型吗？

不必须。有本地视频工具就用本地；否则可走关键姿态或 `transform-local`。

### Grok 报 video tools unavailable under ZDR？

先运行 `/privacy` 并 **Opt in**。团队 ZDR 则要控制台同步的 S3，见 [Grok Build 隐私](#grok-build-隐私opt-in-与-zdr)。同一账户的 `xai-direct` 仍可能可用。

### 生成的静图不像我上传的角色？

静态生图必须把原图交给「接受参考图」的宿主工具。纯文生图会另造一个角色。让 Agent 先检查工具签名，再绑定参考图路径或附件句柄。

### 为什么不是我要求的 3×3？

以模型实际返回的图为准。看叠线图和 `layout.json`。

### 角色之间会互相影响？

整板图生视频常见跨格污染。可降低动作幅度，或只重做问题格。

### Animated WebP 能直接投稿所有平台吗？

不能。通用包同时给 WebP、GIF 和首帧 PNG。微信常用 GIF；Telegram 动态贴纸要 WebM；Discord 要 APNG。平台专用画布（240 / 512）仍在后续计划中。

### Agent 只给了提示词，没有文件？

若路由是 `prompt-only`，说明当前没有视频也没有本地图像处理，这是明确停点，不是半成品视频。若 Pillow、NumPy、FFmpeg 可用，至少应能跑 `transform-local` 并打包。

### 去背挖空了角色？

换与角色主色差更大的纯色背景。边缘变透明就降低阈值；背景残留可适度提高。不要用极高阈值处理复杂场景。

## 当前边界

- 整板视频仍可能跨格污染
- 网格检测针对等分图板；自由排版需要人工确认或 `--override`
- `/privacy` Opt out 或团队 ZDR 会关掉 Grok Build 视频工具，直到 Opt in 或配好存储
- 内置 AI SDK 执行器覆盖 xAI、Kling、ByteDance、Alibaba、FAL；升级依赖前必须重跑 Node 合同测试
- 关键姿态没有光流或生成式插帧；本地模式只做整张贴纸的轻量变换
- 通用包尚未自动转成各聊天平台投稿规格
- 身份一致性和动作自然度仍需人工看

## 给维护者和贡献者

普通用户不需要运行下面的命令。调试 Provider 或单独复用脚本时，以 `work/` 为唯一工作目录，`probe` / `route` / `execute` 使用同一份 config 和 task。

### 项目结构

```text
motion-sticker-pack/
├── SKILL.md
├── LICENSE
├── README.md / README.en.md
├── package.json / package-lock.json
├── requirements.txt
├── agents/openai.yaml
├── assets/                      # example 配置与 tile-plan 模板
├── references/                  # Agent 合同（intake、prompt、routing、output）
├── scripts/
├── tests/
└── tests-node/
```

根目录 `process_emoji_grid.py` 只转发到 `scripts/process_emoji_grid.py`。

### 统一工作目录

审批和逐格 `tile-plan.json` 就绪后：

```bash
python3 scripts/prepare_workflow.py \
  --work-dir work \
  --image "$PWD/static-sheet.png" \
  --layout "$PWD/layout.json" \
  --prompts "$PWD/prompts.json" \
  --state "$PWD/job-state.json" \
  --tile-plan "$PWD/tile-plan.json"

# 按当前宿主实际可调用的工具改 work/runtime-tools.json
# 然后始终使用 work/ 下同一份文件：

python3 scripts/probe_video_capabilities.py \
  --config work/video-providers.json \
  --tool-manifest work/runtime-tools.json \
  --output work/capabilities.json

python3 scripts/route_video_provider.py \
  --config work/video-providers.json \
  --capabilities work/capabilities.json \
  --task work/video-task.json \
  --output work/route.json
```

`prepare_workflow.py` 会把 example 里的占位绝对路径改成本仓库 `scripts/`。不要把 probe 指到 `assets/video-providers.example.json`、却把 execute 指到另一份 `video-providers.json`。

任何动画前：

```bash
python3 scripts/manage_job_state.py verify \
  --state work/job-state.json \
  --image work/static-sheet.png \
  --layout work/layout.json
```

用户提供的现成图板：

```bash
python3 scripts/manage_job_state.py create \
  --image work/static-sheet.png \
  --layout work/layout.json \
  --source-type user-supplied \
  --output work/job-state.json
```

不要对已经 `static-approved` 的 user-supplied 状态再跑 `approve`。

低置信度人工确认：

```bash
python3 scripts/inspect_sticker_sheet.py sheet.png \
  --override 4x3 \
  --output work/layout.json \
  --overlay work/layout-overlay-confirmed.png
```

整板视频若还没有 layout，先抽帧：

```bash
ffmpeg -y -i grid.mp4 -frames:v 1 work/representative-frame.png
python3 scripts/inspect_sticker_sheet.py work/representative-frame.png \
  --output work/layout.json \
  --overlay work/layout-overlay.png
```

独立贴纸、本地动画、交付装配：

```bash
python3 scripts/process_independent_stickers.py stickers output --fps 6

python3 scripts/keyframe_fallback.py work/static-sheet.png output \
  --state work/job-state.json \
  --layout work/layout.json \
  --fps 6

python3 scripts/assemble_delivery.py \
  --media-dir output \
  --audit-dir work \
  --output delivered \
  --require-job-state \
  --require-prompts \
  --require-route

python3 scripts/assemble_prompt_only.py \
  --static-prompt work/static-prompt.json \
  --tile-plan work/tile-plan.json \
  --prompts work/prompts.json \
  --route work/route.json \
  --output prompt-only
```

其余编译、审批、execute、切图命令见 [`SKILL.md`](SKILL.md) 的 Included commands。贡献前请跑：

```bash
python3 -m pip install -r requirements.txt
npm ci
python3 -m unittest discover -s tests -v
npm test
npm audit --audit-level=high
```

不要把真实密钥、私有媒体或付费 API 响应放进 fixtures。

## 后续计划

- API 中转站 Adapter 模板
- 逐格视频与单格重试
- 可选插帧、时序 Alpha 平滑、复杂视频抠图
- 微信 240 GIF、Telegram WebM、Discord APNG 等平台画布
- 体积预算、整包预览和可视化 QC

欢迎提交真实案例（输入、实际布局、路由、失败与修正），而不仅是最终效果图。

## License

[MIT](LICENSE) © 2026 kobingogo
