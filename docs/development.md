# 本地开发

## 环境

运行 `make sync` 安装锁定依赖，运行 `make compose-up` 启动本地 PostgreSQL/pgvector。

本地 API 和 Web 都只绑定 loopback。任何 production 模式在认证、数据库和 Secret 未配置时必须拒绝启动。

## TDD

每个可测试切片遵循：

1. 写失败测试并保存 RED 输出。
2. 写最小实现达到 GREEN。
3. 重构并运行相关回归。
4. 阶段结束运行根 `make check`。

纯配置、脚手架和文档使用对应解析器、构建或静态检查作为替代验证。

## 私有资料

内部课程资料只能从工作区之外的路径读取，输出写入 `.local/`。生成 fixture 必须是自制或开放许可内容。
