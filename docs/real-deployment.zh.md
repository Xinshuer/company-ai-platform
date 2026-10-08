# 如果真的部署

[English](real-deployment.md) | **中文**

如果要在瑞典一家约 70 人的真实公司（像虚构的 ABC Tech AB 这样）运行这个平台，需要改哪些东西。每一条都写明了今天对应的是哪个模块或配置文件。这里写的都还没有实现，演示版仍然是单进程的模拟（[架构 §17](architecture.zh.md#17-如果真实部署)）。

大部分设计可以原样保留：业务代码只认 `local` 和 `external` 两个线路名；权限由代码判断，再由行级安全（RLS）兜底；所有写操作都走提议；审计哈希链在数据库里。要变的是周边的基础设施：真正的本地模型、真实的身份、多个 worker 共享的状态、密钥管理、网络控制和运维。

下面的数字（硬件、价格、负载）都是**用来做规划的数量级估计，不是实测值**。买之前请用自己的提示词做压测，并拿到报价。

## 目录

1. [目标架构](#1-目标架构)
2. [本地线路：硬件与推理服务](#2-本地线路硬件与推理服务)
3. [外部线路与欧盟数据驻留](#3-外部线路与欧盟数据驻留)
4. [身份](#4-身份)
5. [策略](#5-策略)
6. [多个 worker：今天按进程保存的状态](#6-多个-worker今天按进程保存的状态)
7. [数据库](#7-数据库)
8. [密钥](#8-密钥)
9. [审计锚点放到数据库主机改不了的存储上](#9-审计锚点放到数据库主机改不了的存储上)
10. [可观测性](#10-可观测性)
11. [网络与出口](#11-网络与出口)
12. [备份与保留期限](#12-备份与保留期限)
13. [成本模型](#13-成本模型)
14. [上线计划](#14-上线计划)
15. [代码和配置改动清单](#15-代码和配置改动清单)

## 1. 目标架构

```text
                 身份提供方（Keycloak 或 Entra ID）：OIDC 登录；SCIM 或 HR 系统向目录同步供数
                                  ▲
员工浏览器 ──TLS──► 反向代理 ──► API worker ×2–3（corpai/api，MCP 服务器在进程内，只用应用角色）
                                    │          │          │
                                    │          │          └──► 出口代理 ──► 外部模型 API（欧盟）
                                    │          └──► GPU 主机：vLLM 或 SGLang（本地线路，内网 TLS）
                                    ▼
          Postgres 16 + pgvector（主库 + 备库）◄── 任务进程（owner 角色：执行器、目录同步、
                                                    指标刷新、prune、审计锚点 ──► WORM 存储桶）
          脱敏后的 span ──► OpenTelemetry collector / 自托管 Langfuse
```

全部放在瑞典或欧盟境内。API worker 只持有 `corpai_app` 数据库角色；需要 owner 角色的工作挪到单独的任务进程里（第 7 节）。

## 2. 本地线路：硬件与推理服务

**现在。** `config/models.yaml` 让两条线路都指向 `https://api.deepseek.com`；`local` 带着 `simulated_onprem: true`，`config/egress.yaml` 里 `model.local` 的主机也是它。把 `local` 换成真服务器，只改它的 `base_url`、`model`、`api_key_env` 和出口清单条目，再加上下面几点。

**推理服务。** 在 Linux GPU 主机上跑一个 OpenAI 兼容的推理服务：[vLLM](https://docs.vllm.ai/) 或 [SGLang](https://docs.sglang.ai/)。两者都有连续批处理和前缀缓存，很适合这个平台：法务审查每审一个条款都发同样的 Playbook 提示词。代码对推理服务的要求：

- **工具调用。** `qa` 最多 3 轮调用只读工具（`corpai/agents/qa.py` 的 `MAX_ROUNDS`）；Clause Check 的逐条款循环也以工具调用的方式做查询（`corpai/legal/clausecheck/review.py`，每个条款最多 `max_steps = 6` 步）。启动服务时要打开所选模型家族对应的工具调用解析器。
- **JSON 模式。** `json_mode` 为真时，网关发送 `response_format: {"type": "json_object"}`（`corpai/gateway/backend.py`），推理服务要支持结构化输出。
- **HTTPS。** 后端拒绝非 https 的地址（`corpai/gateway/egress.py`）。推理服务前面要有 TLS，证书由公司 CA 签发。HTTP 客户端用操作系统的证书库校验（httpx2 用的是 `truststore`）；又因为客户端建的时候设了 `trust_env=False`（不让代理环境变量偷偷多加一跳），它也不读 `SSL_CERT_FILE`。所以要把公司 CA 装进 API 主机的系统证书库，或者在 `corpai/gateway/backend.py` 里显式传入。
- 推理主机**不记录提示词**（检查服务的请求日志参数）；如果担心时间侧信道，按用户隔离前缀缓存（vLLM 支持每个请求带一个缓存盐）。
- **模型专有的请求字段。** `config/models.yaml` 里的 `extra_body: {thinking: {type: disabled}}` 是 DeepSeek 关掉思考模式的开关，现在是全局的（`corpai/gateway/conf.py` 的 `GatewayConfig.extra_body`）。要改成按线路配置，本地服务用自己的字段（比如关掉思考的聊天模板参数）。

**模型。** 选一个中等规模的开放权重指令模型：大约 30B 参数的稠密模型（或总规模相近的 MoE 模型），英语、德语、瑞典语都要好，工具调用和 JSON 输出稳定，许可证允许公司内部商用。用这个平台自己的评测集来挑，不要看榜单：把 `local` 指向候选服务器，运行 `CORPAI_LLM=live python -m pytest evals/qa evals/routing evals/legal -m eval`（法务评测要 CORPAI_EVAL_RUNS ≥ 3 才算验证过，见 `evals/legal/harness.py`）。注意：本地线路换成真服务器之后，实时评测里 L1–L2 的调用仍然会发到 `external` 指向的地方。

**GPU 选型。** 瓶颈是显存：权重加 KV 缓存。

| | 权重（约 30B 稠密模型） | KV 缓存 |
|---|---|---|
| BF16 | 约 64 GB | 典型的 30B 分组查询注意力模型约每个 token 0.25 MB：一个 8k token 的请求占约 2 GB |
| FP8 | 约 32 GB | 用 FP8 KV 缓存大约减半 |
| 4 位（AWQ/GPTQ） | 约 18 GB | |

70 人公司的两种做法：

- **一台服务器两张 48 GB 的 GPU**，每张卡各跑一个 FP8 或 4 位模型的副本，前面放一个简单的负载均衡。扣掉权重和运行时开销，每张卡还剩约 10–25 GB 给 KV 缓存，也就是每个副本能同时处理约 5–12 个 8k token 的请求（用 FP8 KV 缓存再翻倍）。两个副本还意味着坏一张卡本地线路不会停；如果只有一个副本，卡一坏，所有 L3/L4 功能都停，因为网关绝不回退到 `external`（`corpai/gateway/gateway.py` 第 4 条规则）。
- **一张 80–96 GB 的 GPU** 跑 FP8 模型：更简单，KV 缓存更多，但没有冗余。

量化是拿质量换显存和速度。在当前的 GPU 上 FP8 和 BF16 差别很小；4 位一般够问答和起草用，但要在法务评测集上检查，因为漏掉一个高风险条款是代价最大的错误（`evals/legal`）。用评测数字来决定，不要凭原则。

**负载与信号量。** 粗略估计：70 名员工，某一天大约 40 人在用，高峰时同时有几个模型调用在跑。最吃资源的是法务审查：条款是一个接一个审的（`corpai/legal/review.py`，最多 120 个条款，每个最多 6 步模型调用），所以一份 20 个条款的合同就是 20–120 次串行的本地调用，会占着一个并发名额好几分钟。`config/models.yaml` 按线路、**按事件循环**限制同时进行的调用数（`max_concurrency`：local 2，external 8；`ModelGateway._sem` 里的 `asyncio.Semaphore`），排队超过 `queue_timeout_s`（local 20 秒）就报 `GatewayBusy`。换成真硬件后：

- 先用压测确定推理服务自己的上限（vLLM 的 `--max-num-seqs`），在延迟可接受的范围内取值；再把 local 的 `max_concurrency` 设为这个上限除以 API worker 数（每个 worker 各有一个信号量，见第 6 节）；
- 调大 local 的 `queue_timeout_s` 和 `timeout_s` 以适应法务审查，或者把长审查改成后台任务，不放在 HTTP 请求里同步跑；
- 保留按部门的每日 token 预算（`ops.budget_defaults`，每个部门每个 UTC 日 2,000,000 token，迁移 010）：它已经在数据库里，所有 worker 共用。在本地线路上它管的是公平，不是花钱。

## 3. 外部线路与欧盟数据驻留

**现在** `external` 是 DeepSeek API，运营方在中国。网关只把 L1–L2 发过去（[架构 §8](architecture.zh.md#8-模型网关a4) 第 3 条规则）；按规则个人信息至少是 L3（决定 D2、D4），DLP 只会把调用改到 `local`。对一家真实的瑞典公司来说，这还不够：

- L2 是公司内部信息，而分级是基于规则和人名表的（`corpai/classify`）。一条没有任何可检测特征的机密信息，可能被判为 L2。
- 把个人信息发到没有充分性认定的国家，需要 GDPR 第五章的依据（标准合同条款加传输影响评估）。设计上虽然不发个人信息，但一次误判就构成这样的传输。

所以在生产环境里，要么让 `external` 指向一家承诺数据留在欧盟、签了数据处理协议（GDPR 第 28 条）、不拿客户数据训练的模型服务商；要么干脆不要外部线路：让 `external` 也指向本地服务器（路由代码不用改，只改 `config/models.yaml` 和 `config/egress.yaml`）。后者需要更多 GPU，因为所有调用都跑在公司自己的硬件上；前者能在欧盟合同下，把便宜的服务商算力用在公开和内部的问题上。

其他所有存 L3/L4 数据的东西也都放在欧盟，最好在瑞典：数据库和备份、GPU 主机、trace 和可观测性存储、审计锚点存储桶、身份提供方。处理活动记录和 DPIA 放在 [compliance/](../compliance/)。

## 4. 身份

**现在**任何人都能选任何演示身份：`POST /api/auth/demo-login`（`corpai/api/routes_core.py`）给列表里的身份签发登录会话；`python -m corpai metrics ... --as` 在命令行上做同样的事（`corpai/cli/metrics.py`）。两者都只在 `CORPAI_DEMO_LOGIN=1` 时可用（`corpai/config.py`），都按 `demo_login` 记入审计。目录（`core.users`、`core.memberships`、`core.grants`）由 `corpai/synth` 写入。

**登录。** 用 OpenID Connect 的授权码流程加 PKCE，对接 Keycloak（自托管，身份数据留在公司内部）或 Entra ID（如果公司已经在用 Microsoft 365）。API 自己做 OIDC 客户端（backend-for-frontend 模式）：在 `corpai/api/routes_core.py` 里加跳转和回调两个路由，把身份提供方的用户标识映射到 `core.users.user_id`（新加一列存这个标识），然后调用现有的 `tokens.aissue_session(user_id)`。之后的一切都不变：HttpOnly 的会话 cookie、`core.sessions` 里的记录、每个请求都从目录重新读取用户身份（`corpai/api/security.py` 的 `session_principal`）。设置 `CORPAI_DEMO_LOGIN=0`，就关掉了 `/api/personas`、演示登录和命令行的 `--as`。多因素认证在身份提供方那边强制。界面上的身份选择页（`ui/js/view_login.js`）换成一个“登录”按钮。

上了 TLS 还要改：cookie 加 `secure=True`（`corpai/api/security.py` 的 `set_session_cookie`）；CSP 的 `connect-src` 去掉 `http://127.0.0.1:8000`（`corpai/api/security.py` 和 `ui/index.html` 里的 meta 标签）。把员工访问服务器用的名称写进 `CORPAI_ALLOWED_HOSTS`：为了防 DNS 重绑定，应用只回答回环地址和登记过的名称（`HostCheck`，问题记录 FN-26）。

**组映射。** **不要**直接用令牌里的组声明作 ACL 组。声明在一次登录的有效期内是固定的，而 `identity.groups.effective_groups` 每个请求都从目录重新计算，所以停用账号或撤销授权会立刻生效（[架构 §5](architecture.zh.md#5-身份组与令牌a3)）。正确做法是保留目录表，用一个 owner 任务从各业务系统同步进来（和今天的 synth 一样记 `owner_job` 审计）：

| 目录数据 | 来源 |
|---|---|
| `core.users`：姓名、邮箱、部门、角色（`employee`、`manager`、`ceo` 等）、职位、`manager_id`、`active` | HR 系统，或身份提供方的 SCIM 同步；身份提供方里的 `dept-sales`、`heads` 这类组映射到部门和角色 |
| `clearance` | 按 [架构 §5.1](architecture.zh.md#51-clearance-与-acl-分配模拟数据策略rls-三方共同遵守的数据约定) 的规则推导，不手工填 |
| `core.memberships`：`proj:`、`matter:`、`req:` | 工单系统（项目成员）、法务案件登记（承办律师）、招聘系统（面试小组） |
| `core.grants`（限时授权，比如法务临时查看一个 CRM 客户） | 给平台管理员一个带工单号的小审批流程；现在只有 synth 会写 |

员工离职：SCIM 把 `active` 设为 false，下一个请求读到的就是停用的身份，什么也看不到；`revoke_user_sessions` 立刻结束这个人所有的登录会话。

## 5. 策略

**现在**规则是 `corpai/policy/rules.py` 里的 Python 表，由纯函数 `decide()`（`corpai/policy/engine.py`）求值；同样的读规则再由 RLS 策略（`core.can_read`，在迁移文件里）执行一遍；有特权的 SQL 函数也会再查一次角色。测试保证 `decide()` 和 RLS 对每个演示身份的结论一致（`tests/security/test_permission_matrix.py`、`test_permission_matrix_rls.py`）。

**要不要换成 OPA（Rego）或 Cedar？** 好处：策略和代码分开评审、分开发布；有决策日志；其他服务需要同样的判断时用同一个引擎。代价：每次判断多一次网络调用或 sidecar；SQL 之外又多一种语言；而且 RLS 策略还得手工维护，因为数据库调不了 OPA。

对一家 70 人、只有一个应用的公司，保留 Python 表是说得通的。如果平台发展成多个服务，可以保持函数签名不变，把 `decide()`、`tool_allowed()` 和 `can_approve()` 换到 OPA 或 Cedar 后面，并把 `tests/security/permission_matrix.yaml` 作为两个引擎共同的一致性测试。**无论怎样都保留 RLS**：应用代码出 bug 时它仍然有效，而且应用角色是 `NOBYPASSRLS`（`corpai/db/pg.py`）。

## 6. 多个 worker：今天按进程保存的状态

`python -m corpai serve` 是单进程（`corpai/cli/serve.py`）。为了可用性跑两三个 worker 之前，要先改这些地方：

| 状态 | 现在在哪 | 怎么改 |
|---|---|---|
| 上传的合同 | 进程内存，按用户存 2 小时（`corpai/agents/legal/uploads.py`） | 一张表（比如 `legal.uploads`），带标签列、`trust = 'untrusted'`、RLS“只有上传者能读”，通过 SECURITY DEFINER 函数写入；再加一个按 2 小时过期清理的任务 |
| 一个会话同时只跑一个运行 | 进程内字典里每个会话一把 `asyncio.Lock`（`corpai/agents/_runner.py` 的 `_thread_locks`） | 按会话 id 加 Postgres advisory 锁：新运行用 try-lock（忙就返回 400，和现在一样），恢复运行用阻塞锁 |
| 模型并发 | 每个事件循环、每条线路一个 `asyncio.Semaphore`（`corpai/gateway/gateway.py`） | `max_concurrency` 除以 worker 数，或者用一个共享的限流器；真正的上限是推理服务自己的队列 |
| 审计写入并发 | 每个事件循环最多 16 个，每次写入新开一个连接（`corpai/audit/log.py`） | 每个 worker 一个小的专用连接池；注意连接总数（见下） |
| checkpoint 连接 | 每个进程一个 16 连接的池，每个运行中的图占一个连接（`corpai/agents/_checkpoint.py`） | 每个 worker 一份没问题；按 `max_connections` 算好总数 |
| 签名密钥 | `$DATA/secrets` 下的文件，第一次用时生成（`corpai/identity/keys.py`） | 所有 worker 用同一套密钥，从密钥库取（第 8 节） |
| 中断的提议 | 每次 API 启动时，`fail_interrupted()` 把处于 `approved` 超过 300 秒的提议标为失败（`corpai/proposals/service.py`，在 `corpai/api/app.py` 里调用） | 改由一个定时任务执行，或者给执行加租约，免得一个重启的 worker 把另一个 worker 正在执行的提议标成失败 |
| 指标刷新 | CFO 的演示按钮 `POST /api/metrics/refresh`（`corpai/api/routes_metrics.py`） | 月结之后的定时任务；刷新本身已经加了 advisory 锁（`corpai/metrics/refresh.py`） |
| checkpoint 过期清理 | 手动运行 `python -m corpai prune`（`corpai/cli/prune.py`） | 每晚的定时任务 |

已经可以跨 worker 的：宕机开关和预算（在 `ops` 表里）；人名表（每个 worker 启动时加载，运行器在每次运行前核对 `ops.name_source_fingerprint()`，见 `corpai/agents/_gazetteer.py`）；LangGraph 的 checkpoint（在 `lg` schema 里）；提议；审计哈希链（插入触发器用 advisory 锁串行追加）。

MCP 服务器可以继续留在进程内（`corpai/api/app.py` 里的 `McpClient(asgi_app=app)`）。如果拆出去，设置 `CORPAI_API_BASE`，并把工具令牌从 HS256 改成非对称算法（`corpai/identity/tokens.py`）：共用一个 HMAC 密钥时，任何能验证工具令牌的服务器，也能替任何用户签发令牌。

## 7. 数据库

**现在** pgserver 内嵌运行 Postgres 16 和 pgvector，只监听回环地址，角色没有密码：`corpai/db/pg.py` 从 pgserver 给的 URI 拼出连接串（`_swap`）。API 进程还会以 `corpai_owner` 身份连接，做好几件事：启动时执行迁移、提议执行器、checkpoint 表的初始化、加载人名表、写审计锚点和校验哈希链（[架构 §4](architecture.zh.md#4-数据库)）。

**怎么改。**

- 换成真正的 Postgres 16 + pgvector（两台虚拟机做流复制，或者欧盟境内、支持 pgvector 并允许 RLS 和 SECURITY DEFINER 函数的托管服务）。连接串和凭据从配置读取；`corpai/db/pg.py` 是冻结的核心代码，所以要先改规范。
- 角色用 SCRAM 密码或客户端证书；`pg_hba.conf` 只允许 API 主机以 `corpai_app` 连接、只允许任务进程以 `corpai_owner` 连接。
- **让面向用户的 API 拿不到 owner 角色。** 把提议执行、迁移、`setup_schema`、审计锚点和哈希链校验挪到任务进程（有自己队列的 worker）。这样 API 只持有 `corpai_app` 的凭据，即使 API 进程被攻破也绕不过 RLS。执行器本来就是根据已批准的记录执行、并重新核对参数哈希，挪过去不用改逻辑。
- 现在每个 API worker 的连接数：应用连接池 10（`corpai/api/app.py`）+ checkpoint 16 + 审计最多 16，三个 worker 就远超 Postgres 默认的 `max_connections` 100。PgBouncer 的事务模式适用于 `principal_tx`（它用事务内有效的 `set_config(..., true)` 设置用户身份）和审计写入（每个连接只调用一次函数），前提是 PgBouncer 的版本能处理 psycopg 的预编译语句；但它**不适用于** checkpoint 存储：checkpoint 在一个整个运行期间都占着的连接上，以会话级设置用户身份变量（`corpai/agents/_checkpoint.py`）。这个连接池要直连数据库，或者用会话模式的连接池。
- 迁移继续是纯 SQL，并保留 sha256 校验（`corpai/db/migrate.py`）；由部署流水线执行，而不是每个 worker 启动时各执行一遍。

## 8. 密钥

| 密钥 | 现在 | 生产环境 |
|---|---|---|
| 模型 API key | `config/models.yaml` 里 `api_key_env` 指定的环境变量，调用时才读取（`corpai/gateway/backend.py`） | 进程启动时由密钥库注入；本地推理服务单独一个 key（`vllm --api-key`），`local` 和 `external` 绝不共用 |
| 登录会话、工具令牌、网关令牌的签名密钥 | `$DATA/secrets` 下三个文件，第一次用时生成；删掉文件就让所有人退出登录（`corpai/identity/keys.py`） | 放进密钥库，令牌头里带密钥 id，轮换时新旧密钥可以并存；如果 MCP 服务器拆出去，工具令牌改用非对称密钥 |
| 审计锚点密钥 | `$DATA/secrets/audit_anchor.key`（`corpai/audit/anchor.py`） | 数据库主机读不到的 KMS 或 HSM 密钥；更好的是用非对称签名，校验时不需要任何秘密 |
| 数据库凭据 | 没有（pgserver，回环地址） | 每个角色一份，放在密钥库里，定期轮换 |
| OIDC 客户端密钥 | — | 密钥库 |

用一个统一的密钥库（HashiCorp Vault，或欧盟托管商提供的 KMS 和密钥管理服务）。任何秘密都不进 `config/`、代码仓库、日志、trace 或审计详情；后两者现有代码已经在强制（`corpai/audit/log.py` 拒绝像凭据的键和值，trace 拒绝像凭据的属性键；令牌从不进入图的状态，见 [架构 §5](architecture.zh.md#5-身份组与令牌a3)）。日志只靠约定保持干净：没有日志过滤器，要加一个对每条记录运行 `scrub_secrets` 的过滤器。

## 9. 审计锚点放到数据库主机改不了的存储上

**现在** `audit.log` 在 Postgres 里只能追加、带哈希链（触发器拒绝修改、删除和清空）。光有哈希链，发现不了被截掉的尾部，也发现不了控制这张表的人把历史整体重算一遍，所以每个 owner 任务都会把链头追加到 `$DATA/audit_anchors.jsonl`，用 `$DATA/secrets/audit_anchor.key` 做 HMAC 签名，`verify_chain()` 会检查每个被锚定的行（`corpai/audit/anchor.py`）。这两个文件和数据库在同一台机器上，而且最新锚点之后追加的行被删掉，仍然发现不了（[架构 §9](architecture.zh.md#9-审计与-tracea1)）。

**改动**在 `corpai/audit/anchor.py`（`write_anchor`、`read_anchors`）：

- 每个锚点作为一个对象写进 S3 兼容存储，开启**合规模式的对象锁**（WORM），所用凭据只能写入对象：在保留期结束之前，数据库主机和平台管理员都删不掉、也改不了锚点。本地文件保留作缓存。
- 用数据库主机读不到的密钥给锚点签名（第 8 节）。
- 按时间表打锚点（比如任务进程每 5 分钟一次），而不只是在 owner 任务运行时，这样没被锚定的尾部就很短。
- 每天跑一次 `verify_chain`，出现 `first_bad_seq` 就告警。安全审计员的 `GET /api/audit` 已经会报告哈希链状态。

## 10. 可观测性

**现在** trace 是 `trace.spans` 表里的行，通过 `trace.write_span()` 写入；文字在 span 结束时脱敏一次，由 `corpai/trace/span.py` 的 `default_masker` 完成（先清除密钥，再 `mask_for_level`；L4 的 span 不存文字）。发起人看自己的 span，平台管理员只看元数据，部门负责人只看本部门的计数，且分组至少 5 人。没有指标和告警的流水线。

**怎么改。**

- 把 span 导出到 **OpenTelemetry**（OTel collector 接 Tempo/Jaeger 和 Prometheus/Grafana），或者欧盟境内**自托管的 Langfuse**。导出放在 `corpai/trace/span.py` 的 `Tracer` 里、**脱敏之后**：导出器拿到的文字和存进 `trace.spans` 的完全一样，L4 的 span 只导出元数据。数据库里的 span 可以保留给用户看的 trace 页面用，或者等新工具里重建好访问规则之后再替换。
- **不要打开那些在网关脱敏之前就抓提示词的自动埋点**（OpenAI SDK 的埋点、LangChain 的回调）。尤其是 `langsmith` 作为 LangGraph 的依赖已经装上了：只要设置 `LANGSMITH_TRACING=true`，LangChain 就会挂上一个 tracer，把图的状态发到 LangSmith 服务，绕过 `config/egress.yaml` 和脱敏。导入 `corpai` 时会把这几个追踪变量强制设成 `false`（`corpai/__init__.py`，问题记录 FN-25）；保留这一点，同时在出口代理上封掉那个主机（另见 [compliance/egress-inventory.md](../compliance/egress-inventory.md)）。
- 指标和 SLO，从网关已经在记录的数据里推出来（`model_call` 审计记录里有线路、延迟、重试次数和 token 用量，见 `corpai/gateway/gateway.py`）：

| 指标（SLI） | 目标（示例） |
|---|---|
| 工作时间内 API 可用性 | 99.5 % |
| `qa` 回答延迟 p95 | `local` 上 < 10 秒 |
| `gateway_busy` 和 `local_unavailable` 错误 | < 模型调用的 1 % |
| 20 个条款的合同审查 p95 | < 5 分钟 |
| 审计哈希链校验 | 每天一次，始终通过 |

- 告警：本地线路宕机（`local_down` 开关和推理服务的健康检查失败）、某部门预算用完、出口代理拒绝、`verify_chain` 失败。

## 11. 网络与出口

**现在**出口控制是在进程内做的：`config/egress.yaml` 列出所有目的地、各自的主机和最高级别；发送任何东西之前先跑 `check_egress()`；实时模式的 HTTP 客户端对每个请求（包括重定向）重新检查主机，并且不理会代理环境变量（`corpai/gateway/backend.py`、`corpai/gateway/egress.py`）；外部调用在发送之前先写进 `ops.egress_log`；还有一个测试，只要 `corpai/` 或 `config/` 里出现清单之外的主机名就失败。`web` MCP 服务器是一个模拟的语料库（`search.mock`，`in_process`）。

**怎么改。** 在下面加上网络这一层：

- API 主机没有直接通往互联网的路由。出站 HTTPS 只能经过一个正向代理（Squid 或 Envoy），代理的白名单**由 `config/egress.yaml` 生成**（`http` 类目的地和它们的主机），这样清单仍然是唯一的来源。
- 因为后端故意不理会代理环境变量，所以要按目的地显式配置代理（在 `config/egress.yaml` 里加一个字段，由 `corpai/gateway/backend.py` 使用）。
- 每天对账：代理日志里的每个外部请求，在 `ops.egress_log` 里都要有对应的行，反过来也一样。
- 网络分区：反向代理 → API worker → 数据库和 GPU 主机；数据库和 GPU 主机只接受来自 API worker 和任务进程的连接。
- `sales_profile` 要接真实的网络搜索时，加一个新的 `http` 目的地，`max_level: L1`，并有自己的域名白名单（`corpai/mcp_servers/web_allowlist.py`、`corpai/mcp_servers/web.py`）。
- Python 包在构建时从内部镜像安装；运行时不安装任何东西。embedding 模型也一样：设置 `CORPAI_EMBED=fastembed` 时，fastembed 第一次使用会从 huggingface.co 下载模型（`config/egress.yaml` 里的 `embed.model_download`，不带任何公司数据）。构建镜像时把 `.cache/fastembed` 拷进去，并设置 `HF_HUB_OFFLINE=1`，出口代理就永远不必放行这个主机。

## 12. 备份与保留期限

**备份。** 持续的 WAL 归档加时间点恢复（pgBackRest 或 Barman），加密，存在欧盟，每季度做一次恢复演练。对这个规模的公司，目标是 RPO 15 分钟、RTO 4 小时。锚点存储桶有自己的保留期，不属于数据库备份。丢了登录会话或工具令牌的密钥，只是让所有人重新登录；丢了锚点密钥，旧锚点就无法校验了，所以它由 KMS 负责备份。

**保留期限**，要和法律顾问一起定，并写进处理活动记录：

| 数据 | 现在 | 建议 |
|---|---|---|
| `audit.log` | 永久保留；触发器对所有人拒绝删除 | 保留固定年限（比如 2–5 年）；按年分区，用一个有文档、有审计的超级用户流程整区删除，删除前再打一个锚点 |
| `trace.spans`（脱敏后的文字，最高 L3） | 永久保留；删除请求通过 `trace.erase_subject()` 覆盖到它们（迁移 103，问题记录 D-04） | 30–90 天 |
| LangGraph checkpoint（`lg`），里面是完整的图状态，包括 L3/L4 内容 | 闲置超过 `CORPAI_CHECKPOINT_TTL_DAYS`（30）天后手动 `prune` | 同样的期限，改成每晚的定时任务；HR 的会话更短 |
| 上传的合同 | 内存里 2 小时 | 新表里 2 小时 |
| `hr.screening_evidence`、`hr.screenings`、候选人 | 保留 | 招聘岗位关闭后保留固定期限，然后删除；在隐私告知里告诉候选人（GDPR 第 13 条） |
| `ops.egress_log`（每个发往外部线路的内容的副本，L1–L2） | 保留 | 保留到够复查和事件响应用，比如 12 个月 |
| `legal.reviews`、提议、发件箱 | 保留 | 作为法务和销售流程的业务记录 |
| `metrics.snapshots` | 保留 | 由总账派生；总账本身在财务系统里按瑞典《会计法》（Bokföringslagen）保存七年 |

删除与备份：删除请求从在线数据库里删掉数据；另外要维护一份删除登记，任何一次从备份恢复之后都要重新执行一遍。

## 13. 成本模型

2026 年的每月粗略数字；请换成实际报价。

| 项目 | 估计 | 依据 |
|---|---|---|
| GPU 服务器，2 张 48 GB 数据中心 GPU | 每月 €1,000–1,500 | 购置 €3–5 万，按 3 年折旧，加电费（约 1 kW）和机柜 |
| *或者*租用欧盟的 GPU | 每月 €500–3,500 | 每 GPU 小时 €1–2.5；只在工作时间开还是 24 小时开，一张还是两张 |
| 外部模型 API（如果保留） | 每月几十到一两百欧元 | 比如 40 人 × 每人 20 次调用 × 3,000 token ≈ 每个工作日 240 万 token，每月约 5,000 万，其中只有 L1–L2 的部分走外部 |
| Postgres 主备、任务进程、可观测性、对象存储 | 每月 €200–500 | 欧盟服务商的小型虚拟机和存储 |
| 身份提供方 | 已有 Microsoft 365 时 Entra ID 不额外花钱；Keycloak 要一台虚拟机 | |
| 人力 | 最大的一项：约 0.5 个人的平台工程，加上安全和合规工作（DPIA、AI 法案、审计） | |

每日预算给最坏情况封了顶：`ops.budget_defaults` 允许每个部门每个 UTC 日 2,000,000 token（`'*'` 兜底，迁移 010），所以七个部门加起来一天绝不会超过 1,400 万 token，不管 Agent 怎么跑。选硬件或签合同之前，先从 `ops.budgets.tokens_used` 和 `model_call` 审计记录里的 `usage` 量出实际用量。

## 14. 上线计划

每个阶段都有退出标准；任何应用都可以从 `config/agents.yaml` 的 `start` 里去掉来关闭，宕机开关能立刻停掉任一条线路。

| 阶段 | 范围 | 退出标准 |
|---|---|---|
| 0. 基础设施（4–6 周） | Postgres、身份提供方登录、密钥库、出口代理、备份、可观测性、锚点存储桶；GPU 主机，并用实时评测选定模型；DPIA 和处理活动记录；引入系统之前按瑞典《共同决定法》（MBL）与员工代表协商；员工的 AI 素养培训（欧盟 AI 法案第 4 条） | 本地模型通过评测；做过恢复演练；登录和 API 做过渗透测试 |
| 1. 试点 | IT 和一个部门使用 `qa` | 一个月内没有路由事故：每周抽查 `ops.egress_log` 里发往外部的内容，由人审阅并重新分级，没有发现机密内容，没有金丝雀泄露；延迟符合 SLO |
| 2. 全公司问答和指标 | 全员用 `qa`；CFO 用平台签核指标，定时刷新；CEO 和各部门负责人用管理层报告 | CFO 在平台上签核一次真实的月结；报告的数字和财务系统一致 |
| 3. 法务和销售 | `legal_review`（实时法务评测达到评测框架的门槛之后），`sales_profile` 接入经过出口代理的真实网络搜索 | 律师认可在真实合同上的审查质量；新网页来源上的注入测试通过 |
| 4. 最后才是 HR | 先 `hr_headcount`；`hr_screening` 要单独评估之后再上：招聘用途在欧盟 AI 法案下属于高风险（附件 III），要告诉候选人工具怎么用，在匿名化的真实数据上做偏见测试，并决定到底要不要用 | HR、法务和员工代表签字同意 |

## 15. 代码和配置改动清单

| 文件 | 改动 |
|---|---|
| `config/models.yaml` | `local`：真实的 `base_url`、`model`、自己的 `api_key_env`、`simulated_onprem: false`、实际的 `max_concurrency` 和超时；`extra_body` 按线路配置 |
| `config/egress.yaml` | `model.local` → 内网推理主机；`model.external` → 欧盟服务商（或本地主机）；按目的地配置代理；真实网络搜索的目的地 |
| `corpai/gateway/conf.py`、`backend.py` | 按线路的 `extra_body`；显式的出口代理；只有公司 CA 不在系统证书库里时才需要显式传入 |
| `corpai/api/routes_core.py`、`security.py`、`ui/js/view_login.js`、`ui/index.html` | OIDC 登录；`Secure` cookie；CSP 去掉 `http://127.0.0.1:8000` |
| 新的目录同步任务（`corpai/cli/`） | 从 HR 系统、身份提供方和业务系统同步用户、成员关系和授权 |
| `corpai/agents/legal/uploads.py` + 迁移 | 上传存进表里 |
| `corpai/agents/_runner.py` | 按会话的 advisory 锁 |
| `corpai/proposals/service.py`、`corpai/api/app.py` | `fail_interrupted` 改成定时任务或加租约；执行器挪到任务进程 |
| `corpai/identity/keys.py`、`tokens.py` | 密钥从密钥库读取、带密钥 id；MCP 拆出去时工具令牌改用非对称签名 |
| `corpai/db/pg.py`（冻结的核心代码，先改规范） | 连接串和凭据从配置读取 |
| `corpai/audit/anchor.py` | WORM 存储、KMS 里的密钥、定时锚点 |
| `corpai/trace/span.py` | 脱敏之后导出到 OpenTelemetry / Langfuse；固定关掉自动埋点 |
| `corpai/api/routes_metrics.py`、`corpai/cli/prune.py` | 指标刷新和 prune 改成定时任务 |
| `corpai/mcp_servers/web.py`、`web_allowlist.py` | 经过出口代理的真实搜索后端 |
