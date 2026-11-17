# Capsule Cinema 来源

- 上游为 JuneYaooo/capsule-cinema，快照提交 `495dd5844ebee33ccfd40a6e91b604a6745cdf32`。
- 此处只随包提供原版分镜合同、校验器及其直接依赖。源码保留原样，随附 Apache 2.0 许可证。
- `tools/handoff.py` 负责按本项目约定生成分镜文档和本地媒体，调用该校验器验证分镜，再把逐段输入交给独立 H3 适配器。客户机不需要安装 Capsule 的完整多供应商运行时即可执行这条路线。
