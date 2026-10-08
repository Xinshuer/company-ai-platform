window.FIXTURES.runs = {
 "recorded": {
  "mode": "live",
  "model": "deepseek-flash",
  "on": "2026-10-08",
  "company": "full",
  "seed": 7
 },
 "items": [
  {
   "persona_key": "*",
   "app": "qa",
   "input": "FleetPilot 3.0 有哪些新功能？",
   "run": {
    "run_id": "run_01m4d8qy7e5c59zfjmet",
    "thread_id": "thr_01m4d8qy7bpn92j1dweq",
    "app": "qa",
    "status": "done",
    "output": {
     "text": "根据现有资料，FleetPilot 3.0 的新功能是**多仓库优化（multi-depot optimisation）**。\n\n- 新闻稿明确说明：FleetPilot 3.0 发布，带来多仓库优化功能 [doc:pub-press-2025-10-fleetpilot-3]。\n\n资料中没有提到 FleetPilot 3.0 的其他新功能。",
     "citations": [
      {
       "doc_id": "pub-press-2025-10-fleetpilot-3",
       "title": "FleetPilot 3.0 brings multi-depot optimisation",
       "level": 1,
       "collection": "public",
       "snippet": "# FleetPilot 3.0 brings multi-depot optimisation\n\n*Stockholm, 2025-10-07*\n\nFleetPilot 3.0 released with multi-depot optimisation.\n\nAbout ABC Tech: ABC Tech AB builds FleetPilot, route planning and dis"
      }
     ],
     "data": {
      "tools": [
       {
        "domain": "docs",
        "tool": "search_docs",
        "ok": true,
        "level": 1,
        "sources": [
         "rag.chunk:pub-press-2025-10-fleetpilot-3@v1#0",
         "rag.chunk:pub-press-2026-07-rutamar@v1#0",
         "rag.chunk:pub-fleetpilot@v1#0",
         "rag.chunk:pub-jd-R-02@v1#0",
         "rag.chunk:pub-jd-R-03@v1#0"
        ],
        "denied": []
       },
       {
        "domain": "docs",
        "tool": "get_doc",
        "ok": true,
        "level": 1,
        "sources": [
         "doc:pub-press-2025-10-fleetpilot-3"
        ],
        "denied": []
       }
      ],
      "dropped_citations": 0,
      "flagged_sources": []
     }
    },
    "hwm": 2,
    "route": "external",
    "events": [
     {
      "t": "classify",
      "level": 1,
      "findings": []
     },
     {
      "t": "retrieval",
      "count": 8,
      "level": 2,
      "collections": [
       "general",
       "public"
      ]
     },
     {
      "t": "hwm",
      "from": 1,
      "to": 2,
      "reason": "retrieval:qa"
     },
     {
      "t": "tool",
      "domain": "docs",
      "tool": "search_docs",
      "decision": "allow",
      "level": 1,
      "ok": true,
      "denied": []
     },
     {
      "t": "tool",
      "domain": "docs",
      "tool": "get_doc",
      "decision": "allow",
      "level": 1,
      "ok": true,
      "denied": []
     },
     {
      "t": "route",
      "route": "external",
      "level": 2,
      "reason": "route_by_level"
     },
     {
      "t": "citations",
      "kept": 1,
      "dropped": 0
     }
    ],
    "pending_approval": null,
    "error": null
   }
  },
  {
   "persona_key": "sales_staff",
   "app": "qa",
   "input": "Havenmeer 的合同什么时候续约？我们需要提前多久通知？",
   "run": {
    "run_id": "run_01m4d8r4c5w05n23d0es",
    "thread_id": "thr_01m4d8r4c3g9wjz8mkmr",
    "app": "qa",
    "status": "done",
    "output": {
     "text": "根据现有资料，我只能确认以下内容：\n\n**续约时间**：Havenmeer 的续约（Havenmeer renewal 2026）目前处于 negotiation 阶段，金额 EUR 132,000.00 [doc:sales-plan-A-0002]。资料中只标注了“2026”，没有给出具体的续约日期。\n\n**提前通知期限**：现有资料中没有关于需要提前多久通知的任何信息。合同条款、通知期限这类内容不在我检索到的来源中。\n\n建议：具体续约日期和通知期限需要查阅合同本身，或联系负责该客户的同事（Procurement Manager 为 Charlotte Teunissen [doc:crm.contact:CT-00005]）。",
     "citations": [
      {
       "doc_id": "sales-plan-A-0002",
       "title": "Account plan Havenmeer Distributie B.V.",
       "level": 3,
       "collection": "sales",
       "snippet": "# Account plan: Havenmeer Distributie B.V.\n\n## Snapshot\nRetail distribution, Rotterdam, about 860 employees and 310 vehicles. Status customer, tier key, ARR EUR 132,000.00.\n\n## Buying centre\n- Esila B"
      },
      {
       "doc_id": "crm.contact:CT-00005",
       "title": "crm.contact:CT-00005",
       "level": 3,
       "collection": "tool:crm",
       "snippet": ""
      }
     ],
     "data": {
      "tools": [
       {
        "domain": "crm",
        "tool": "search_accounts",
        "ok": true,
        "level": 3,
        "sources": [
         "crm.account:A-0002"
        ],
        "denied": []
       },
       {
        "domain": "crm",
        "tool": "get_account",
        "ok": true,
        "level": 3,
        "sources": [
         "crm.account:A-0002",
         "crm.contact:CT-00004",
         "crm.contact:CT-00005",
         "crm.contact:CT-00006"
        ],
        "denied": []
       },
       {
        "domain": "docs",
        "tool": "search_docs",
        "ok": true,
        "level": 3,
        "sources": [
         "rag.chunk:sales-plan-A-0002@v1#0",
         "rag.chunk:gen-ai-usage-guideline@v1#0",
         "rag.chunk:GDPR-Art-14@v1#0",
         "rag.chunk:hr-handbook-onboarding@v1#0",
         "rag.chunk:GDPR-Art-30@v1#0",
         "rag.chunk:GDPR-Art-28@v1#0",
         "rag.chunk:GDPR-Art-49@v1#0",
         "rag.chunk:GDPR-Art-13@v1#0"
        ],
        "denied": []
       },
       {
        "domain": "docs",
        "tool": "search_docs",
        "ok": true,
        "level": 3,
        "sources": [
         "rag.chunk:gen-expense-policy@v1#0",
         "rag.chunk:sales-plan-A-0002@v1#0",
         "rag.chunk:gen-ai-usage-guideline@v1#0",
         "rag.chunk:pub-trust@v1#0",
         "rag.chunk:GDPR-Art-14@v1#0",
         "rag.chunk:gen-data-protection-policy@v1#0",
         "rag.chunk:gen-code-of-conduct@v1#0",
         "rag.chunk:gen-it-security-policy@v2#0"
        ],
        "denied": []
       }
      ],
      "dropped_citations": 0,
      "flagged_sources": []
     }
    },
    "hwm": 3,
    "route": "local",
    "events": [
     {
      "t": "classify",
      "level": 1,
      "findings": []
     },
     {
      "t": "retrieval",
      "count": 1,
      "level": 3,
      "collections": [
       "general",
       "legal_regs",
       "public",
       "sales"
      ]
     },
     {
      "t": "hwm",
      "from": 1,
      "to": 3,
      "reason": "retrieval:qa"
     },
     {
      "t": "notice",
      "code": "local_mode",
      "zh": "本会话已切换到本地模式",
      "en": "This session now uses the local model"
     },
     {
      "t": "tool",
      "domain": "crm",
      "tool": "search_accounts",
      "decision": "allow",
      "level": 3,
      "ok": true,
      "denied": []
     },
     {
      "t": "tool",
      "domain": "crm",
      "tool": "get_account",
      "decision": "allow",
      "level": 3,
      "ok": true,
      "denied": []
     },
     {
      "t": "tool",
      "domain": "docs",
      "tool": "search_docs",
      "decision": "allow",
      "level": 3,
      "ok": true,
      "denied": []
     },
     {
      "t": "tool",
      "domain": "docs",
      "tool": "search_docs",
      "decision": "allow",
      "level": 3,
      "ok": true,
      "denied": []
     },
     {
      "t": "route",
      "route": "local",
      "level": 3,
      "reason": "route_by_level"
     },
     {
      "t": "citations",
      "kept": 2,
      "dropped": 0
     }
    ],
    "pending_approval": null,
    "error": null
   }
  },
  {
   "persona_key": "sales_staff",
   "app": "qa",
   "input": "Havenmeer 的合同什么时候续约？我们需要提前多久通知？",
   "switches": {
    "local_down": true
   },
   "run": {
    "run_id": "run_01m4d8ra7eq2d5em3578",
    "thread_id": "thr_01m4d8ra7c76dbhpgck7",
    "app": "qa",
    "status": "error",
    "output": {
     "text": "",
     "citations": [],
     "data": {}
    },
    "hwm": 3,
    "route": "local",
    "events": [
     {
      "t": "classify",
      "level": 1,
      "findings": []
     },
     {
      "t": "retrieval",
      "count": 1,
      "level": 3,
      "collections": [
       "general",
       "legal_regs",
       "public",
       "sales"
      ]
     },
     {
      "t": "hwm",
      "from": 1,
      "to": 3,
      "reason": "retrieval:qa"
     },
     {
      "t": "notice",
      "code": "local_mode",
      "zh": "本会话已切换到本地模式",
      "en": "This session now uses the local model"
     },
     {
      "t": "route",
      "route": "local",
      "level": 3,
      "reason": "local_down"
     }
    ],
    "pending_approval": null,
    "error": {
     "code": "local_unavailable",
     "message": "The local model is unavailable. Confidential requests never fall back to the external route; try again later."
    }
   }
  },
  {
   "persona_key": "eng_staff",
   "app": "qa",
   "input": "Havenmeer 的合同什么时候续约？我们需要提前多久通知？",
   "run": {
    "run_id": "run_01m4d8rakp110m9exyyg",
    "thread_id": "thr_01m4d8rakng9j0xr4c2m",
    "app": "qa",
    "status": "done",
    "output": {
     "text": "我无法回答这个问题，因为没有提供任何来源，也没有可用的工具结果。\n\n请提供相关文档或工具结果，我会根据其中的内容回答，并按 格式引用来源。",
     "citations": [],
     "data": {
      "tools": [],
      "dropped_citations": 1,
      "flagged_sources": []
     }
    },
    "hwm": 1,
    "route": "external",
    "events": [
     {
      "t": "classify",
      "level": 1,
      "findings": []
     },
     {
      "t": "retrieval",
      "count": 0,
      "level": 1,
      "collections": [
       "eng",
       "general",
       "legal_regs",
       "public"
      ]
     },
     {
      "t": "route",
      "route": "external",
      "level": 1,
      "reason": "route_by_level"
     },
     {
      "t": "citations",
      "kept": 0,
      "dropped": 1
     }
    ],
    "pending_approval": null,
    "error": null
   }
  },
  {
   "persona_key": "ceo",
   "app": "qa",
   "input": "Havenmeer 的合同什么时候续约？我们需要提前多久通知？",
   "run": {
    "run_id": "run_01m4d8rdmf9q8psm683b",
    "thread_id": "thr_01m4d8rdmeb03m0rjjpz",
    "app": "qa",
    "status": "done",
    "output": {
     "text": "Havenmeer Distributie B.V. (account A-0002) has one contract in the next 12 months:\n\n- **Contract C-0003** — order form, expires **2026-11-30**, auto-renews: yes, notice period: **45 days**, notice deadline: **2026-10-16** [doc:legal.contract:C-0003]\n\nSo the renewal date is 2026-11-30, and you must give notice at least 45 days in advance — by **2026-10-16** at the latest. Note that this deadline is only about two weeks away from today (2026-10-01).\n\nAccount context: Havenmeer Distributie B.V. is a key-tier customer in Rotterdam, NL [doc:crm.account:A-0002]. Relevant contacts include Charlotte Teunissen (Procurement Manager) [doc:crm.contact:CT-00005].",
     "citations": [
      {
       "doc_id": "legal.contract:C-0003",
       "title": "legal.contract:C-0003",
       "level": 3,
       "collection": "tool:legal",
       "snippet": ""
      },
      {
       "doc_id": "crm.account:A-0002",
       "title": "crm.account:A-0002",
       "level": 3,
       "collection": "tool:crm",
       "snippet": ""
      },
      {
       "doc_id": "crm.contact:CT-00005",
       "title": "crm.contact:CT-00005",
       "level": 3,
       "collection": "tool:crm",
       "snippet": ""
      }
     ],
     "data": {
      "tools": [
       {
        "domain": "crm",
        "tool": "search_accounts",
        "ok": true,
        "level": 3,
        "sources": [
         "crm.account:A-0002"
        ],
        "denied": []
       },
       {
        "domain": "legal",
        "tool": "contracts_expiring",
        "ok": true,
        "level": 1,
        "sources": [],
        "denied": []
       },
       {
        "domain": "crm",
        "tool": "get_account",
        "ok": true,
        "level": 3,
        "sources": [
         "crm.account:A-0002",
         "crm.contact:CT-00004",
         "crm.contact:CT-00005",
         "crm.contact:CT-00006"
        ],
        "denied": []
       },
       {
        "domain": "legal",
        "tool": "contracts_expiring",
        "ok": true,
        "level": 3,
        "sources": [
         "legal.contract:C-0003",
         "legal.contract:C-0025",
         "legal.contract:C-0015",
         "legal.contract:C-0005",
         "legal.contract:C-0030",
         "legal.contract:C-0009",
         "legal.contract:C-0016"
        ],
        "denied": []
       }
      ],
      "dropped_citations": 0,
      "flagged_sources": []
     }
    },
    "hwm": 3,
    "route": "local",
    "events": [
     {
      "t": "classify",
      "level": 1,
      "findings": []
     },
     {
      "t": "retrieval",
      "count": 0,
      "level": 1,
      "collections": [
       "exec",
       "general",
       "legal_regs",
       "public"
      ]
     },
     {
      "t": "tool",
      "domain": "crm",
      "tool": "search_accounts",
      "decision": "allow",
      "level": 3,
      "ok": true,
      "denied": []
     },
     {
      "t": "hwm",
      "from": 1,
      "to": 3,
      "reason": "tool:crm.search_accounts"
     },
     {
      "t": "notice",
      "code": "local_mode",
      "zh": "本会话已切换到本地模式",
      "en": "This session now uses the local model"
     },
     {
      "t": "tool",
      "domain": "legal",
      "tool": "contracts_expiring",
      "decision": "allow",
      "level": 1,
      "ok": true,
      "denied": []
     },
     {
      "t": "tool",
      "domain": "crm",
      "tool": "get_account",
      "decision": "allow",
      "level": 3,
      "ok": true,
      "denied": []
     },
     {
      "t": "tool",
      "domain": "legal",
      "tool": "contracts_expiring",
      "decision": "allow",
      "level": 3,
      "ok": true,
      "denied": []
     },
     {
      "t": "route",
      "route": "local",
      "level": 3,
      "reason": "route_by_level"
     },
     {
      "t": "citations",
      "kept": 3,
      "dropped": 0
     }
    ],
    "pending_approval": null,
    "error": null
   }
  },
  {
   "persona_key": "eng_staff",
   "app": "qa",
   "input": "P-02 为什么一直卡在测试阶段？",
   "run": {
    "run_id": "run_01m4d8rm2qcqfhe1vesr",
    "thread_id": "thr_01m4d8rm2nwn152k217n",
    "app": "qa",
    "status": "done",
    "output": {
     "text": "P-02（Donauzeile cold-chain routing）目前确实仍处于 **testing** 阶段，自 2026-07-14 起未推进，健康状态为 **red**，计划上线日期为 2026-08-31 [doc:eng.project:P-02]。\n\n从工单情况看，卡在测试阶段的主要原因是有多项测试/缺陷类工作尚未关闭：\n\n**未关闭的缺陷（bug）**\n- FP-1297：取消站点时 ETA 偏差超过 10 分钟（in_progress）[doc:eng.ticket:FP-1297]\n- FP-1272：收费道路场景下 ETA 偏差超过 10 分钟（open）[doc:eng.ticket:FP-1272]\n- FP-1249：取消站点时 ETA 偏差超过 10 分钟（open）[doc:eng.ticket:FP-1249]\n- FP-1220：收费道路场景下调度看板卡死（open）[doc:eng.ticket:FP-1220]\n- FP-1239：地图瓦片过期后产生重复站点（in_progress）[doc:eng.ticket:FP-1239]\n- FP-1263：CSV 导入拒绝司机休息记录（in_progress）[doc:eng.ticket:FP-1263]\n- FP-1214：路径优化器在拆分配送时忽略司机休息（open）[doc:eng.ticket:FP-1214]\n\n**未关闭的测试相关任务/杂项**\n- FP-1295：升级 routing-api 到当前小版本（open）[doc:eng.ticket:FP-1295]\n- FP-1292：清理 routing-api 中的死代码（in_progress）[doc:eng.ticket:FP-1292]\n- FP-1271：轮换 telematics-ingest 的测试夹具（in_progress）[doc:eng.ticket:FP-1271]\n- FP-1257：编写 eta-service 运行手册步骤（open）[doc:eng.ticket:FP-1257]\n\n**未关闭的需求（story）**\n- FP-1283：调度员希望查看每条路线的 CO2 估算（open）[doc:eng.ticket:FP-1283]\n- FP-1281：车队经理希望将站点锁定到指定司机（in_progress）[doc:eng.ticket:FP-1281]\n- FP-1209：调度员希望只重新规划下午波次（in_progress）[doc:eng.ticket:FP-1209]\n\n值得注意的是，QA 签署工单 FP-1287（冷链路回归测试完成）已标记为 done [doc:eng.ticket:FP-1287]，但上述多个缺陷和任务仍未关闭，说明测试阶段虽已通过一轮回归，但后续发现的缺陷与未完成工作使项目无法进入下一阶段。",
     "citations": [
      {
       "doc_id": "eng.project:P-02",
       "title": "eng.project:P-02",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1297",
       "title": "eng.ticket:FP-1297",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1272",
       "title": "eng.ticket:FP-1272",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1249",
       "title": "eng.ticket:FP-1249",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1220",
       "title": "eng.ticket:FP-1220",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1239",
       "title": "eng.ticket:FP-1239",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1263",
       "title": "eng.ticket:FP-1263",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1214",
       "title": "eng.ticket:FP-1214",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1295",
       "title": "eng.ticket:FP-1295",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1292",
       "title": "eng.ticket:FP-1292",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1271",
       "title": "eng.ticket:FP-1271",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1257",
       "title": "eng.ticket:FP-1257",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1283",
       "title": "eng.ticket:FP-1283",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1281",
       "title": "eng.ticket:FP-1281",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1209",
       "title": "eng.ticket:FP-1209",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      },
      {
       "doc_id": "eng.ticket:FP-1287",
       "title": "eng.ticket:FP-1287",
       "level": 3,
       "collection": "tool:code",
       "snippet": ""
      }
     ],
     "data": {
      "tools": [
       {
        "domain": "code",
        "tool": "get_project_status",
        "ok": true,
        "level": 3,
        "sources": [
         "eng.project:P-02"
        ],
        "denied": []
       },
       {
        "domain": "code",
        "tool": "search_tickets",
        "ok": true,
        "level": 3,
        "sources": [
         "eng.ticket:FP-1283",
         "eng.ticket:FP-1295",
         "eng.ticket:FP-1297",
         "eng.ticket:FP-1292",
         "eng.ticket:FP-1286",
         "eng.ticket:FP-1287",
         "eng.ticket:FP-1257",
         "eng.ticket:FP-1278",
         "eng.ticket:FP-1281",
         "eng.ticket:FP-1271",
         "eng.ticket:FP-1263",
         "eng.ticket:FP-1272",
         "eng.ticket:FP-1236",
         "eng.ticket:FP-1209",
         "eng.ticket:FP-1237",
         "eng.ticket:FP-1249",
         "eng.ticket:FP-1220",
         "eng.ticket:FP-1224",
         "eng.ticket:FP-1239",
         "eng.ticket:FP-1214"
        ],
        "denied": []
       }
      ],
      "dropped_citations": 0,
      "flagged_sources": []
     }
    },
    "hwm": 3,
    "route": "local",
    "events": [
     {
      "t": "classify",
      "level": 1,
      "findings": []
     },
     {
      "t": "retrieval",
      "count": 0,
      "level": 1,
      "collections": [
       "eng",
       "general",
       "legal_regs",
       "public"
      ]
     },
     {
      "t": "tool",
      "domain": "code",
      "tool": "get_project_status",
      "decision": "allow",
      "level": 3,
      "ok": true,
      "denied": []
     },
     {
      "t": "hwm",
      "from": 1,
      "to": 3,
      "reason": "tool:code.get_project_status"
     },
     {
      "t": "notice",
      "code": "local_mode",
      "zh": "本会话已切换到本地模式",
      "en": "This session now uses the local model"
     },
     {
      "t": "tool",
      "domain": "code",
      "tool": "search_tickets",
      "decision": "allow",
      "level": 3,
      "ok": true,
      "denied": []
     },
     {
      "t": "route",
      "route": "local",
      "level": 3,
      "reason": "route_by_level"
     },
     {
      "t": "citations",
      "kept": 16,
      "dropped": 0
     }
    ],
    "pending_approval": null,
    "error": null
   }
  }
 ]
};
