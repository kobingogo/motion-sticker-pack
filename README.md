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

## 13 个真实验证风格

选择器只展示经过真实九格处理验证的风格。每个风格都附带 240×240 静图、真实动作 GIF、layout、来源 route 和 processing 报告。

| ID | 风格 |
|---|---|
| `3d` | 统一 3D，可明确指定动画或真人写实子风格 |
| `realistic` | 电影感写实 |
| `hand-drawn` | 暖色手绘 |
| `chibi` | 虹彩 Q 版 |
| `manga` | 国潮漫画 |
| `pixel-art` | 精细像素 |
| `cute` | 软萌毛绒 |
| `caricature-3d` | 夸张 3D 肖像 |
| `fashion-realistic` | 时尚写实 |
| `mascot-toy` | 品牌玩具吉祥物 |
| `clay-cute` | 软陶萌宠 |
| `fantasy-plush` | 奇幻毛绒 |
| `kawaii-anime` | 日系萌系 |

<p>
  <img src="gallery/styles/plush-toy/motion.gif" width="120" alt="3D 毛绒玩具动态验证">
  <img src="gallery/styles/cinematic-realistic/motion.gif" width="120" alt="电影感写实动态验证">
  <img src="gallery/styles/iridescent-chibi/motion.gif" width="120" alt="虹彩 Q 版动态验证">
  <img src="gallery/styles/pixel-art/motion.gif" width="120" alt="像素艺术动态验证">
  <img src="gallery/styles/manga-cel/motion.gif" width="120" alt="漫画动态验证">
</p>

查看或验证选择器：

```bash
python3 scripts/style_selector.py --format markdown
python3 scripts/style_selector.py --style clay-cute
python3 scripts/style_selector.py --verify-only
```

完整证据见 [gallery/](gallery/README.md)。旧版完整案例包已迁移至 [GitHub Release asset](https://github.com/kobingogo/motion-sticker-pack/releases/download/v0.2.0/motion-sticker-pack-legacy-gallery-2026-09.zip)。

## 静图与透明度

首选 GPT-image-2 等能返回真实 Alpha 的图像工具。静图请求永远先尝试透明 RGBA PNG；只有本地像素检查失败后，才使用单一高对比色键备用请求。

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
- 付费 attempt 只能提交一次；中断状态不会静默重放。
- xAI request id 支持恢复同一个远端任务。
- 输出目录采用可恢复事务，拒绝输入/输出重叠。
- `artifact-manifest.json` 记录静图、提示词、pose、route、视频和交付的依赖谱系。
- 每个原生视频帧都经过幕布、Alpha、跨格实例与编码后 QC。

更多说明：[Routing and audit](docs/advanced/routing-and-audit.md)。

## 安装

```bash
git clone https://github.com/kobingogo/motion-sticker-pack.git
cd motion-sticker-pack
python3 -m pip install -r requirements.txt
npm ci --ignore-scripts
```

然后将仓库安装为 Codex Skill，或按你当前 Codex 环境的 Skill 安装方式引用本目录。Skill 的执行合同见 [SKILL.md](SKILL.md)。

## 高级文档

- [Provider、凭据与 ZDR](docs/advanced/providers-and-zdr.md)
- [路由、ledger 与 artifact manifest](docs/advanced/routing-and-audit.md)
- [完整 CLI 参考](docs/advanced/cli-reference.md)
- [仓库媒体策略、Release gate 与历史瘦身评估](docs/advanced/repository-maintenance.md)
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
