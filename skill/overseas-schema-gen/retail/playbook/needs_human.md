# 需要人工介入（needs_human）套路

**适用**：强防护站 / 业务边界，自动化值得止损的场景。**别硬凑，及时转人工。**

## 识别信号（命中任一即值得转人工）

| 信号 | 表现 | 例 |
|---|---|---|
| 验证码拦截 | 200 但 `blocked/captcha`，BrowserFetcher 仍撞 | Sams / Walmart 部分场景 |
| 登录墙 | 价格/评价需登录才显示 | 部分会员站 |
| 加购墙 | 价格在加购后才给出 | 部分 B2B |
| 无搜索入口 | 只能按类目翻，型号搜索不可用 | 部分旧站 |
| 反爬假页 | 页面有评价容器但内容全是草稿/空 | 防爬提示「需 JavaScript」 |

## 记录到 cases.jsonl

```jsonc
{
  "code": "<site>",
  "verdict": "needs_human",
  "signals": {"block_reason": "...", "structure": "..."},
  "result": {},
  "lesson": "为什么会标 needs_human / 缺哪个引擎档位"
}
```

`solution` 可留空；`lesson` 写清「若有人工补丁或引擎加 X 档位后可直接复用」。

## 转人工后

- schema 草稿保留在 `data/schema_drafts/`（不转正）
- 更新 `reference.md` 的「缺什么档位」段落，作为引擎 P 里程碑输入

## 与 blocked 的区分

`verdict=blocked`：反爬拦截但不是业务不可达（改代理/降频可重试）；
`verdict=needs_human`：业务上必须人参与（登录/验证码/手工采），不是频率问题。