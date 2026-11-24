# 本地 OCR 接入与使用

工作台不依赖 Codex 运行。桌面入口启动本机程序，需要模型时才启动既有 PaddleOCR 虚拟环境；报告不发送到云服务。本模块负责文字和表格提取，不提供大模型技术审查或自动技术批准。

## 工作台操作

证书诊断页的“OCR 模型”作用于接下来导入或重新识别的报告：

- 自动（文字优先）：保留可读 PDF 的文字层；文字不足的扫描页调用本地 PaddleOCR-VL-1.6，未安装时使用 Tesseract。
- Tesseract · 快速识别：所有页面使用 Tesseract。
- PaddleOCR-VL-1.6 · 表格增强：所有页面使用 PaddleOCR-VL，适合文字层错乱或需要重新识别复杂表格的报告。当前采用 CPU 推理，每页可能需数分钟。

选择已接入报告，再点“重新识别并诊断”，从该批次固定原件建立新的提取快照并重新生成基础诊断。旧提取、旧审查、用户确认方案及原件均保留。浏览器记住上次选择的OCR模型；切换选项本身不会立即处理报告，“开始诊断”使用已有识别结果，“重新识别并诊断”才重新运行所选OCR。自动识别不会把候选方案提升为已确认基准。

“归档与识别方式”显示当前快照实际使用的引擎；模型失败或超时时显示提示，并尝试 Tesseract。正在运行的任务显示真实识别阶段与页码，扫描动画仅为等待反馈，不代表技术核验进度。

## 运行边界

核心程序保持标准库依赖。可选模型由 `paddle_worker.py` 在专用 Python 中运行，显式读取本地 PP-DocLayoutV3 与 PaddleOCR-VL-1.6，禁用模型源检查和 Hugging Face 在线获取。不安装或升级用户现有模型环境。

默认发现 `~/.local/share/ocr-runtime/paddleocr-vl-1.6/.venv/bin/python` 和 `~/.paddlex/official_models`。可通过 `LABCAL_PADDLE_PYTHON`、`LABCAL_PADDLE_MODELS` 覆盖运行位置；`LABCAL_OCR_ENGINE` 可设为 auto、tesseract 或 paddle。命令行 `./labcal ocr status` 仅检查文件存在，不把存在误报为推理验证成功。

模型每份报告加载一次，多页复用；跨导入进程的本机文件锁限制同时只运行一个模型。等待锁最多60秒，初始化最多120秒，每页推理最多300秒，失败后本份报告余页使用备用方式；工作台后台进程每次等待识别消息上限600秒。工作台应在无识别任务时关闭，避免中断未完成任务。

Paddle 表格输出按显式 rowspan/colspan 展开，并保存原始 HTML；只规范化 ±、≤ 等明确排版符号，不修正数值、不推断缺失内容。不完整表格不转成结构化结果。坐标为原页内容块范围，不能冒充逐字符或逐单元格精准定位。所有结果仍为候选，关键数字、符号和上下文需回看原页。

## CLI

```sh
./labcal ocr status
./labcal certificate add --lab LAB_ID --source-id SRC_ID --ocr-engine paddle --force-ocr
./labcal certificate add --lab LAB_ID --source-id SRC_ID --ocr-engine tesseract
```

新工作台业务从明确实验室及批次接入。CLI 的 certificate 命令为实验室级已有入口，不自行猜测批次；新提取需通过 campaign attach 纳入指定批次。

## 实测边界

2026-10-15，Apple M1 Pro / 16GB，既有模型环境，取 LAB_KB 已接入万能试验机证书第4页，144dpi图像，对相同图像分别识别：Tesseract约1.96秒；Paddle初始化约21.86秒、识别约195.44秒。Paddle恢复3张表格及显式±/≤；该样本中 Tesseract 将±读为+、≤读为<。这是单页工程验证，不能外推整体准确率或生产吞吐量，也不构成设备符合用途结论。原始比对材料保存在该实验室 runs 下。

Apple Silicon 的 MLX 加速属于后续可选优化；当前未部署加速服务，不宣称已经使用 GPU。

官方依据：[Paddle Apple Silicon 指南](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.en.md)、[Tesseract 图像与表格识别说明](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)。
