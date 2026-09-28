# DeepResearch海外电视市场调研报告生成系统

> 基于S4大模型Agent开发训练营的多智能体架构，对海外爬取数据进行深度调研报告生成。
> 6个Agent分工协作：ChiefArchitect(规划) → DataScout(数据采集) → DataAnalyst(分析) → CompetitorAnalyst(对手分析) → ReportWriter(撰写) → CriticMaster(审核)

## 核心设计原则

### 减少大模型幻觉的6项措施

1. **数据锚定**：所有分析基于DB中的真实数据，每个结论必须引用数据来源（表名+记录数+时间窗口）
2. **信源追溯**：每条数据点标注 `source: {table, site_code, captured_week}`，无源数据不采纳
3. **事实约束**：Agent只能从提取的结构化数据中得出结论，禁止"推测"无数据支撑的趋势
4. **对抗审核**：CriticMaster对每个结论做"是否存在幻觉"检查，标注 critical/major/minor
5. **数值校验**：价格、评分、产品数等数值字段在写入报告前与DB二次核对
6. **引用格式**：所有数据点用 `[来源:site_code, week]` 格式标注，便于人工追溯

## 6个Agent职责

| Agent | 职责 | 输入 | 输出 | 防幻觉机制 |
|-------|------|------|------|------------|
| ChiefArchitect | 问题分析、大纲规划、假设生成 | user query + DB统计 | outline, research_questions | 大纲必须映射到DB表 |
| DataScout | 数据采集、信源收集、信源评级 | research_questions | structured_data_points | 每条数据标注信源可信度0-1 |
| DataAnalyst | 数据提取、趋势识别、ECharts配置 | data_points | analysis_results + charts | 数值与DB二次核对 |
| CompetitorAnalyst | 对手分析、上市预警、活动预警 | spec_series + price + review | competitor_intel | 只基于已抓取数据推断 |
| ReportWriter | 报告撰写、内容整合、Markdown排版 | all_results | markdown_report | 每段标注数据来源 |
| CriticMaster | 对抗式审核、质量评分、幻觉检测 | report + raw_data | review_result | 逐条核对结论与数据 |

## 报告维度

1. **市场总览** — 区域/品牌/品类分布
2. **数据分析** — 价格区间、评分分布、规格趋势
3. **对手分析** — 各品牌产品线对比、规格差异、定价策略
4. **未来报告** — 基于spec_series新系列推断上市趋势
5. **其他厂家上市预警** — 新系列发现、新型号注册
6. **其他厂家活动预警** — 价格异常波动、促销信号
7. **数据缺口分析** — 还需抓取哪些信息（含FCC）

## 还需抓取的信息清单

### FCC认证信息（后续重点）
- FCC ID查询：`https://fccid.io/` — 按品牌/型号查认证文件
- 认证日期 → 推断上市时间窗口
- 认证类型（FCC Part 15 B/C/D/E）→ 推断产品类别
- 内部照片 → 硬件方案推测
- 技术规格书 → 补充官方未公开参数

### 当前数据缺口
| 缺口 | 说明 | 补充方式 |
|------|------|----------|
| 销量数据 | 当前无销量/排名数据 | 抓取Amazon BSR、kakaku排名 |
| 库存状态 | price_snapshot有in_stock但未充分利用 | 增量抓取库存变化 |
| 促销标记 | 无促销/折扣标记 | 抓取list_price vs price对比 |
| 上市日期 | 无明确上市日期字段 | 从spec_series.first_seen推断 |
| 退市信号 | 无退市标记 | 监控产品last_seen超过N周 |
| 社交媒体 | 无Twitter/Facebook舆情 | 接入社交API |
| 展会信息 | 无CES/IFA等展会数据 | 抓取展会官网 |
| 专利申请 | 无专利数据 | 抓取Google Patents |
| 供应链 | 无BOM/供应商数据 | FCC内部照片分析 |
