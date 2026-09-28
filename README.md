# 海外电视数据爬取项目

> **全量整理版 v3.0** | 2026-09-28 | 260站/5区域/30品牌 | 最新最全

---

## 项目总览

| 指标 | 数量 |
|------|------|
| 站点总数 | 260 |
| 产品总数 | 13,507 |
| SPEC系列 | 861 |
| SPEC规格行 | 146,853 |
| 价格快照 | 11,824 |
| 用户评价 | 84,095 |
| 评价摘要 | 2,615 |
| Adapter | 260站(全部加载OK) |
| LangGraph节点 | 7个(init/spec/retail/review/verify/report/retry) |

---

## 目录结构（单文件夹全量）

```
海外电视爬取项目/
├── README.md                          ← 本文件(项目总入口)
│
├── ① 主程序/                           ← 硬编码引擎(可复用，不需探索)
│   ├── overseas/                       Python包
│   │   ├── cli.py                      CLI入口
│   │   ├── scenarios.py                SPEC/RETAIL/网评 抓取引擎
│   │   ├── flow/                       LangGraph工作流引擎(7节点)
│   │   ├── report/                     DeepResearch报告引擎(6-Agent)
│   │   ├── sites/                      260站adapter(平铺结构)
│   │   │   ├── sony_jp/               adapter.py + schema.json
│   │   │   ├── kakaku_jp/
│   │   │   └── ... (260站)
│   │   └── ...                         其他核心模块
│   ├── pyproject.toml
│   └── requirements.txt
│
├── ② skill/                            ← 探索经验库(活文档)
│   └── overseas-schema-gen/
│       ├── SKILL.md                    统一入口+5分支分派
│       ├── spec/                       SPEC探索(36条cases+276行reference)
│       ├── retail/                     RETAIL探索(548条cases+1245行reference)
│       ├── ops/                        运维手册(520行)
│       ├── flow/                       工作流说明
│       └── report/                     报告说明
│
├── ③ data/                             ← 抓取数据(按区域拆DB)
│   ├── overseas.db                     主库(全量,111.8MB)
│   ├── merged/overseas.db               主库备份
│   ├── japan/overseas_japan.db         日本线(10.3MB)
│   ├── na/overseas_na.db               北美线(37.5MB)
│   ├── sa/overseas_sa.db               南美线(11.2MB)
│   ├── eu/overseas_eu.db               欧洲线(30.9MB)
│   ├── asia/overseas_asia.db           亚洲线(0.2MB)
│   └── crawl_state.json               LangGraph断点状态
│
├── ④ 工作流/                           ← LangGraph全自动抓取说明
│   ├── README.md                       7节点并行引擎使用说明
│   └── 报告引擎说明.md                 DeepResearch 6-Agent说明
│
└── ⑤ 文档/                             ← 抓取介绍+交付文档
    ├── 爬取进度.csv                     进度跟踪(GB18030)
    ├── 爬取进度_utf8.csv                进度跟踪(UTF-8)
    ├── 增量报告/
    │   ├── 日本线增量抓取报告.md
    │   ├── 北美线增量抓取报告.md
    │   ├── 南美线增量抓取报告.md
    │   ├── 欧洲线增量抓取报告.md
    │   └── 日本线LangGraph增量报告.md
    ├── 调研报告/
    │   └── 海外电视市场深度调研报告.md
    └── 技术方案/
        ├── LLM自动适配器技术方案.md
        ├── 零售线多国自动适配技术方案.md
        ├── AdapterSpec_schema规范.md
        └── RetailSpec_schema规范.md
```

---

## 各区域数据

| 区域 | 站点 | 有数据 | 产品 | 价格 | 网评 | SPEC系列 | SPEC行 | DB大小 |
|------|------|--------|------|------|------|----------|--------|--------|
| 日本 | 7 | 7 | 800 | 529 | 808 | 126 | 28,440 | 10.3MB |
| 北美 | 33 | 28 | 2,066 | 1,013 | 27,713 | 538 | 96,129 | 37.5MB |
| 南美 | 65 | 52 | 8,177 | 7,804 | 7,487 | 174 | 16,703 | 11.2MB |
| 欧洲 | 136 | 70 | 2,402 | 2,423 | 48,087 | 46 | 5,581 | 30.9MB |
| 亚洲 | 18 | 4 | 62 | 55 | 0 | 0 | 0 | 0.2MB |
| **合计** | **259** | **161** | **13,507** | **11,824** | **84,095** | **884** | **146,853** | **90.1MB** |

> ✅ 区域DB合计 == 主库（产品/价格/网评/SPEC行完全对齐）

---

## 核心架构

```
经验驱动自动探索 → 硬编码抓取闭环:

  skill(经验库·活文档) ──→ Agent自动探索 ──→ adapter.py+schema.json
  cases.jsonl(548条)      probe.py探测        ↓
  reference.md(1245行)    匹配playbook      硬编码引擎(可复用)
  playbook(结构型套路)    生成adapter        spec_crawl()
                          selfcheck校验      s2_monitor_known()
  ↑ 沉淀新经验            ↓                  s4_review_incremental()
  └──────────────────── 每完成一站追加case    ↓
                                              LangGraph全自动抓取
                                              → DeepResearch报告
```

---

## 快速开始

### 1. 探索新站 (Agent用skill)
```bash
cd 主程序
# SPEC站探测
py -3.12 ../skill/overseas-schema-gen/spec/probe.py --entry "<URL>" --code <code>
# RETAIL站探测
py -3.12 ../skill/overseas-schema-gen/retail/probe.py --site <code> --model "<型号>" --base-url "<站根>"
```

### 2. 单站抓取 (硬编码引擎)
```bash
cd 主程序
# SPEC抓取
python -m overseas.cli --db ../data/overseas.db spec-crawl --site sony_jp
# 价格监控
python -m overseas.cli --db ../data/overseas.db monitor --site kakaku_jp --from-db --limit 10
# 网评增量
python -m overseas.cli --db ../data/overseas.db reviews --site kakaku_jp --sku <sku> --pages 3
```

### 3. 全自动抓取 (LangGraph)
```bash
cd 主程序
# 单线
python -m overseas.flow.langgraph_crawl --lines japan --db ../data/overseas.db
# 全量5线
python -m overseas.flow.langgraph_crawl --lines japan,na,sa,eu,asia
# 断点续跑
python -m overseas.flow.langgraph_crawl --resume
```

### 4. 生成调研报告 (DeepResearch)
```bash
cd 主程序
python -m overseas.report.generate_report --db ../data/overseas.db --region all
```

---

## 环境要求

```
Python: C:\Users\likunyuan\AppData\Local\Programs\Python\Python312\python.exe
代理:   http://127.0.0.1:7877
环境变量:
  PYTHONIOENCODING=utf-8
  OVERSEAS_PROXY=http://127.0.0.1:7877
  OVERSEAS_HEADLESS=true
  OVERSEAS_USE_PROFILE=1
```

---

## 爬取进度

| 状态 | 站数 | 说明 |
|------|------|------|
| 可抓取丨已完成 | 153 | adapter+DB数据就绪 |
| 不可抓取丨需代理 | 92 | adapter就绪但WAF硬封(需住宅IP) |
| 未开始丨新增需求 | 30 | 新增站点 |
| 不可抓取丨未提供URL | 26 | 附件URL为空 |
| 未开始丨未抓取 | 14 | 政策认证站(非零售) |
| 其他 | 20 | 部分完成/校准/受限 |
| **总计** | **335** | — |
