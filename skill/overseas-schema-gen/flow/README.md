# LangGraph全自动流程化海外爬取引擎

## 概述

将海外250站爬取全流程改造为LangGraph编排引擎，每个功能节点封装为skill，支持断点续跑、失败重试、边界条件控制。

## 目录结构

```
overseas/flow/
├── __init__.py              # 包入口
├── state.py                 # CrawlState状态定义
├── edges.py                 # 条件路由逻辑
├── langgraph_crawl.py       # 主编排引擎(CLI入口)
└── nodes/
    ├── __init__.py
    ├── init.py              # 初始化节点(备份DB/加载站点/磁盘检查)
    ├── spec_crawl.py        # SPEC抓取节点(spec_crawl)
    ├── retail_monitor.py    # RETAIL价格监控节点(s2_monitor_known)
    ├── review_incremental.py # 网评增量节点(s4_review_incremental)
    ├── verify.py            # 验证节点(DB增量对比)
    ├── report.py            # 报告节点(自动生成markdown)
    └── retry.py             # 重试节点(失败站重试/limit减半)
```

## 流程图

```
START → init → spec → retail → review → verify → report → END
                     ↓失败              ↓不足
                   retry ─────────→ review
```

## 节点skill

| 节点 | 功能 | 封装的现有函数 | 边界条件 |
|------|------|---------------|----------|
| init | 初始化/备份DB/加载站点 | — | 磁盘<1GB→暂停 |
| spec | SPEC抓取 | spec_crawl() | 连续5失败→品牌熔断 |
| retail | 价格监控 | s2_monitor_known() | 90秒/SKU超时→TIMEOUT |
| review | 网评增量 | s4_review_incremental() | 已有评价→跳过 |
| verify | DB增量验证 | — | 增量=0→警告 |
| report | 生成报告 | — | — |
| retry | 失败重试 | s2_monitor_known() | 最多2次/limit减半 |

## 使用方法

```bash
# 全量5线串行执行
python -m overseas.flow.langgraph_crawl --lines japan,na,sa,eu,asia

# 单线执行
python -m overseas.flow.langgraph_crawl --lines japan

# 断点续跑
python -m overseas.flow.langgraph_crawl --resume

# 指定DB和代理
python -m overseas.flow.langgraph_crawl --lines japan --db data/overseas.db --proxy http://127.0.0.1:7877
```

## 边界条件

1. **磁盘空间**：每个节点执行前检查 `free > 1GB`，不足→暂停+告警
2. **代理超时**：retail_node 90秒/SKU超时→标记TIMEOUT
3. **品牌熔断**：spec_node 连续5系列失败→熔断（spec_crawl内置）
4. **WAF硬封**：retail_node 连续3 blocked→标记blocked跳过
5. **断点续跑**：State持久化到 `data/crawl_state.json`
6. **最大重试**：retry_node 最多2次，limit递减

## 测试结果

日本线端到端测试通过：
- init: SPEC=6站, RETAIL=1站, 磁盘=1.7GB ✅
- spec: 6站105系列ok, 20旧系列404跳过 ✅
- retail: kakaku_jp 超时(代理) ⚠️
- review: reviews_new=0(已有无新增) ✅
- verify: spec_row=+0(同周重复), price=+6 ✅
- report: 自动生成 `日本线LangGraph增量报告.md` ✅
