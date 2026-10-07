window.FIXTURES.traces = {
 "by_run": {
  "run_01m4c0b7sal3hvn8r2qe": {
   "run_id": "run_01m4c0b7sal3hvn8r2qe",
   "spans": [
    {
     "span_id": "span_s01",
     "parent_id": null,
     "name": "qa",
     "kind": "node",
     "level": 3,
     "status": "ok",
     "started_at": "2026-10-07T09:12:01.100Z",
     "ended_at": "2026-10-07T09:12:05.920Z",
     "attrs": {
      "agent": "qa"
     },
     "input_text": "Havenmeer 的合同什么时候续约？我们需要提前多久通知？",
     "output_text": null
    },
    {
     "span_id": "span_s02",
     "parent_id": "span_s01",
     "name": "classify_input",
     "kind": "node",
     "level": 1,
     "status": "ok",
     "started_at": "2026-10-07T09:12:01.110Z",
     "ended_at": "2026-10-07T09:12:01.140Z",
     "attrs": {
      "findings": []
     },
     "input_text": "Havenmeer 的合同什么时候续约？我们需要提前多久通知？",
     "output_text": "L1"
    },
    {
     "span_id": "span_s03",
     "parent_id": "span_s01",
     "name": "retrieve",
     "kind": "retrieval",
     "level": 3,
     "status": "ok",
     "started_at": "2026-10-07T09:12:01.150Z",
     "ended_at": "2026-10-07T09:12:01.480Z",
     "attrs": {
      "purpose": "qa",
      "k": 8,
      "hits": 6,
      "collections": [
       "public",
       "general",
       "legal_regs",
       "sales"
      ]
     },
     "input_text": "Havenmeer contract renewal notice",
     "output_text": "6 hits: sales-plan-A-0002, gen-employee-handbook, …"
    },
    {
     "span_id": "span_s04",
     "parent_id": "span_s01",
     "name": "crm.get_account",
     "kind": "tool",
     "level": 3,
     "status": "ok",
     "started_at": "2026-10-07T09:12:01.500Z",
     "ended_at": "2026-10-07T09:12:01.860Z",
     "attrs": {
      "domain": "crm",
      "decision": "allow",
      "rule_id": "tool-crm-sales"
     },
     "input_text": "{\"account_id\": \"A-0002\"}",
     "output_text": "Havenmeer Distributie B.V. · key · ARR EUR 132,000 · contact ⟨person_name⟩ ⟨email⟩"
    },
    {
     "span_id": "span_s05",
     "parent_id": "span_s01",
     "name": "legal.contracts_expiring",
     "kind": "policy",
     "level": 1,
     "status": "ok",
     "started_at": "2026-10-07T09:12:01.870Z",
     "ended_at": "2026-10-07T09:12:01.875Z",
     "attrs": {
      "domain": "legal",
      "decision": "deny",
      "rule_id": "default-deny"
     },
     "input_text": null,
     "output_text": "denied: tool rule"
    },
    {
     "span_id": "span_s06",
     "parent_id": "span_s01",
     "name": "answer",
     "kind": "llm",
     "level": 3,
     "status": "ok",
     "started_at": "2026-10-07T09:12:01.900Z",
     "ended_at": "2026-10-07T09:12:05.700Z",
     "attrs": {
      "route": "local",
      "model": "deepseek-flash",
      "purpose": "qa.answer",
      "prompt_tokens": 2310,
      "completion_tokens": 214
     },
     "input_text": "System: answer only from the context… Context: [doc:sales-plan-A-0002] … contact ⟨person_name⟩ (⟨email⟩)…",
     "output_text": "Havenmeer Distributie B.V.（A-0002）的订单 C-0003 将于 2026-11-30 到期…"
    },
    {
     "span_id": "span_s07",
     "parent_id": "span_s01",
     "name": "citation_check",
     "kind": "node",
     "level": 3,
     "status": "ok",
     "started_at": "2026-10-07T09:12:05.710Z",
     "ended_at": "2026-10-07T09:12:05.900Z",
     "attrs": {
      "citations": 2,
      "dropped": 0
     },
     "input_text": null,
     "output_text": "2 citations verified"
    }
   ]
  },
  "run_01m4c0d9ceo2hvn6p4wa": {
   "run_id": "run_01m4c0d9ceo2hvn6p4wa",
   "spans": [
    {
     "span_id": "span_c01",
     "parent_id": null,
     "name": "qa",
     "kind": "node",
     "level": 4,
     "status": "ok",
     "started_at": "2026-10-07T09:12:20.000Z",
     "ended_at": "2026-10-07T09:12:26.400Z",
     "attrs": {
      "agent": "qa"
     },
     "input_text": null,
     "output_text": null
    },
    {
     "span_id": "span_c02",
     "parent_id": "span_c01",
     "name": "classify_input",
     "kind": "node",
     "level": 1,
     "status": "ok",
     "started_at": "2026-10-07T09:12:20.010Z",
     "ended_at": "2026-10-07T09:12:20.030Z",
     "attrs": {
      "findings": []
     },
     "input_text": "Havenmeer 的合同什么时候续约？我们需要提前多久通知？",
     "output_text": "L1"
    },
    {
     "span_id": "span_c03",
     "parent_id": "span_c01",
     "name": "retrieve",
     "kind": "retrieval",
     "level": 4,
     "status": "ok",
     "started_at": "2026-10-07T09:12:20.040Z",
     "ended_at": "2026-10-07T09:12:20.410Z",
     "attrs": {
      "purpose": "qa",
      "k": 8,
      "hits": 8,
      "collections": [
       "public",
       "general",
       "legal_regs",
       "exec"
      ]
     },
     "input_text": null,
     "output_text": null
    },
    {
     "span_id": "span_c04",
     "parent_id": "span_c01",
     "name": "crm.get_account",
     "kind": "tool",
     "level": 3,
     "status": "ok",
     "started_at": "2026-10-07T09:12:20.420Z",
     "ended_at": "2026-10-07T09:12:20.700Z",
     "attrs": {
      "domain": "crm",
      "decision": "allow"
     },
     "input_text": "{\"account_id\": \"A-0002\"}",
     "output_text": "Havenmeer Distributie B.V. · key · ARR EUR 132,000"
    },
    {
     "span_id": "span_c05",
     "parent_id": "span_c01",
     "name": "legal.contracts_expiring",
     "kind": "tool",
     "level": 3,
     "status": "ok",
     "started_at": "2026-10-07T09:12:20.710Z",
     "ended_at": "2026-10-07T09:12:20.990Z",
     "attrs": {
      "domain": "legal",
      "decision": "allow"
     },
     "input_text": "{\"within_days\": 90}",
     "output_text": "C-0003 · 2026-11-30 · auto-renew · 45 days"
    },
    {
     "span_id": "span_c06",
     "parent_id": "span_c01",
     "name": "answer",
     "kind": "llm",
     "level": 4,
     "status": "ok",
     "started_at": "2026-10-07T09:12:21.000Z",
     "ended_at": "2026-10-07T09:12:26.200Z",
     "attrs": {
      "route": "local",
      "model": "deepseek-flash",
      "purpose": "qa.answer",
      "prompt_tokens": 3120,
      "completion_tokens": 251
     },
     "input_text": null,
     "output_text": null
    },
    {
     "span_id": "span_c07",
     "parent_id": "span_c01",
     "name": "citation_check",
     "kind": "node",
     "level": 4,
     "status": "ok",
     "started_at": "2026-10-07T09:12:26.210Z",
     "ended_at": "2026-10-07T09:12:26.390Z",
     "attrs": {
      "citations": 3
     },
     "input_text": null,
     "output_text": null
    }
   ]
  },
  "run_01m4c0a1pub0fp30x7k2": {
   "run_id": "run_01m4c0a1pub0fp30x7k2",
   "spans": [
    {
     "span_id": "span_p01",
     "parent_id": null,
     "name": "qa",
     "kind": "node",
     "level": 1,
     "status": "ok",
     "started_at": "2026-10-07T09:12:40.000Z",
     "ended_at": "2026-10-07T09:12:42.900Z",
     "attrs": {
      "agent": "qa"
     },
     "input_text": "FleetPilot 3.0 有哪些新功能？",
     "output_text": null
    },
    {
     "span_id": "span_p02",
     "parent_id": "span_p01",
     "name": "retrieve",
     "kind": "retrieval",
     "level": 1,
     "status": "ok",
     "started_at": "2026-10-07T09:12:40.020Z",
     "ended_at": "2026-10-07T09:12:40.300Z",
     "attrs": {
      "purpose": "qa",
      "hits": 6
     },
     "input_text": "FleetPilot 3.0 new features",
     "output_text": "6 hits: pub-press-2025-10-fleetpilot-3, pub-fleetpilot, …"
    },
    {
     "span_id": "span_p03",
     "parent_id": "span_p01",
     "name": "answer",
     "kind": "llm",
     "level": 1,
     "status": "ok",
     "started_at": "2026-10-07T09:12:40.310Z",
     "ended_at": "2026-10-07T09:12:42.800Z",
     "attrs": {
      "route": "external",
      "model": "deepseek-flash",
      "purpose": "qa.answer",
      "prompt_tokens": 1650,
      "completion_tokens": 188
     },
     "input_text": "System: answer only from the context…",
     "output_text": "FleetPilot 3.0 于 2025-10-07 发布…"
    }
   ]
  },
  "run_01m4c0f4sal8dwn2q9hc": {
   "run_id": "run_01m4c0f4sal8dwn2q9hc",
   "spans": [
    {
     "span_id": "span_d01",
     "parent_id": null,
     "name": "qa",
     "kind": "node",
     "level": 3,
     "status": "error",
     "started_at": "2026-10-07T09:12:50.000Z",
     "ended_at": "2026-10-07T09:12:50.800Z",
     "attrs": {
      "agent": "qa"
     },
     "input_text": "Havenmeer 的合同什么时候续约？我们需要提前多久通知？",
     "output_text": null
    },
    {
     "span_id": "span_d02",
     "parent_id": "span_d01",
     "name": "retrieve",
     "kind": "retrieval",
     "level": 3,
     "status": "ok",
     "started_at": "2026-10-07T09:12:50.010Z",
     "ended_at": "2026-10-07T09:12:50.300Z",
     "attrs": {
      "hits": 6
     },
     "input_text": null,
     "output_text": "6 hits"
    },
    {
     "span_id": "span_d03",
     "parent_id": "span_d01",
     "name": "answer",
     "kind": "llm",
     "level": 3,
     "status": "error",
     "started_at": "2026-10-07T09:12:50.400Z",
     "ended_at": "2026-10-07T09:12:50.420Z",
     "attrs": {
      "route": "local",
      "error_type": "LocalUnavailable",
      "reason": "local_down"
     },
     "input_text": null,
     "output_text": null
    }
   ]
  }
 }
};
