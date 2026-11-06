# Label-Tools

本机运行的二维实例标注框架：浏览器描边、显式人工复核、原图与实例标签配对导出。

## 安装与启动

需要 Python 3.10 或更新版本。在包含实现代码的仓库分支中执行：

```bash
python -m pip install .
label-tools demo --output ../label-tools-demo --language zh
label-tools serve ../label-tools-demo --open
```

默认地址为 `http://127.0.0.1:8766`。端口被占用时使用 `--port 8767`。
浏览器只是界面，真正的标注保存在本机数据目录中；标注时保持服务进程运行。

## 导入自己的图片

```bash
label-tools init /path/to/images --output /path/to/workspace --resolution-nm 100 --target "目标完整外轮廓" --language zh
label-tools verify /path/to/workspace
label-tools serve /path/to/workspace --open
```

- 路径包含空格时加双引号。程序不覆盖已有输出目录，不改原始输入文件。
- 不知道像素尺寸时省略 `--resolution-nm`，界面会显示像素而非假定物理尺度。
- 支持 PNG、JPEG、单图 TIFF。灰度原图保留数据类型与像素值；高位深图仅对显示预览做窗口映射。
- 彩色图限 uint8 RGB；不支持带透明通道、调色板、三维堆栈或超过 1600 万像素的图。

## 描边与复核

1. 在多边形模式下逐点点击，画布获得焦点时按 Enter、双击或点起点闭合。
2. 编辑模式支持拖动顶点、双击边添加顶点、删除顶点。支持撤销重做、缩放和平移。
3. 同一视野有多个目标时分别描边。完成所有目标后点击“完成本视野”；无目标必须单独“确认无目标”。
4. 截断或不完整目标勾选对应选项。接触图像边缘的轮廓会自动标记为不完整。
5. 闭合轮廓自动保存，确认显示“已保存到本机”。未闭合的点仅暂存在当前浏览器。
6. 保存冲突时先下载草稿 JSON，再读取最新版本，不会自动覆盖另一个标签页的标注。

## 导出与训练

网页按钮或 `label-tools export /path/to/workspace` 导出已完成图和明确确认的阴性图。
输出包括原图 TIFF、uint16 实例 TIFF、不完整目标 ignore mask、多边形 JSON 和元数据。

**未标注不等于阴性。** 草稿不进入训练导出。`labels/` 工作目录可能包含草稿，不能直接把整个目录用于训练。
每张图内 0 表示背景，正整数表示独立实例；`ignore_masks == 255` 表示不完整目标。
训练适配器必须处理 ignore mask，不能把不完整目标置零后当作背景。

框架可记录空间重叠关系，但不会凭空建立训练/验证独立性。重叠视野应放在同一划分中，样本或动物层面的独立性需要额外检查。

## 范围与数据安全

多边形模式用于二维、单目标类别、多实例描边，不支持多边形孔洞或实例像素重叠。
可选中心点模式支持已有前景的画笔修正和粘连实例拆分。不包含三维标注、模型训练和多人在线协作。
仅监听本机，不要直接暴露到公网。

公开仓库只包含代码、文档和合成示例，不包含真实电镜图、人工标注、模型权重、私人路径或服务器凭据。
代码采用 MIT 许可证；图标许可见仓库第三方说明。

## 中心点实例修正

```bash
python -m pip install ".[centers]"
label-tools centers-demo --output ../center-demo
label-tools centers-serve ../center-demo --open
```

默认端口为 `8772`。导入灰度原图、二值前景和可选实例 mask：

```bash
label-tools centers-init /path/to/images --masks /path/to/foreground --instances /path/to/instances --output /path/to/workspace --resolution-nm 20 --target "Objects"
```

- 原图与 mask 按文件名主干配对，形状必须一致；前景只能使用 0/1 或 0/255。
- 省略 `--instances` 时按前景连通域初始化；不知道像素尺度时省略 `--resolution-nm`。
- 在同一个粘连区域中给不同目标各标一个中心点，程序进行局部分水岭拆分，保留已有独立实例。
- 点位在前景之外会提示，不会偷偷吸附。漏分割区域需要先用画笔补前景。
- 编辑后自动保存并更新预览；点击导出后才会生成训练 TIFF，不会改写输入数据。
- 输出包括无损原图、uint32 实例、前景、边缘、内部、三类标签及 ignore mask。
- **标了中心点不等于完整人工真值。** 未复核的默认严格训练标签全部忽略；完整检查前景和实例后才能勾选已复核。
- 接触图像边界的实例及明确的硬忽略区域仍然保留 ignore。计算生成的边缘不能等同于人工描绘的生物膜。

中心点界面目前为英文。完整格式、算法边界和导出语义见 [center-points.md](center-points.md)。
