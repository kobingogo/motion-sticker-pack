# motion-sticker-pack｜X 发布文案

## 简报

- 素材类型：开源工具 + 真实案例 + 操作教程
- 目标读者：想把角色图做成动态表情包的创作者、开发者和 AI 工具用户
- 共同主梁：动态表情包的难点不是生成一张图，而是把静态确认、动画、切图和平台交付连成可复用流程。
- 主要物证：仓库支持角色参考图、静态图板、九宫格视频和独立 MP4 四种输入；可导出 Animated WebP、GIF、PNG 和 ZIP。
- 目标动作：访问 GitHub 并安装 Skill。

## 算法预检

| 项 | 结果 | 说明 |
|---|---|---|
| R1 语义热区 | 过 | AI tools、open source、animated stickers、Codex、emoji 是明确领域词 |
| R2 普世性 | 过 | “一张图变一套表情”不依赖中文语境，海外读者也能理解 |
| R3 媒体计划 | 过 | 主帖配 15 秒 4:5 MP4；回复配 6 秒黑猫循环或流程卡 |
| R4 领域词 | 过 | 文案明确出现 animated stickers、WebP、GIF、PNG、ZIP、Codex Skill |

## 版本 A · 犀利解码

**正文：**

九宫格视频能播放，不等于它能当表情包用。

真正要交付的是：先确认静态表情板，再生成动画，最后切成一张张独立的 WebP / GIF / PNG。

我把这条流程做成了开源 Codex Skill：

`motion-sticker-pack`

一张角色图、一个静态图板、一个九宫格视频，甚至一批独立 MP4，都可以进入同一套处理流程。

Demo 和安装方式：
https://github.com/kobingogo/motion-sticker-pack

## 版本 B · 烟火通俗

**正文：**

给它一张角色图，再选几个 Emoji。

它会先做出静态表情板，让你确认“这九个动作对不对”；确认以后，才继续生成动画、切图，最后打包成 WebP、GIF、PNG 和 ZIP。

我把这套流程开源成了一个 Codex Skill：

`$motion-sticker-pack`

想把自己的头像、宠物或吉祥物做成一套动态表情，可以从这里开始：
https://github.com/kobingogo/motion-sticker-pack

## 版本 C · 格局升维

**正文：**

AI 生成一张表情图已经不难。

难的是把一次性的“生成结果”，变成一套下次还能复用的工作流：输入可追踪，静态结果先确认，动画单独处理，输出按平台交付。

`motion-sticker-pack` 把这条链路放进了一个开源 Codex Skill。

今天是一只猫，下一次可以是你的角色、宠物或产品吉祥物。

仓库：
https://github.com/kobingogo/motion-sticker-pack

## 推荐首发

推荐版本 B，配 `motion-sticker-pack-15s-4x5.mp4`。它对新手最容易理解，视频负责展示结果，正文负责解释“先确认、再动画、最后切图”。

## Thread｜推荐烟火路线

**1/5**

做动态表情包，最容易走错的一步，是把“能播放的九宫格视频”当成“已经可以发送的表情包”。

这两者中间，还差一次切图和一次格式适配。

**2/5**

`motion-sticker-pack` 的流程很简单：

上传角色图 → 选择风格和 Emoji → 确认静态表情板 → 生成动画 → 自动切图和打包。

先确认静态图，是为了避免错误结果继续流转到视频阶段。

**3/5**

它不只接受一张角色图。

已有静态图板、九宫格视频，或者一批独立 MP4，也可以进入对应的处理路径。

九宫格视频要先识别布局；独立 MP4 不需要伪造一个 1×1 网格。

**4/5**

同一套结果要准备两类文件：

普通 MP4，用来发 X、小红书和抖音；

单张 WebP / GIF / PNG，用来继续导入聊天软件。

不要把已经拍扁成黑底的宣传 MP4，当成透明贴纸源文件。

**5/5**

如果你用的是 Codex，安装后输入：

```bash
npx skills add kobingogo/motion-sticker-pack -g -y -a codex
```

然后在对话中调用：

```text
$motion-sticker-pack
```

仓库和完整教程：
https://github.com/kobingogo/motion-sticker-pack

## 技术回复帖

为什么要同时保留 MP4 和 WebP？

因为它们服务的对象不同：MP4 解决“让别人看到整组结果”，WebP / GIF / PNG 解决“让聊天软件逐张使用”。

所以这不是重复导出，而是把展示层和贴纸交付层分开。

## 配图与发布提醒

- 主帖：`motion-sticker-pack-15s-4x5.mp4`
- Thread：`stills/01-input-output.png` + `cards/03-workflow.png`
- 技术回复：`motion-sticker-pack-black-cat-loop-6s.mp4`
- 安装说明：`cards/04-install.png`
- 首发只保留一个 CTA：访问仓库并尝试安装。
- 以上是 AI 草稿，发布前请按自己的口语改一遍，并核对仓库链接和命令。

