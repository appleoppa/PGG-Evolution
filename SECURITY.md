# 安全政策（Security Policy）

## 报告漏洞（Reporting a Vulnerability）

如果你发现安全漏洞（密钥泄露、越权、命令注入、凭据处理缺陷等），**请不要公开披露**，通过以下任一渠道私密报告：

- GitHub Private Vulnerability Reporting（推荐）：进入本仓库 **Settings → Security → Vulnerability alerts → Report a vulnerability**，按提示填写
- 或发送邮件到仓库维护者（见 GitHub 主页个人信息）

我们会在 **5 个工作日内**确认收到，并尽快修复。

## 本项目安全边界

本方案遵循"默认只读 + 沙箱写入"原则：

- 引擎默认只读；所有写入限 `~/.pi/agent/evolution/` 沙箱
- 生产变更需人工审批；不写 canonical memory、不改权限/路由/安全策略/凭据
- kill switch：`SELF_EVOLUTION_PLUGIN_DISABLED=1` 时插件拒绝运行
- 密钥通过宿主白名单凭据桥获取（只进内存、不落盘、不打印、不写仓库）

## 支持版本

| 版本 | 支持状态 |
|---|---|
| latest（main 分支） | ✅ 支持 |
| 历史 tag | 仅重大安全修复 |

## 已审计边界

- 无网络外发（LLM 调用需部署方显式配置凭据桥）
- 无凭据写入仓库（密钥值永不入库）
- 沙箱外无写入（测试/运行只在沙箱）
