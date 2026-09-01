# Motion Sticker Pack v0.2.1

发布日期：2026-08-30；xAI 实测与修复补充：2026-09-01

v0.2.1 是一次审计闭环版本，重点处理任务级 Provider 选择、xAI 配置漂移、fallback 异构参数、媒体真实性检查和覆盖写入安全。

## 主要更新

- `prepare_workflow.py` 新增 `--provider`、有序 `--fallback-provider`、`--provider-duration`、`--provider-resolution` 和显式 fallback 开关。
- `video-task.json` 新增 `provider_chain` 与 `provider_execution`；AI SDK 和命令适配器都读取当前 attempt 自己的时长与分辨率。
- xAI model 改从 Provider 配置读取，分辨率改从任务快照读取；移除 `XAI_VIDEO_MODEL`、`XAI_VIDEO_RESOLUTION`、`GROK_VIDEO_RESOLUTION` 漂移入口。
- route 绑定输入图、layout、prompt、审批状态和生产配置内容哈希，修改依赖后旧 route 拒绝执行。
- 外部视频统一经过本地可解码性与真实 Alpha 探测，opaque 视频才进入色键背景和网格安全检查。
- xAI 色键任务在上传前把透明输入确定性铺为精确 `#00FF00`，并清除 Alpha≤8 的近透明噪声；提示词同步声明逐帧固定色键合同。
- Provider 成功但本地 QC 拒绝时，结果文件同步写入 `status: rejected`、`executor_status`、`qc_status` 和错误原因。
- 主处理、关键帧、独立贴纸、prompt-only 与最终组装链路统一拒绝输入/输出目录重叠，堵住 `--overwrite` 删除源素材。
- 静图来源 CLI 强制参考图与文字定义二选一；参考图在实际调用前复核 SHA-256；非 3×3 提示不再残留“九宫格”。
- `gif.max_alpha_coverage_delta` 在 Python 运行时与 Schema 中都限制为 0–1。

## 验证

- Python：133 项测试通过。
- Node：14 项测试通过。
- `npm audit`：0 个已知漏洞。
- 已在用户明确授权后执行一次新的 `xai-direct` 付费 attempt，没有自动重试。返回 3.041667 秒、960×960、73 个原生帧的视频，背景硬 QC 通过，最终 9/9 单元成功导出。
- 实测响应头报告 `zero_data_retention: false`；隐私与留存仍以实际账户策略为准。

---

## Motion Sticker Pack v0.2.0

发布日期：2026-08-28

v0.2.0 把 `motion-sticker-pack` 从“能生成和切分动态贴纸”推进到一条更可复现、可审计、少重复文件的交付链路。它是向后兼容的功能版本，但包含几项默认行为变化，升级前请阅读“升级提醒”。

## 主要更新

### 角色既可来自参考图，也可来自文字

- 参考图路线继续绑定角色身份并等待静态图确认。
- 无参考图时可直接用文字定义角色，并一次生成完整表情图板。
- 静图请求统一采用透明优先策略；只有本地像素检查确认 Alpha 不可用时，才允许一次精确 `#00FF00` 兜底。

### 视频请求与成品规格统一

- 新增 `sticker-production.json` 配置合同与 JSON Schema。
- Grok Build 默认请求 6 秒；xAI Videos 直连默认请求 3 秒。
- 两条路线统一输出 240×240、8 fps，GIF 最多 192 色。
- Grok 完整 6 秒结果保留在根版本，同时从相同源视频的初始 24 个采样帧生成 3 秒版本；不会加速、倒放或再次调用付费 Provider。
- 先试产配置格（默认 `01`），通过编码和 1 MiB GIF 预算检查后才处理整组。

### 后处理稳定性与透明质量

- 对每个原生帧执行固定色键背景检查，再进行连续 Alpha 抠像。
- 先在整帧上抠像，再做组件归属和跨格恢复，降低接缝切断与角色串格风险。
- 使用跨时间联合边界框和固定粘贴位置，避免逐帧裁切导致画面跳动。
- 固定镜头默认关闭逐帧整数全局注册；`--registration auto` 仅用于确认存在真实镜头漂移的素材。
- WebP 和 GIF 编码后重新解码逐帧检查，并记录 Alpha 覆盖、边界触碰和静止段质心位移。

### 更干净的交付目录

- 被接受的 Grok attempt 直接提升为 `grok-build-local.mp4`，不再额外复制一份相同视频。
- `assemble_delivery.py --cleanup-media-dir` 在最终 ZIP 成功后删除编码中间目录。
- 正常任务只保留一个规范源视频和一个 `delivered/` 成品目录。
- 最终 ZIP 不再嵌套 `3s/sticker-pack.zip`，但仍包含 6 秒根版本、3 秒媒体和完整审计报告。

### 路由、隐私与安全合同

- Provider 请求时长、能力、区域、凭证别名与可选环境变量都经过合同校验。
- Grok Build 保持单次生成策略，并区分个人 `/privacy` Opt in 与团队 ZDR 存储配置。
- 失败轮询可凭请求 ID 恢复同一远程任务，避免重复提交与重复计费。
- 输出目录、归档名、输入大小和原生帧数继续采用 fail-closed 检查。

## 升级提醒

1. 最终交付目录现在以 `works/<character>/delivered/` 为准；`output/` 是可清理的编码中间目录。
2. 固定镜头素材默认不再启用全局注册。如确有镜头漂移，显式传入 `--registration auto`。
3. Grok 的 3 秒版本来自完整 6 秒源视频的前 24 个采样帧，不是重定时循环。
4. 外部 Provider 的默认参数应从 `assets/sticker-production.default.json` 派生，不要在多个脚本中分别硬编码。
5. 使用内置 AI SDK 路由时需要 Node 22+，升级后先运行 `npm ci`。

## 验证结果

- Python：120 项测试通过。
- Node：14 项测试通过。
- Python 脚本编译检查通过。
- `npm audit --audit-level=high`：0 个已知漏洞。
- README 中英文版的本地链接检查通过。

这些结果覆盖本地合同和模拟 Provider 请求，不代表远端账户必然具备配额、模型权限或 Grok 隐私授权。真实付费生成仍需一次显式授权的单任务验证。

## 已知边界

- 视频模型自身持续运动、身份漂移或动作不回位，不能仅靠后处理完全修复。
- 整板视频仍可能出现不可分离的跨格融合；受影响格会被 withheld，而不是静默输出坏文件。
- Telegram WebM、Discord APNG、微信投稿限制等平台专用导出仍未自动化。
- 自由排版图板仍需要人工确认网格或显式 `--override`。

## 升级

```bash
npx skills update motion-sticker-pack -g -y
```

源码开发环境：

```bash
python3 -m pip install -r requirements.txt
npm ci
python3 -m unittest discover -s tests
npm test
```
