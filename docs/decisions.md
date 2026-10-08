# 决定记录（Decision log）

按时间记录已经定下来的事。完整的设计审查见 [design-review.md](design-review.md)；两者冲突时以本文件为准。

## 2026-10-07 范围与业务

| # | 决定 | 说明 |
|---|---|---|
| D1 | 这是**模拟项目** | 公司、员工、客户、合同、财务、简历全部用脚本合成，不出现任何真实个人信息或公司机密 |
| D2 | **法域：GDPR**。模拟公司 ABC Tech AB 总部在瑞典斯德哥尔摩（欧盟），员工和客户主要在欧盟（2026-10-07：应用户要求由德国改为瑞典；GDPR 和路由规则不变） | 合规说明按 GDPR + 欧盟 AI 法案写；DeepSeek API 在中国，个人信息外发即跨境传输，这是“个人信息只走 `local`”的依据 |
| D3 | 模型两条逻辑线路 `local` / `external`，**底层都调用 DeepSeek API**（用户已有 key，在用户环境变量 `DEEPSEEK_API_KEY`） | 业务代码只认线路名；换真本地只改 `base_url`。保留真实约束：审计每次路由、可选让 `local` 用较弱模型、“本地宕机”开关、并发上限 |
| D4 | 数据分级 L1 公开 / L2 内部 / L3 机密 / L4 绝密 + 五条规则（个人信息至少 L3、就高不就低、只升不降、按来源默认+按内容升级、密钥不进模型） | 详见 design-review.md 第四节 |
| D5 | **销售画像针对公司**，不对个人做网上画像 | 公司公开信息 L1 走 `external`；联系人只是 CRM 记录（L3，走 `local`）；“匹配度”（公开信号）和“意向”（CRM 互动）两个分数由代码计算 |
| D6 | **HR 筛简历：AI 只抽取证据**，逐条对照岗位硬性条件给“满足 / 不满足 / 无法判断”并引用原文；不打总分、不自动淘汰；HR 人工决定 | 高风险用途；送模型前去掉照片、性别、年龄、民族、婚育、籍贯、健康信息；做反事实偏见测试 |
| D7 | **管理层不看员工个人粒度的指标**，只看部门级、项目级汇总（n<5 的分组不展示） | 避开员工监控这一高风险用途 |
| D8 | **写操作真实执行，但只作用在模拟系统里**（模拟发件箱表、模拟 CRM / 工单 / 总账 / HRIS 表等），全部经过“AI 提议 → 人审批 → 系统执行 → 审计” | 工具分三级：只读 / 内部可撤销 / 对外或不可逆（第三级必须人工审批，四眼原则） |
| D9 | 做一个**本地打开的 HTML 演示界面**，展示 UI 和不同权限的人的使用场景 | 静态页面，无构建步骤；后端没启动时用录制好的场景数据（演示模式），后端启动时连真实接口（实时模式） |
| D10 | 不管 CMO / COO；管理层 = CEO + 各部门负责人（CTO、CFO、法务负责人、HR 负责人、销售负责人） | |
| D11 | **M1–M4 全部做**，不设时间限制 | M1 平台骨架 + 模拟公司；M2 法务（复用 Clause Check）；M3 管理层报告 + 财务指标层；M4 销售 + HR |

## 2026-10-07 环境与技术选型

| # | 决定 | 说明 |
|---|---|---|
| E1 | Python 3.12 虚拟环境 `.venv`（`D:\python\python.exe -m venv .venv`），依赖版本锁在 `requirements.lock.txt` | 主要版本：fastapi 0.142.2、langgraph 1.2.14、langgraph-checkpoint-postgres 3.1.2、psycopg 3.3.6、mcp 2.3.0、openai 3.26.0、fastembed 0.8.1、PyJWT 2.15.1、pytest 9.1.1、Faker 40.41.0、pgserver 0.1.4 |
| E2 | **数据库用 `pgserver`**（pip 包，自带 Postgres + pgvector 0.6.2），数据目录放 `.data/`，不装系统服务、不需要管理员权限 | 这台机器没有 Docker、Postgres、WSL。已实测：约 5 秒启动；pgvector 检索可用；行级安全（RLS）对非表主人的角色生效；`SET LOCAL` 在事务结束后清空 |
| E3 | 权限过滤用 **pgvector + Postgres RLS** 兜底；应用用非表主人的数据库角色连接；每个请求在事务里 `SET LOCAL app.*` | 表主人和超级用户默认绕过 RLS |
| E4 | 本地 embedding 用 fastembed 的 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`（384 维，约 0.22 GB），模型缓存放 `.cache/`；测试用确定性的假 embedding | 数据以英文为主，提问可能是中文 |
| E5 | 身份先“简化做”：后端签发会话 token 和短期工具 token（aud / sub / act），演示模式下可以选身份登录；以后可换 Keycloak | 不装 Keycloak |
| E6 | trace 先自己实现（Postgres 表，按级别脱敏），可选导出到 Langfuse；审计日志单独一张只追加的表，带哈希链 | Langfuse 自托管太重（需要 ClickHouse 等） |
| E7 | 测试和 CI 用假 LLM（确定性），不花钱；演示场景和部分评测用真实 DeepSeek 录制 | 密钥只从环境变量读取，绝不写进文件或日志 |

## 2026-10-08 收尾

| # | 决定 | 说明 |
|---|---|---|
| F1 | Playbook KB-03 加第二个备选立场：**双方另行签署、并在合同里写明适用于该处理的数据处理协议**（Playbook 版本号仍是 2026-10-01，规则编号不变） | 实时评测里模型把这种情况判为“超出 Playbook”，和代码（MR-09）、M3 的指标矛盾；Playbook 归法务负责人（问题记录 FN-05） |
| F2 | 法务审查里，模型**接受**某条款、但又请律师确认（`mark_for_review`）时，结论是“备选”（要人看），不是“不可接受”；接受但说“超出 Playbook”、写给 AI 的指令、代码底线检查仍是“不可接受” | 实时评测里这是假“不可接受”的主要来源（问题记录 FN-06） |
| F3 | 离线演示回放**真实 DeepSeek 的录制**（`python -m corpai demo-record`，2026-10-08，完整规模的公司），页面标明录制日期；测试和 CI 仍只用假模型 | 取代 E7 里“演示场景用真实 DeepSeek 录制”的计划，落实了它（问题记录 D-07、WR-06） |
| F4 | 之前记下的低危问题和待决定事项逐条定了结论：修、写进已知局限、不修或仍待定 | 见问题记录第十五节的“分诊结果”和 README 的“已知局限” |

## 已知的坑

- `pgserver` 在 Windows 上：`cleanup()` 删了数据目录，但 postgres 进程不会退出，日志文件会被占用。要先用 `.venv/Lib/site-packages/pgserver/pginstall/bin/pg_ctl.exe -D <数据目录> stop -m fast` 停服务；残留进程只能结束路径在本项目 `.venv` 下的 postgres 进程。
- 用户的环境变量和 Clause Check 项目的 `.env` 里都有 DeepSeek key。跑测试时要用假 LLM，或者显式去掉 `DEEPSEEK_API_KEY`。
