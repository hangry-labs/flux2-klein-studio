<p align="center"><a href="../README.md">English</a> · <a href="../nb/README.md">Norsk bokmål</a> · <a href="../pl/README.md">Polski</a> · <a href="../ja/README.md">日本語</a> · <strong>简体中文</strong> · <a href="../es/README.md">Español</a></p>

# FLUX.2 Klein Studio · 文档

本简体中文入门指南介绍常用工作流程。详细 API 架构、测试报告、适配器参数、发布规则与验证历史仍以英文维护。下方链接已标明语言。

[项目概览](../../README.zh.md) · [Docker Hub](../dockerhub.zh.md).

## 简明使用指南

**生成：**描述主体、场景和光照，建议从四步开始。宽高必须在 64–1920 之间，且为 16 的倍数。示例按钮只填入描述，不改变其他设置。增加步数不一定改善结果。

**参考图：**上传一至四张图片，或从图库选择结果。每个文件最大 16 MiB。系统会校正方向、将透明部分合成到白色背景，并将每张参考图限制到约一百万像素，尺寸对齐到 16 的倍数。参考图顺序有意义。基础模型支持多图，编辑适配器只使用一张。

**绿区填充：**用 #00FF00 涂画，或增大画布添加绿色边缘。固定填充指令始终包含，内容描述可留空，小标记可能残留。**红框放大：**矩形保持输出比例，内部拖动可移动，外部重选可替换。使用结果可继续生成更近的特写。

**图库：**生成前选择保存。已保存修改会保留所有处理后的参考图及生成参数，但不保留原始上传文件或画笔历史。重新打开参数会关闭随机种子。PNG 和 WebP 保留像素，JPEG 有损。确认删除后会移除该任务的全部本地文件。

**系统：**默认空闲时间为 300 秒。设为零会在生成后卸载模型。“释放未使用显存”保留权重，“立即卸载”也会移除权重。尺寸选择影响新的 HTTP/MCP 请求及工具公告，不限制本地画布尺寸。GPU 图表仅在“系统”可见时采样。

## 文件与显存

将 `flux2_klein_studio_data` 数据卷挂载到 `/app/persistent`，以保留模型、缓存、设置和已保存结果。替换容器时请复用同一数据卷。未选择保存到图库时，结果为临时文件，会在重启或被移出 100 项预览保留范围时删除。已保存结果仅在主动删除时移除。删除数据卷可能同时丢失缓存和已保存结果。

所有模型组件都驻留在 GPU，不向 CPU 或磁盘卸载。默认在最后一个任务结束后保留权重五分钟。未使用的工作显存会自动释放，也可在“系统”中立即释放。尺寸表是纯文生图的参考测量，不保证每种编辑任务都能装入显存。

## 应用与智能体

图片 API 提供 `/v1/images/generations` 和 `/v1/images/edits`，模型名为 `flux2-klein-4b`。MCP 使用 `/mcp/` 上的 Streamable HTTP；智能体应先调用 `skill`。`square_big` 等尺寸名及适配器 ID 保持不变。API 支持 `n=1..16`；请求在共享 FIFO 队列中等待，GPU 每次生成一张图片。下例采用 Bash 引号语法。

```bash
curl http://localhost:7860/v1/images/generations -H "Content-Type: application/json" -d '{"model":"flux2-klein-4b","prompt":"A glass robot tending orchids in a sunlit greenhouse","size":"square_big","seed":12345}'
```

## 详细英文文档

- [浏览器 Studio（英文）](../studio.md)
- [HTTP 与 MCP（英文）](../agent-api.md)
- [适配器与固定指令（英文）](../lora.md)
- [测量方法与结果（英文）](../benchmarks.md)
- [模型功能与限制（英文）](../model-capabilities.md)
- [测试与验证历史（英文）](../validation.md)
- [容器发布（英文）](../RELEASING.md)
- [多参考图显存测量（英文）](../../benchmarks/multiref/README.md)

## 隐私与条款

图片生成在本地执行。只有主动选择保存到图库时，才会持久保留结果和提示词；首次下载需要网络。请将端口限制在 localhost，或使用合适的认证代理。`STUDIO_API_KEY` 保护 `/v1` 和 `/mcp`，不保护本地浏览器 API。请使用获得必要授权的参考图，不要将生成图片当作真实记录。

应用代码采用 Apache-2.0。模型、适配器和依赖保留各自条款。请参阅 LICENSE、NOTICE 和 THIRD_PARTY.md；应用许可不取代组件条款。

[LICENSE](../../LICENSE) · [NOTICE](../../NOTICE) · [THIRD_PARTY.md](../../THIRD_PARTY.md).
