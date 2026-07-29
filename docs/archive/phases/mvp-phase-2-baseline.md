# Eidos MVP 第二期回归基线

记录时间：2026-07-16

## 版本

- Git commit：`697208d7cf33dedf0f3508246ac4bcaae7e655ec`
- MVP Lite：`mvp-lite.md` 历史完成版本
- JSON-RPC：v1
- Runtime：0.1.0
- SQLite schema：一期隐式 schema，无 revision row
- 协议 fixture SHA-256：`1ca658a6411a91a72f3dce3a0f05c28e0b53bd1b4029c6a7a447cb1e29097493`

## 自动化结果

- macOS 原生 Runtime：78 tests，全部通过，11.171s
- 覆盖：协议、Session 重启、Runtime Loop、文件原子提交、Shell 进程组、Seatbelt、DeepSeek Adapter
- 受限容器内的 Seatbelt/进程组失败不计入产品基线；发布检查必须在 macOS 原生环境执行。

第二期新增测试只可扩展本基线，不得删除或放宽一期协议 fixture 与安全断言。
