# 贡献指南（Contributing）

感谢你有兴趣改进 PGG-Evolution！本项目是**通用、独立的自进化插件**，欢迎任何贡献。

## 怎么贡献

### 1. 报问题（Bug / 想法）
- 打开 [Issues](https://github.com/appleoppa/PGG-Evolution/issues)
- 说明：现象 / 期望 / 实际 / 环境（Python 版本、系统）

### 2. 提交代码
1. Fork 本仓库
2. 新建分支：`git checkout -b feature/your-feature`
3. 修改代码（遵循现有风格：纯 Python 标准库，不引入第三方依赖）
4. **必须跑测试**：`python3 tests/test_self_evolve.py`（7 项全过）
5. 提交 PR，说明改动内容和验证结果

### 3. 增加评测样例
- warmup 样例放 `examples/`（可调优）
- holdout 样例必须**冻结**（创建后不得回看调优）

## 代码规范

- 纯 Python 标准库（`from __future__ import annotations`）
- 中文注释，关键逻辑英文函数名
- 每次变更必须有测试覆盖
- 不引入敏感信息（token/key/secret 绝不入库）

## 门禁

- PR 自动跑 CI（`tests/` + health + kill switch + curve）
- 所有改动默认只读/沙箱，生产写入需说明
- 红线：不碰法律意见、canonical memory、权限/路由/凭据

## 发布

版本号遵循 `vX.Y.Z`，主要变更写 CHANGELOG。
