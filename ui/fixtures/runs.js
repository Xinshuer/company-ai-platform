window.FIXTURES.runs = {
 "items": [
  {
   "persona_key": "*",
   "app": "qa",
   "input": "FleetPilot 3.0 有哪些新功能？",
   "run": {
    "run_id": "run_01m4c0a1pub0fp30x7k2",
    "thread_id": "thr_01m4c0a1pubthr9q2w",
    "app": "qa",
    "status": "done",
    "output": {
     "text": "FleetPilot 3.0 于 2025-10-07 发布，重点是多仓库（multi-depot）优化：\n1. 一次规划可以跨多个仓库分配订单和车辆，不再按仓库分别排线；\n2. 新的优化引擎支持时间窗、车辆容量和司机班次等约束；\n3. 调度员可以在地图上手动调整，系统会实时重新计算剩余路线。\n冷链模块（温度窗约束）是单独的扩展模块。公开资料可参考：https://www.kestrelbyte.example/fleetpilot",
     "citations": [
      {
       "doc_id": "pub-press-2025-10-fleetpilot-3",
       "title": "FleetPilot 3.0 brings multi-depot optimisation",
       "level": 1,
       "collection": "public",
       "snippet": "FleetPilot 3.0 plans orders and vehicles across several depots in one run…"
      },
      {
       "doc_id": "pub-fleetpilot",
       "title": "FleetPilot product overview",
       "level": 1,
       "collection": "public",
       "snippet": "Route planning and dispatch for logistics fleets: time windows, capacities, driver shifts…"
      },
      {
       "doc_id": "pub-coldchain",
       "title": "FleetPilot cold-chain module",
       "level": 1,
       "collection": "public",
       "snippet": "Temperature-window constraints for refrigerated fleets, available as an add-on…"
      }
     ],
     "data": {}
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
      "count": 6,
      "level": 1,
      "collections": [
       "public",
       "general"
      ]
     },
     {
      "t": "route",
      "route": "external",
      "level": 1,
      "reason": "route_by_level"
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
    "run_id": "run_01m4c0b7sal3hvn8r2qe",
    "thread_id": "thr_01m4c0b7salthr4kz1",
    "app": "qa",
    "status": "done",
    "output": {
     "text": "Havenmeer Distributie B.V.（A-0002，你负责的客户）的订单 C-0003 将于 2026-11-30 到期，并且会自动续约。\n合同要求提前 45 天书面通知，所以如果要调整条款或价格，最晚要在 2026-10-16 之前发出通知——距离今天（2026-10-01）只剩 15 天。\n客户计划里提到 P-04 门店配送优化项目已进入上线阶段，可以把它作为续约沟通的切入点。\n注意：你没有法务合同库的权限，合同到期查询被拒绝；以上到期日期来自 CRM 和客户计划。如需合同原文，请向法务提出合同请求。",
     "citations": [
      {
       "doc_id": "crm.account:A-0002",
       "title": "Havenmeer Distributie B.V. (CRM)",
       "level": 3,
       "collection": "crm",
       "snippet": "Tier key · ARR EUR 132,000 · renewal 2026-11-30 (auto-renew) · owner Tobias Reiner"
      },
      {
       "doc_id": "sales-plan-A-0002",
       "title": "Account plan Havenmeer Distributie B.V.",
       "level": 3,
       "collection": "sales",
       "snippet": "Order form auto-renews on 2026-11-30; 45 days' written notice. Expansion: store-delivery optimisation (P-04)…"
      }
     ],
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
      "count": 6,
      "level": 3,
      "collections": [
       "public",
       "general",
       "legal_regs",
       "sales"
      ]
     },
     {
      "t": "hwm",
      "from": 1,
      "to": 3,
      "reason": "retrieval:sales"
     },
     {
      "t": "tool",
      "domain": "crm",
      "tool": "get_account",
      "decision": "allow",
      "level": 3,
      "denied": []
     },
     {
      "t": "tool",
      "domain": "legal",
      "tool": "contracts_expiring",
      "decision": "deny",
      "level": 1,
      "denied": [
       {
        "id": "legal.contracts_expiring",
        "reason": "tool rule: legal tools are for the legal department and the CEO"
       }
      ]
     },
     {
      "t": "route",
      "route": "local",
      "level": 3,
      "reason": "route_by_level"
     },
     {
      "t": "notice",
      "code": "local_mode",
      "zh": "本会话已切换到本地模式",
      "en": "This session now uses the local model"
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
    "run_id": "run_01m4c0f4sal8dwn2q9hc",
    "thread_id": "thr_01m4c0f4salthr5v7j",
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
      "count": 6,
      "level": 3,
      "collections": [
       "public",
       "general",
       "legal_regs",
       "sales"
      ]
     },
     {
      "t": "hwm",
      "from": 1,
      "to": 3,
      "reason": "retrieval:sales"
     },
     {
      "t": "tool",
      "domain": "crm",
      "tool": "get_account",
      "decision": "allow",
      "level": 3,
      "denied": []
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
     "message": "The local model is unavailable. Confidential requests never fall back to the external route; try again later or ask the platform admin."
    }
   }
  },
  {
   "persona_key": "eng_staff",
   "app": "qa",
   "input": "Havenmeer 的合同什么时候续约？我们需要提前多久通知？",
   "run": {
    "run_id": "run_01m4c0c2eng5hvn1t8yb",
    "thread_id": "thr_01m4c0c2engthr6m3d",
    "app": "qa",
    "status": "done",
    "output": {
     "text": "我没有找到你有权限查看的、关于 Havenmeer 合同续约的资料。\nCRM 客户数据和法务合同库都不在你的权限范围内，这两个查询被拒绝了（见事件时间线）。\n公开资料和内部手册里没有这家客户的合同信息。如果你需要了解续约安排，请联系客户负责人或销售负责人 Mareike Fuchs。",
     "citations": [],
     "data": {}
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
      "count": 3,
      "level": 2,
      "collections": [
       "public",
       "general",
       "legal_regs",
       "eng"
      ]
     },
     {
      "t": "hwm",
      "from": 1,
      "to": 2,
      "reason": "retrieval:general"
     },
     {
      "t": "tool",
      "domain": "crm",
      "tool": "get_account",
      "decision": "deny",
      "level": 1,
      "denied": [
       {
        "id": "crm.get_account",
        "reason": "tool rule: CRM tools are for the sales department and the CEO"
       }
      ]
     },
     {
      "t": "tool",
      "domain": "legal",
      "tool": "contracts_expiring",
      "decision": "deny",
      "level": 1,
      "denied": [
       {
        "id": "legal.contracts_expiring",
        "reason": "tool rule: legal tools are for the legal department and the CEO"
       }
      ]
     },
     {
      "t": "route",
      "route": "external",
      "level": 2,
      "reason": "route_by_level"
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
    "run_id": "run_01m4c0d9ceo2hvn6p4wa",
    "thread_id": "thr_01m4c0d9ceothr2x8n",
    "app": "qa",
    "status": "done",
    "output": {
     "text": "Havenmeer Distributie B.V. 的订单 C-0003 于 2026-11-30 到期并自动续约，通知期 45 天，截止日是 2026-10-16。\n合同库显示这份订单年费 EUR 132,000，没有价格上调条款；如果计划在续约时调价，必须在截止日前书面通知。\nQ3 董事会更新把 Havenmeer 列为续约风险之一（P-04 上线后的成效评估还没完成）。建议由销售负责人在 10 月第二周前确认续约策略。",
     "citations": [
      {
       "doc_id": "crm.account:A-0002",
       "title": "Havenmeer Distributie B.V. (CRM)",
       "level": 3,
       "collection": "crm",
       "snippet": "Tier key · ARR EUR 132,000 · renewal 2026-11-30 (auto-renew) · owner Tobias Reiner"
      },
      {
       "doc_id": "C-0003",
       "title": "order_form C-0003 Havenmeer Distributie B.V.",
       "level": 3,
       "collection": "legal",
       "snippet": "Term ends 2026-11-30; renews automatically for 12 months unless either party gives 45 days' written notice…"
      },
      {
       "doc_id": "exec-board-update-2026-Q3",
       "title": "Board update Q3 2026 (draft)",
       "level": 4,
       "collection": "exec",
       "snippet": null
      }
     ],
     "data": {}
    },
    "hwm": 4,
    "route": "local",
    "events": [
     {
      "t": "classify",
      "level": 1,
      "findings": []
     },
     {
      "t": "retrieval",
      "count": 8,
      "level": 4,
      "collections": [
       "public",
       "general",
       "legal_regs",
       "exec"
      ]
     },
     {
      "t": "hwm",
      "from": 1,
      "to": 4,
      "reason": "retrieval:exec"
     },
     {
      "t": "tool",
      "domain": "crm",
      "tool": "get_account",
      "decision": "allow",
      "level": 3,
      "denied": []
     },
     {
      "t": "tool",
      "domain": "legal",
      "tool": "contracts_expiring",
      "decision": "allow",
      "level": 3,
      "denied": []
     },
     {
      "t": "route",
      "route": "local",
      "level": 4,
      "reason": "route_by_level"
     },
     {
      "t": "notice",
      "code": "local_mode",
      "zh": "本会话已切换到本地模式",
      "en": "This session now uses the local model"
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
    "run_id": "run_01m4c0g1eng7p02k3mz",
    "thread_id": "thr_01m4c0g1engthr8b2c",
    "app": "qa",
    "status": "done",
    "output": {
     "text": "P-02（Donauzeile 冷链路线）自 2026-07-14 起停留在测试阶段。\n最近 10 次 CI 全部失败，失败集中在温度窗约束的回归用例（2026-08-19 起）。\n注意：工单 T-0214 写着“测试已完成”，但这与 CI 结果矛盾，建议在站会上确认。",
     "citations": [
      {
       "doc_id": "code.project:P-02",
       "title": "P-02 Donauzeile cold-chain routing (status)",
       "level": 3,
       "collection": "eng",
       "snippet": "Stage: testing since 2026-07-14 · health red · last 10 CI runs failed"
      },
      {
       "doc_id": "eng-P-02-runbook",
       "title": "Donauzeile cold-chain routing runbook",
       "level": 3,
       "collection": "eng",
       "snippet": "Regression suite: temperature-window constraints must hold for every stop…"
      }
     ],
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
      "count": 5,
      "level": 3,
      "collections": [
       "public",
       "general",
       "legal_regs",
       "eng"
      ]
     },
     {
      "t": "tool",
      "domain": "code",
      "tool": "get_project_status",
      "decision": "allow",
      "level": 3,
      "denied": []
     },
     {
      "t": "hwm",
      "from": 1,
      "to": 3,
      "reason": "tool:code.get_project_status"
     },
     {
      "t": "route",
      "route": "local",
      "level": 3,
      "reason": "route_by_level"
     },
     {
      "t": "notice",
      "code": "local_mode",
      "zh": "本会话已切换到本地模式",
      "en": "This session now uses the local model"
     }
    ],
    "pending_approval": null,
    "error": null
   }
  },
  {
   "persona_key": "sales_staff",
   "app": "sales_profile",
   "input": "Havenmeer Distributie B.V.",
   "run": {
    "run_id": "run_01m4c0h5sal6prf9d2ue",
    "thread_id": "thr_01m4c0h5salthr3n6k",
    "app": "sales_profile",
    "status": "waiting_approval",
    "output": {
     "text": "Havenmeer Distributie B.V.：荷兰零售配送企业，约 210 辆车，现有客户（ARR EUR 132,000）。\n公开信号：2026 年 8 月宣布扩建鹿特丹配送中心（匹配度高）。CRM 互动：近 30 天 4 次联系，P-04 已进入上线阶段（意向高）。\n按 playbook “Sales follow-up by intent tier”（热）建议：续约前安排成效回顾电话，并介绍 2027 多仓库方案。\n已起草一封跟进邮件，发送前需要销售负责人审批。",
     "citations": [
      {
       "doc_id": "web:havenmeer-distributie.example/news/2026-08-dc",
       "title": "Havenmeer expands Rotterdam DC",
       "level": 1,
       "collection": "web",
       "snippet": "Havenmeer Distributie invests in a second distribution centre…"
      },
      {
       "doc_id": "crm.account:A-0002",
       "title": "Havenmeer Distributie B.V. (CRM)",
       "level": 3,
       "collection": "crm",
       "snippet": "4 interactions in the last 30 days · P-04 in release"
      },
      {
       "doc_id": "pb-sales-followup",
       "title": "Sales follow-up by intent tier",
       "level": 3,
       "collection": "playbooks",
       "snippet": "Hot: offer a results review before renewal; propose expansion…"
      }
     ],
     "data": {
      "fit_score": 78,
      "intent_score": 84,
      "intent_tier": "hot",
      "playbook": "pb-sales-followup@3",
      "signals": {
       "public": [
        "dc_expansion_2026_08",
        "fleet_size_200_plus"
       ],
       "crm": [
        "interactions_30d=4",
        "project_P-04=release"
       ]
      }
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
      "t": "tool",
      "domain": "web",
      "tool": "search",
      "decision": "allow",
      "level": 1,
      "denied": []
     },
     {
      "t": "route",
      "route": "external",
      "level": 1,
      "reason": "route_by_level"
     },
     {
      "t": "tool",
      "domain": "crm",
      "tool": "get_interactions",
      "decision": "allow",
      "level": 3,
      "denied": []
     },
     {
      "t": "hwm",
      "from": 1,
      "to": 3,
      "reason": "tool:crm.get_interactions"
     },
     {
      "t": "route",
      "route": "local",
      "level": 3,
      "reason": "route_by_level"
     },
     {
      "t": "notice",
      "code": "local_mode",
      "zh": "本会话已切换到本地模式",
      "en": "This session now uses the local model"
     },
     {
      "t": "approval",
      "proposal_id": "prop_01m4c0h6sal2eml7w3kd",
      "tier": 3
     }
    ],
    "pending_approval": {
     "proposal_id": "prop_01m4c0h6sal2eml7w3kd",
     "tool": "crm.send_email",
     "tier": 3,
     "summary": "Send renewal follow-up email to Havenmeer Distributie B.V. (Simon Jaceps)"
    },
    "error": null
   }
  }
 ]
};
