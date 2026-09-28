# 工作流引擎说明

## LangGraph全自动抓取 (7节点)

位置: `主程序/overseas/flow/`

### 节点流程
```
init → spec → retail → review → verify → report → end
                                              ↓
                                           retry (最多2次) → blocked
```

### 快速开始
```bash
cd 主程序
# 单线
python -m overseas.flow.langgraph_crawl --lines japan --db ../data/overseas.db
# 全量5线
python -m overseas.flow.langgraph_crawl --lines japan,na,sa,eu,asia
# 断点续跑
python -m overseas.flow.langgraph_crawl --resume
```

### 并行配置
- SPEC并行: 2站同时
- RETAIL并行: 3站同时
- 每站独立子进程(避免SQLite线程冲突)
- SPEC超时: 600秒/站
- RETAIL超时: 90秒/站

## DeepResearch报告生成 (6-Agent)

位置: `主程序/overseas/report/`

### Agent协作
```
ChiefArchitect(总架构) → DataScout(数据收集) → DataAnalyst(数据分析)
→ CompetitorAnalyst(对手分析) → ReportWriter(报告撰写) → CriticMaster(对抗审核)
```

### 快速开始
```bash
cd 主程序
python -m overseas.report.generate_report --db ../data/overseas.db --region all
```

### 防幻觉6措施
1. 数据锚定 — 所有结论必须引用DB数据
2. 信源追溯 — 每条数据标注[来源:Sx]
3. 事实约束 — 无数据支撑的结论标为"待验证"
4. 对抗审核 — CriticMaster检测无信源结论
5. 数值校验 — 价格/评分等数值必须来自DB
6. 引用格式 — 统一[来源:Sx]格式
