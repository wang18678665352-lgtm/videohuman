# 安全策略

## 报告漏洞

请勿在公开 issue 中披露安全漏洞。请通过仓库所有者的私信渠道(或 GitHub Security Advisories
"Report a vulnerability")私下报告,附上复现步骤与影响范围。我们会在确认后尽快修复并致谢。

## 部署建议

- 对外提供服务时务必设置长随机串 `VH_WEB_TOKEN`,并通过 HTTPS 反向代理暴露;
- `configs/cookies/`(平台登录态)、`.env`、`configs/server.local.bat` 均为敏感文件,勿提交或外发;
- 定期运行 `vh doctor` 检查配置;日志目录 `logs/` 可能包含任务参数,注意访问权限。
