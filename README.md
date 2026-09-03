# Motion Sticker Pack｜动态表情包制作器

[![ci](https://github.com/kobingogo/motion-sticker-pack/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kobingogo/motion-sticker-pack/actions/workflows/ci.yml)
[English](README.en.md) · [MIT](LICENSE) · [Release notes](RELEASE_NOTES.md)

把一张角色图或一段角色描述，变成经过静图确认、可审计、可打包的循环动态表情包。

`motion-sticker-pack` 是面向 Codex 的 Agent Skill。默认交付透明 PNG、lossless Animated WebP、兼容 GIF、处理报告和 ZIP；普通用户不需要手动运行脚本。

## 30 秒开始

在 Codex 中安装 Skill 后上传角色图，发送：

```text
$motion-sticker-pack
用附件角色做 3×3 动态表情包。
风格选 soft-plush，表情是：开心、喜欢、委屈、惊讶、亲亲、谢谢、加油、困困、点赞。
先给我确认静图，再生成动画。每格独立、小幅、可循环。
```

没有角色图也可以直接描述角色；流程会直接生成完整静图板，不先制造一张中间角色图。

交互顺序：

1. 提供图片或角色描述。
2. 选择风格和表情。
3. 检查静态网格。
4. 明确回复“确认，继续”。
5. 选择可用的视频路线。
6. 下载 WebP/GIF/PNG 与 ZIP。

## 三档动态路线

| 档位 | 路线 | 结果与成本 |
|---|---|---|
| AI 视频 | `native-video` / Grok / xAI / Kling / Seedance / Wan / FAL | 真实面部和肢体动作；可能产生调用费用 |
| 真实关键姿势 | `keypose-local` | 生图生成 anticipation/peak/recovery，本地编排；有真实姿势变化 |
| 轻动效 | `light-motion-local` | 零生成成本，只做小幅仿射循环；不承诺新肢体或表情动作 |

`transform-local` 和 `keyframe-local` 仍作为旧配置兼容别名，但新 route 统一输出 `light-motion-local`。

没有视频或本地图像能力时，`prompt-only` 只交付提示词和审计文件，不会伪造媒体。

## 16 个风格效果图

下面是同一只狐狸、同一套服装与配饰的 16 张静态风格探索图，适合直接横向比较视觉方向。它们是风格效果参考，不等同于动态路线证据；真实九格处理、GIF、Animated WebP、layout、route、processing 和 provenance 证据见 [gallery/](gallery/README.md)。

<table><tr><th width="140">ID</th><th>风格</th><th width="180">同一角色效果图</th></tr>
<tr><td><code>3d</code></td><td>统一 3D</td><td><img src="docs/assets/style-exploration/fox/01-3d.png" width="128" alt="3D 风格狐狸效果图"></td></tr>
<tr><td><code>realistic</code></td><td>电影感写实</td><td><img src="docs/assets/style-exploration/fox/02-realistic.png" width="128" alt="电影感写实狐狸效果图"></td></tr>
<tr><td><code>hand-drawn</code></td><td>暖色手绘</td><td><img src="docs/assets/style-exploration/fox/03-hand-drawn.png" width="128" alt="暖色手绘狐狸效果图"></td></tr>
<tr><td><code>chibi</code></td><td>虹彩 Q 版</td><td><img src="docs/assets/style-exploration/fox/04-chibi.png" width="128" alt="虹彩 Q 版狐狸效果图"></td></tr>
<tr><td><code>manga</code></td><td>国潮漫画</td><td><img src="docs/assets/style-exploration/fox/05-manga.png" width="128" alt="国潮漫画狐狸效果图"></td></tr>
<tr><td><code>pixel-art</code></td><td>精细像素</td><td><img src="docs/assets/style-exploration/fox/06-pixel-art.png" width="128" alt="精细像素狐狸效果图"></td></tr>
<tr><td><code>cute</code>（别名 <code>soft-plush</code>）</td><td>软萌毛绒</td><td><img src="docs/assets/style-exploration/fox/07-cute.png" width="128" alt="软萌毛绒狐狸效果图"></td></tr>
<tr><td><code>caricature-3d</code></td><td>夸张 3D 肖像</td><td><img src="docs/assets/style-exploration/fox/08-caricature-3d.png" width="128" alt="夸张 3D 肖像狐狸效果图"></td></tr>
<tr><td><code>fashion-realistic</code></td><td>时尚写实</td><td><img src="docs/assets/style-exploration/fox/09-fashion-realistic.png" width="128" alt="时尚写实狐狸效果图"></td></tr>
<tr><td><code>mascot-toy</code></td><td>品牌玩具吉祥物</td><td><img src="docs/assets/style-exploration/fox/10-mascot-toy.png" width="128" alt="品牌玩具狐狸效果图"></td></tr>
<tr><td><code>clay-cute</code></td><td>软陶萌宠</td><td><img src="docs/assets/style-exploration/fox/11-clay-cute.png" width="128" alt="软陶萌宠狐狸效果图"></td></tr>
<tr><td><code>fantasy-plush</code></td><td>奇幻毛绒</td><td><img src="docs/assets/style-exploration/fox/12-fantasy-plush.png" width="128" alt="奇幻毛绒狐狸效果图"></td></tr>
<tr><td><code>kawaii-anime</code></td><td>日系萌系</td><td><img src="docs/assets/style-exploration/fox/13-kawaii-anime.png" width="128" alt="日系萌系狐狸效果图"></td></tr>
<tr><td><code>retro-halftone</code></td><td>复古漫画网点风（探索）</td><td><img src="docs/assets/style-exploration/fox/14-retro-halftone.png" width="128" alt="复古漫画网点狐狸效果图"></td></tr>
<tr><td><code>ink-wash-meme</code></td><td>水墨 Meme 风（探索）</td><td><img src="docs/assets/style-exploration/fox/15-ink-wash-meme.png" width="128" alt="水墨 Meme 狐狸效果图"></td></tr>
<tr><td><code>emoji-hybrid</code></td><td>Emoji 混合风（探索）</td><td><img src="docs/assets/style-exploration/fox/16-emoji-hybrid.png" width="128" alt="Emoji 混合狐狸效果图"></td></tr></table>
其中 `retro-halftone`、`ink-wash-meme` 和 `emoji-hybrid` 属于受控验证前的风格探索，不应被当作已验证的动态 Provider 路线。

## 最近生成的动态表情包 GIF

下面是最近完成的“暖尾狐狸”九宫格案例：9 个独立表情均为原始 240×240、8 fps、3 秒可循环 GIF，已通过透明度、边界和编码检查。完整来源与 SHA-256 记录见 [`manifest.json`](docs/assets/recent-sticker-pack/manifest.json)；13 个风格的长期验证证据仍见 [gallery/](gallery/README.md)。

| 社死 | 阴阳怪气 | 吃瓜 |
|---|---|---|
| <img src="docs/assets/recent-sticker-pack/fox-01.gif" width="180" alt="暖尾狐狸社死动态表情"> | <img src="docs/assets/recent-sticker-pack/fox-02.gif" width="180" alt="暖尾狐狸阴阳怪气动态表情"> | <img src="docs/assets/recent-sticker-pack/fox-03.gif" width="180" alt="暖尾狐狸吃瓜动态表情"> |
| <img src="docs/assets/recent-sticker-pack/fox-04.gif" width="180" alt="暖尾狐狸摆烂动态表情"> | <img src="docs/assets/recent-sticker-pack/fox-05.gif" width="180" alt="暖尾狐狸收到动态表情"> | <img src="docs/assets/recent-sticker-pack/fox-06.gif" width="180" alt="暖尾狐狸冲鸭动态表情"> |
| <img src="docs/assets/recent-sticker-pack/fox-07.gif" width="180" alt="暖尾狐狸破防动态表情"> | <img src="docs/assets/recent-sticker-pack/fox-08.gif" width="180" alt="暖尾狐狸疑惑动态表情"> | <img src="docs/assets/recent-sticker-pack/fox-09.gif" width="180" alt="暖尾狐狸撒娇动态表情"> |

查看或验证选择器：

```bash
python3 scripts/style_selector.py --format markdown
python3 scripts/style_selector.py --format core
python3 scripts/style_selector.py --style clay-cute
python3 scripts/style_selector.py --style soft-plush
python3 scripts/style_selector.py --verify-only
```

其中 `cute` 是兼容保留的规范 ID，`soft-plush` 是推荐的可读别名；两者解析到同一份真实验证证据。

v0.3.1 的核心目录目标为 16 个方向；`--format core` 会同时显示已验证和待受控验证的状态，普通 `--format markdown` 只显示已通过证据门槛的风格。待受控验证的核心风格可以在用户明确指定时编译，但会在合同中标记为未验证。核心目录之外的文化媒介、印刷、复古 UI 或混合风格，请使用 `custom` 描述，不会被硬编码成未经验证的 preset。

```text
$motion-sticker-pack
风格 custom：水墨留白、干湿笔触，保留角色身份；不要添加整格背景。
```

完整证据见 [gallery/](gallery/README.md)，完整 gallery 媒体按版本发布到 [GitHub Releases](https://github.com/kobingogo/motion-sticker-pack/releases)。旧版完整案例包已迁移至 [GitHub Release asset](https://github.com/kobingogo/motion-sticker-pack/releases/download/v0.2.0/motion-sticker-pack-legacy-gallery-2026-09.zip)。

## 静图与透明度

首选 GPT-image-2 等能返回真实 Alpha 的图像工具。静图请求按输入模式选择：参考图路线先生成不透明纯绿 `#00FF00` 源图，文字定义路线先尝试透明 RGBA PNG；两者都必须经过本地像素检查，失败时才进行一次有界重试。

- 棋盘格是可见背景，不是透明度。
- 不假设模型真的返回了请求的 3×3；必须重新检测实际布局。
- 低于 0.75 的布局置信度需要人工确认。
- 静图确认后，图片或 layout 的任一字节变化都会使审批哈希失效。

## 自动幕布

Grok 路线保持严格 `#00FF00` 合同。

非 Grok 路线会从绿、蓝、品红、青四个候选中计算前景冲突，选择距离主体颜色最远的幕布。候选分数、每个 Provider 的颜色和确定性铺底输入都记录在 `video-task.json` 与 `artifact-manifest.json`。用户仍可显式传入 `--key-color`。

## 统一输出

所有新本地路线默认：

- 240×240；
- 8fps；
- lossless Animated WebP 为首选；
- GIF 为兼容格式；
- PNG 为干净首帧；
- 编号按实际检测布局的 row-major 顺序；
- 同一个 `delivered/` 目录与一个 `sticker-pack.zip`。

```text
works/<character-slug>/delivered/
├── 01.webp … NN.webp
├── 01.gif  … NN.gif
├── 01.png  … NN.png
├── preview.png
├── layout.json
├── processing.json
├── job-state.json
├── prompts.json
├── route.json
├── attempt-ledger.json
├── artifact-manifest.json
└── sticker-pack.zip
```

完整规范见 [output contract](references/output-contract.md)。

## 安全与审计

- 静图审批由 SHA-256 绑定。
- 付费 attempt 首次只提交一次；用户主动要求再次生成时，必须先创建绑定当前输入和 route 的重试批准，不会静默重放。
- xAI request id 支持恢复同一个远端任务。
- 输出目录采用可恢复事务，拒绝输入/输出重叠。
- `artifact-manifest.json` 记录静图、提示词、pose、route、视频和交付的依赖谱系。
- 每个原生视频帧都经过幕布、Alpha、跨格实例与编码后 QC。

更多说明：[Routing and audit](docs/advanced/routing-and-audit.md)。

## 安装

### 在 Codex 对话中安装（推荐）

在 Codex 中发送以下消息：

```text
$skill-installer
请从 GitHub 仓库 `kobingogo/motion-sticker-pack` 安装 `motion-sticker-pack` Skill。
Skill 位于仓库根目录（路径 `.`），安装名设为 `motion-sticker-pack`。
```

安装器完成后，在下一轮 Codex 消息中发送 `$motion-sticker-pack` 验证；例如：

```text
$motion-sticker-pack
确认已加载该 Skill，并告诉我可用的入口和当前版本。
```

如果发送 `$skill-installer` 后提示未找到该 Skill，可发送下面的兜底请求（需要当前 Codex 会话具备终端权限）：

```text
请不要调用 `$skill-installer`。请直接从
https://github.com/kobingogo/motion-sticker-pack.git 获取仓库，并将包含 `SKILL.md` 的仓库根目录安装到
`$CODEX_HOME/skills/motion-sticker-pack`；如果未设置 `$CODEX_HOME`，使用 `~/.codex/skills/motion-sticker-pack`。
安装完成后报告实际路径，并在下一轮消息中加载该 Skill。
```

### 本地脚本环境（可选）

如果只在 Codex 对话中调用，使用上面的安装流程即可。需要本地运行脚本或参与开发时，再执行：

```bash
git clone https://github.com/kobingogo/motion-sticker-pack.git
cd motion-sticker-pack
python3 -m pip install -r requirements.txt
npm ci --ignore-scripts
```

Skill 的执行合同见 [SKILL.md](SKILL.md)。

## 高级文档

- [Provider、凭据与 ZDR](docs/advanced/providers-and-zdr.md)
- [路由、ledger 与 artifact manifest](docs/advanced/routing-and-audit.md)
- [完整 CLI 参考](docs/advanced/cli-reference.md)
- [仓库媒体策略、Release gate 与历史瘦身评估](docs/advanced/repository-maintenance.md)
- [风格库策略与 v0.4 候选](docs/advanced/style-library.md)
- [提示词合同](references/prompt-contract.md)
- [关键姿势工作流](references/keypose-workflow.md)
- [对抗性审计](docs/adversarial-audit.md)

## 开发验证

```bash
python3 -m unittest discover -s tests -v
npm test
python3 scripts/style_selector.py --verify-only
python3 scripts/check_repository_policy.py
```

CI 在 Python 3.10/3.12 和 Node 22 上运行。正式版本只能通过 [release workflow](.github/workflows/release.yml) 从通过完整验证的 `main` 创建 tag 和 Release。

## License

MIT。角色肖像、商标和生成媒体的使用权由使用者自行确认。
