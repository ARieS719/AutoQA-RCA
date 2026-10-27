# AutoQA-RCA：车联网「测试—诊断」AI 自治闭环中台

> 由 `AgentQA_Project`（纯视觉 UI 自动化）与 `Vehicle_RCA_Agent`（跨域日志多智能体溯源）
> 合并重构而来：**一个执行引擎 + 一个编排引擎 + 双层定责（座舱 UI 域 / 车联网链路域）**。
> 全部指标可点击 [evidence/](evidence/README.md) 目录中的原始报告核验。

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![LangGraph](https://img.shields.io/badge/LangGraph-StateGraph-green)
![CV](https://img.shields.io/badge/CV-OpenCV-orange)
![RAG](https://img.shields.io/badge/VectorDB-Chroma-purple)

---

## 1. 一句话定位

自然语言用例 → 纯视觉 Agent 在车机端自主执行与独立断言 → 失败现场**按证据门自动分诊**：
纯界面问题走**轨A**（视觉 + Logcat 切片），车联功能问题开启**轨B**（MQTT/T-BOX/CAN 三域对齐），
双轨互为仲裁——链路无责时责任回摆座舱应用层。从"发现缺陷"到"根因定责"全程无人值守。

```
自然语言用例
   │
   ▼
[UI 视觉 Agent] ReAct + SoM：截图→打标→推理→点击/滑动/拖动→自报DONE→独立断言 ──失败──┐
   │                                                               │ 统一事件模型 Incident
   ├─ 通过 → 性能基准（录屏帧差：冷启动/转场耗时）                    │ {case_id, 时间窗, 证据包}
   ▼                                                               ▼
[测试报告] ◄──────────────────────────────────────────── [incident_bridge]
                                    │  采集证据包 → 失败分诊 Triage（元数据 + 证据门）
                 ┌──────────────────┴──────────────────────┐
                 ▼                                          ▼
   【轨A｜UI/座舱域 RCA】                        【轨B｜车联网链路域 RCA】
   截图序列 + 步迹 + logcat 切片                  MQTT/T-BOX/CAN 按时间窗切片
   崩溃/ANR/渲染签名 + VLM 截图对比              时间对齐降噪 → 提纯 → CAN 协议解码
   + RAG 座舱缺陷库                              → RAG 车联缺陷库 → 定责
                 └──────── 双向仲裁（链路无责 → 回摆座舱）◄─────┘
                                    ▼
        [根因 + 修复建议] 按域打标回流知识库（接口就绪，人工确认后写入）
```

> **口径对齐**：轨B 绝不因"轨A 没查到"而启动，只由证据门触发
> （用例元数据声明链路依赖 / 失败窗口日志出现链路异常签名）。
> 纯 UI 失败永不查询 CAN 报文——不存在强行结合。

---

## 2. 实测指标（点击文件名查看原始证据）

### 2.1 诊断评测基准（9 案例 ×2 轮 = 18 次，LLM 模式）

[📄 完整评测报告](evidence/evaluation_llm_20261027.md)

| 指标 | 命中率 |
|---|---|
| 分诊准确率 | **100%**（18/18） |
| 根因命中率 | **100%**（18/18） |
| 责任方命中率 | **94.4%**（17/18，唯一未命中为措辞差异，见报告口径说明） |
| RAG 历史缺陷命中率 | **100%**（18/18） |
| 仲裁正确率 | **100%**（18/18，含 case_5 双向仲裁回摆验证） |

### 2.2 模拟器批量 UI 实测（Android Automotive with Google APIs 模拟器）

[📄 基线套件报告](evidence/ui_batch_baseline_20261027.md)｜[📄 深链路套件报告](evidence/ui_batch_deep_20261027.md)

| 套件 | 结果 |
|---|---|
| 基线（5 任务 ×3 轮 = 15 次） | **成功率 100%**，0 基础设施异常，单轮平均约 25s |
| 深链路（4 任务 ×3 轮 = 12 次） | **成功率 75%**：滑块拖动/二级导航/多应用切换 9/9，BACK 往返导航 0/3（已知待优化，见路线图） |

### 2.3 确定性数据管道（降噪卖点的数字出口）

60 秒混沌案例实测：**1884 行**（MQTT+Logcat+CAN 三源异构）→ **623 行**核心上下文，
降噪率 66.9%、黑名单噪音剔除率 100%、LLM 输入上下文压缩约 3×。

### 2.4 双向仲裁样例（最能体现"双轨"价值的报告）

[📄 case_5：链路健康但 UI 崩溃的仲裁报告](evidence/rca_case5_arbitration.md) + [📄 对应答案卡](evidence/ground_truth_case5.md)

---

## 3. 快速上手（离线可跑，不需要真机）

```bash
pip install -r requirements.txt                # 建议独立 venv，Python 3.9+
python -m mock_data.fault_injection             # 生成 6 个故障注入案例（含答案卡）
python -m mock_data.generate_logs               # 经典 3 案例（含答案卡）
python -m rca_engine.cli cases                  # 查看案例清单
python -m rca_engine.cli diagnose --case data_output/demo_cases/case_1_ui_crash --offline   # 离线确定性诊断
pytest -q                                       # 26 个离线测试
```

配置 `.env`（复制 `.env.example`）后：

```bash
python -m rca_engine.cli diagnose --case data_output/demo_cases/case_5_ui_but_link_ok   # 完整 AI 诊断 + 成本面板
python -m rca_engine.cli evaluate --repeat 2                                            # 五指标自动评测
streamlit run app.py                           # 统一控制台
```

真机 UI 实测（Android Studio 模拟器 / ADB 设备）：

```bash
python -m ui_agent.batch_runner --suite default --repeats 3 --rca-on-failure   # 基线
python -m ui_agent.batch_runner --suite deep --repeats 3 --rca-on-failure      # 深链路
```

---

## 4. 故障注入演示集（每个案例目录含 ground_truth.md 答案卡）

| 案例 | 域 | 注入故障 | 期望轨道 |
|---|---|---|---|
| case_1_ui_crash | ui | 音乐 App 音频焦点数组越界崩溃 | 仅轨A（证据门拦截轨B） |
| case_2_can_congestion | telematics | ECM 心跳风暴抢占 CAN 总线 | 仅轨B；RAG 命中 Jira-8832 |
| case_3_network_offline | telematics | 弱网 MQTT socket 死锁 | 仅轨B；RAG 命中 Jira-9022 |
| case_4_hardware_fault | telematics | BCM 反馈 0xEE 硬件错误码 | 仅轨B；RAG 命中 Jira-9155 |
| case_5_ui_but_link_ok | mixed | 链路全通但座舱状态同步 NPE 崩溃 | 双轨 + **反向仲裁**（责任回摆座舱） |
| case_6_field_noisy | telematics | 未知诊断报文 0x7E8 洪泛 + 脏数据 | 仅轨B；坏行被计数、未知 ID 诚实降级 |

---

## 5. 工程结构

```
AutoQA_RCA/
├─ app.py                     # 统一 Streamlit 控制台（RCA / UI测试 / 性能 / 归档）
├─ config/settings.py         # 统一配置中心（模型、超时、黑名单、证据门签名、单价）
├─ ui_agent/                  # 视觉执行引擎
│  ├─ agent_engine.py         # ReAct 循环：历史记忆 + no-op 检测 + 熔断 + 独立断言双闸门
│  ├─ som_marker.py           # SoM：RETR_EXTERNAL + NMS + 完整包围框（框内拖动）
│  ├─ vlm_client.py           # VLM 网关：指数退避重试 + 结构化输出解析
│  ├─ adb_controller.py       # ADB：唯一化截图路径 + 录屏守护 + logcat 转储
│  ├─ performance_analyzer.py # 帧差性能引擎（巴氏距离 + nan 兜底）
│  ├─ step_recorder.py        # 步骤证据包（失败即产出 incident 原料）
│  └─ batch_runner.py         # 批量实测：成功率统计 + 基建异常单列 + 失败喂回 RCA
├─ rca_engine/                # 双轨定责引擎
│  ├─ graph_pipeline.py       # LangGraph StateGraph（双轨 + 条件边 + 双向仲裁 + 顺序降级）
│  ├─ evidence/               # 证据适配器：telematics（三域对齐）/ ui（截图+签名）
│  ├─ dbc_tool.py             # CAN 解码：cantools 真实 .dbc 优先，内置字典兜底
│  ├─ rag_store.py            # Chroma 持久化 + 按域过滤 + 硬特征/向量混合检索 + 增量写入
│  ├─ report_writer.py        # JSON + Markdown 双产物报告（含成本面板）
│  ├─ evaluate.py             # 评测基准：AI 判定 vs ground_truth 五指标比对
│  ├─ cost_tracker.py         # Token/成本计量（含降噪节省估算）
│  └─ cli.py                  # 命令行入口（diagnose / cases / evaluate）
├─ incident_bridge/           # 统一事件模型 + 失败分诊（证据门）
├─ mock_data/                 # 故障注入 + 真实日志接入（ingest_real_case）
├─ knowledge_base/history_bugs.txt   # 缺陷知识库（按域打标）
├─ evidence/                  # ★ 实测证据归档（随仓库提交，README 可点击核验）
├─ tests/                     # 26 个离线 pytest 用例
└─ .github/workflows/ci.yml   # CI：故障注入 → 离线冒烟 → 评测 → pytest
```

---

## 6. 关键技术点（面试自检清单）

- **SoM 零坐标幻觉**：VLM 只输出数字 ID，OpenCV 映射像素中心 → 物理点击无坐标漂移。
- **独立断言双闸门**：AI 自报 DONE 不算数，另截一张图用专用断言 prompt 判 TRUE/FALSE——实测拦截过 AI 的虚假完成声明。
- **no-op 检测**：动作前后帧差对比，无效操作立即注入反馈并加速换策略；连续滑动无效/死循环自动熔断。
- **框内拖动**：SoM 暴露完整包围框，SWIPE_LEFT/RIGHT 支持滑块/开关类控件（实测解决"点中心=设 50%"问题）。
- **证据门 + 双向仲裁**：轨B 由可解释规则触发；链路无责时责任回摆座舱应用层，终结跨域扯皮。
- **确定性管道前置**：时间戳归一/全局排序/规则降噪走代码毫秒级完成，LLM 只做认知智能。
- **混合检索 RAG**：CAN ID/关键术语硬命中置顶 + 向量召回兜底，按域过滤防张冠李戴。
- **可离线可复现**：26 个 pytest 用例 + CI；评测/实测指标均可重跑复现。

## 7. FAQ（面试高频追问的标准答案）

- **Q：自己出题自己给答案合适吗？** 这是标准的合成基准方法论：题目按场景注入、答案卡独立封存、程序自动比对，且带对抗性设计（噪音/干扰链路/未知 ID/脏数据）。它验证机制与稳定性；真实数据接入通道（ingest_real_case）已就绪，下一步用脱敏真实日志补验证。
- **Q：能接入企业业务吗？** 数据驱动部分直接可接（换真实日志即跑、.dbc 换字典、知识库换 Jira 导出）；适配点为日志格式解析器、LLM 网关地址与合规部署。
- **Q：能接其他知识库吗？** RAGStore 是通用接口（解析 → Chroma 持久化 → 按域检索 → 增量写入），换任何文档/缺陷库只需加一个解析函数。
- **Q：AI 能分析完塞回知识库吗？** 接口就绪（add_record），但刻意保留"人工确认后回写"关卡——全自动回写会把未确认结论写进知识库污染检索，这是工程上的主动取舍。

## 8. 路线图

- [x] Phase 1 闭环打通：incident_bridge + 双轨仲裁 + 故障注入演示
- [x] Phase 2 可信度修复：P0/P1 全修（半成品合并/密钥/名不副实/状态污染/RAG 重建）
- [x] P0：自动评测基准（五指标）+ 真实数据接入工具 + 场域仿真案例
- [x] P1：模拟器批量实测（成功率统计 + 失败喂回 RCA）+ Token/成本面板
- [x] P2（部分）：GitHub Actions CI；实测证据归档（evidence/）
- [ ] 待优化：深链路 roundtrip_nav（BACK 往返导航）0/3——步数上限与 BACK 语义适配
- [ ] 待做：报告"人工确认后一键回写知识库"（知识飞轮闭环，接口已就绪）
- [ ] 待做：真实台架/实车脱敏日志跑通记录（ingest_real_case 接入）

## License

MIT
