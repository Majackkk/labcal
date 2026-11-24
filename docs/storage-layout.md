# 文件存储契约（0.9.0 / layout 2）

实验室专用Word模板在`data/laboratories/LAB_ID/templates/<role>/<SRC_ID>/`，原件主库存储仍为objects；模板选择及固定版本在本实验室meta中，源文件作为批次source固定引用。确认任务在processing/certificates/CONF_ID，完成的单台设备Word在outputs/certificates/CONF_ID。模板跨年度复用，业务任务明确年度批次，见[equipment-confirmation-forms](equipment-confirmation-forms.md)。

带明确equipment_key的新确认在任务目录固定business-context.json及其摘要；发布后该快照与Word、confirmation-result.json及适用B3副本一起进入同一CONF输出目录，delivery-manifest.json记录全部文件摘要。历史确认包不追写。上下文选择和问题跟进保存在该实验室lab.sqlite的meta版本记录中；用途和配置继续使用原表，不新增可编辑限值目录。

0.11.0在layout 2下增量增加data/references/processing/methods、实验室processing/methods与outputs/summaries。共享事实位于catalog.sqlite，实验室采用、用途及诊断位于lab.sqlite，原件对象不移动。汇总主表与历史版本的责任见[方法要求与用途诊断](method-requirements.md)。

## 分层与责任

本系统按实验室隔离资料，按年度、批次组织业务引用，再按业务线路分开输入、处理中间文件与输出。目录名采用稳定ID，不把客户名称、仪器型号或年度写进通用程序。

```text
项目根目录/
  labcal、src/、schemas/、templates/、rules/   通用程序与契约
  tests/、docs/、migrations/                 工程验证与维护说明
  inbox/official/                           人工补充共用官方参考资料的接收入口
  data/
    runtime/                                本机进程、日志；不属于实验室技术资料
    references/                             明确允许共用的参考数据库及原件对象
    laboratories/<LAB_ID>/
      lab.sqlite                            本实验室唯一业务事实库
      objects/<摘要前缀>/<摘要>.*              不可覆盖的原件对象库
      inputs/
        <CAM_ID>/
          calibration/UPLOAD-*/             方案编制输入暂存
          certificates/UPLOAD-*/            校准／检定报告输入暂存
          institution/UPLOAD-*/             机构范围附件输入暂存
        legacy-initial/                     早期入口样本（存在时）
      processing/
        calibration/RUN-*/                  方案生成过程清单
        certificates/INT-*/                 固定模型上下文及结果模板
        certificates/SELF-*/                证书自身检查、固定输入及原页证据
        certificates/METH-*/                方法用途诊断任务
        certificates/CONF-*/                正式确认分析记录、工作稿及排版检查
        methods/                            本实验室采用与用途接入过程
        maintenance/LAYOUT-*/               目录整理清单，含前后路径与文件摘要
        legacy/<任务目录>/                   历史运行／工程验证材料；保留原内容
      outputs/
        calibration/RUN-*/                  方案文档及固定生成清单
        certificates/RUN-*/                 诊断、逐点对照表、批注副本及清单
        certificates/CONF-*/                正式确认Word、固定记录及来源摘要
        summaries/                          实验室当前诊断Excel及RUN历史导出
      campaigns/<年度或undated>/<CAM_ID>/
        index.json、README.md                可重建的批次入口与固定资料引用
        校准方案/<名称>__<WB_ID>/             只读原件查阅副本与提取索引
        证书报告/                            原件档案、设备关联／待匹配投影
      inbox/UPLOAD-*/                       仅兼容历史任务已保存的接收路径
```

`inputs`是接收暂存，不是原件主库；提交成功后的可信字节由`objects`及数据库摘要管理。`processing`不保存另一套可编辑限值，OCR、诊断等固定快照在数据库中。`outputs`是由固定事实生成的版本化交付物，不反向解析为在用技术事实。原件、固定快照和导出不自动清理。

输入采用批次稳定ID，而不再复制年度层级。批次的年度和名称由`campaigns`管理；调整年度不会使任务路径失效。设备、方案版本和输出可被多个批次明确引用，因此输出按线路和稳定任务ID保存，年度批次索引给出实际输出相对路径，不复制成多份可编辑文件。没有直接证据的年度继续保留`undated`。

## 两条线路

方案线路：上传工作簿 → 原件入库 → 批次候选行／方案版本 → 具体用户确认（如有） → 固定基准 → 方案输出。

证书线路：上传报告 → 原件入库 → 固定提取／更正 → 证书自身检查 → 按需进行用途评价、正式结果确认或方案履约对照 → 固定输出与汇总。只有方案履约对照需要只读引用有效确认基准；独立用途确认不以校准方案为前提。当前顺序见[流程总览](workflow.md)。

两条线路共享实验室、设备及原件服务；证书侧不回写方案、MPE或确认状态。输入按`calibration`和`certificates`分区，输出分别落盘。机构附件是批次接入、实验室内按有效期与项目引用的证据，不因放入目录而技术有效。

## 程序与兼容规则

`adapters/layout.py`统一控制路径，业务代码不得另拼新的`inbox/runs/exports`写入路径。新实验室直接创建layout 2目录；既有实验室由显式整理命令迁移。HTTP上传必须先验证实验室和可写批次，下载必须验证任务清单及批次引用。

输出查找兼容`outputs/<线路>/<RUN_ID>`和旧`exports/<RUN_ID>`；出现同一任务多个目录时拒绝自动选择。拒绝路径穿越、线路冲突和迁移中的符号链接。旧上传区因为历史任务可能保存绝对接收路径而保留，后续上传只进入新路径，不静默改写旧任务记录。

```sh
./labcal organize --lab LAB_ID           # 只预览，不移动
./labcal organize --lab LAB_ID --apply   # 显式整理，任务空闲时运行
./labcal storage --lab LAB_ID --deep     # 核对原件、快照、批次投影及输出
```

整理前预检全部冲突，保存每个文件的摘要和路径映射，再逐目录重命名；移动或摘要核对失败会回移已移动目录。整理不改数据库业务记录或历史输出内容，只重建可重建的批次索引。中断后可用日志核对已移动目录；重复执行只迁移剩余旧目录，不合并、不覆盖。操作时应关闭工作台服务，避免其他进程新增任务；命令也拒绝已登记的运行中任务。

## 备份、扩展与边界

现有实验室备份覆盖所有实验室子目录以及共享参考库，包括新输入、中间文件和输出。恢复到空目录，旧布局和新布局均可读取。桌面入口、浏览器偏好、程序、外部OCR环境及模型权重需分别管理，不假称业务备份包含它们。

未设置自动删除、周期分析、历史趋势或异常影响功能。后续新增模块先定义数据契约及固定引用，再在`processing`或`outputs`增加明确线路；不得把客户资料放进通用模板、规则或程序目录。未来联网多人使用仍需另加身份权限、持久队列和一致性备份，目录隔离本身不等于租户安全。

## 聊天诊断任务（0.10.0）

`processing/certificates/SELF_ID`保存固定输入、按需原页图和提交记录；`outputs/certificates/RUN_ID`保存诊断与有定位问题的PDF副本。runs新增类型certificate_self_diagnosis，完整性检查按该批次及固定输出摘要校验，不改表结构。聊天输出不以HTML为入口。技能和程序契约见chat-self-diagnosis.md。
