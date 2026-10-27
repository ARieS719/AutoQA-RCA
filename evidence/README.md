# 证据目录（Evidence）

本目录存放**随仓库提交**的实测证据（原始报告位于 `reports/` 与 `data_output/`，已被 .gitignore 排除）。

| 文件 | 内容 | 说明 |
|---|---|---|
| [evaluation_llm_20261027.md](evaluation_llm_20261027.md) | 评测基准报告（LLM 模式，9 案例 ×2 轮 = 18 次） | 五指标：分诊 100% / 根因 100% / 责任 94.4% / RAG 100% / 仲裁 100% |
| [ui_batch_baseline_20261027.md](ui_batch_baseline_20261027.md) | UI 批量实测·基线套件（5 任务 ×3 轮 = 15 次） | 成功率 100%，0 基础设施异常 |
| [ui_batch_deep_20261027.md](ui_batch_deep_20261027.md) | UI 批量实测·深链路套件（4 任务 ×3 轮 = 12 次） | 成功率 75%（roundtrip_nav 0/3，已知待优化项，见 README 路线图） |
| [rca_case5_arbitration.md](rca_case5_arbitration.md) | RCA 报告样例：case_5「链路健康但 UI 崩溃」 | 展示**双向仲裁**：轨B 证链路无责 → 责任回摆座舱应用层 |
| [ground_truth_case5.md](ground_truth_case5.md) | case_5 答案卡（ground truth） | 评测基准的判分依据样例；先读 RCA 报告再对答案 |

> 复现方式（任选）：
> - 评测：`python -m rca_engine.cli evaluate --repeat 2`
> - 实测：`python -m ui_agent.batch_runner --suite default --repeats 3 --rca-on-failure`
> - 所有数字均为"有效轮次口径"（网关限流等基础设施异常单列，不计入指标）。
