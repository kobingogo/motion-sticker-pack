---
workflow: general-video
flow: automation
storyboard: no
message: "V0.2.0 让动态表情包从生成到发送更顺、更稳、更好用"
destination: social-feed
aspect: 1440x1080
language: zh-CN
audience: creators-and-chat-users
length: 30s
angle: product-upgrade-showcase
---

## Intent

一支约 30 秒的 4:3 横版升级推广视频，用已完成的五组动态表情包证明 V0.2.0 的升级效果：每格动作更独立，透明边界更干净，循环更顺，并且一次得到 GIF/WebP 与 3s/6s 交付版本。整体要明快、好看、适合社交平台传播。

## Assets

- `assets/stickers-animated/` — 五组表情包的透明动画 WebP，作为推广片主物料。
- `assets/stickers/` — 五组表情包的透明首帧 PNG，作为静态回退/参考素材。
- `assets/promo-bgm.mp3` — 复用的轻快推广背景音乐。

## Customizations

- 4:3 横版（1440×1080），约 30 秒，中文字幕与数字信息清晰可读。
- 使用五组角色作为真实结果墙：团团猫、鳄鱼阿班、泡泡星人、紫薯幽灵、赛博锦鲤。
- 用四个升级标签组织叙事：独立动作、透明更稳、循环更顺、GIF/WebP 双格式。

## Notes

- 不调用新的图像或视频生成模型；优先使用已交付的动画 WebP 作为证据素材，场景运动由 HyperFrames 统一编排。
- 保留 V0.2.0 的视觉语言：轻快、可靠、可分享；避免做成企业后台或技术演示页。
