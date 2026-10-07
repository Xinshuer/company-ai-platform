window.FIXTURES.audit = {
 "entries": [
  {
   "seq": 4810,
   "ts": "2026-10-07T08:02:11.204Z",
   "actor": "svc:ingest",
   "act_chain": [],
   "event": "owner_job",
   "action": null,
   "resource": null,
   "decision": "allow",
   "reason": "ingest",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "documents": 143,
    "chunks": 1287
   }
  },
  {
   "seq": 4811,
   "ts": "2026-10-07T09:11:58.031Z",
   "actor": "u0011",
   "act_chain": [],
   "event": "demo_login",
   "action": null,
   "resource": null,
   "decision": "allow",
   "reason": "persona picker",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "persona_key": "sales_staff"
   }
  },
  {
   "seq": 4812,
   "ts": "2026-10-07T09:12:01.480Z",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "retrieval",
   "action": "search",
   "resource": null,
   "decision": "allow",
   "reason": null,
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4c0b7sal3hvn8r2qe",
   "details": {
    "hits": 6,
    "collections": [
     "public",
     "general",
     "legal_regs",
     "sales"
    ]
   }
  },
  {
   "seq": 4813,
   "ts": "2026-10-07T09:12:01.860Z",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "crm.get_account",
   "resource": "crm.account:A-0002",
   "decision": "allow",
   "reason": "tool-crm-sales",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4c0b7sal3hvn8r2qe",
   "details": {
    "jti": "8c1f0e7a42d94b1d"
   }
  },
  {
   "seq": 4814,
   "ts": "2026-10-07T09:12:01.875Z",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "policy_decision",
   "action": "legal.contracts_expiring",
   "resource": null,
   "decision": "deny",
   "reason": "default-deny: no tool rule for sales",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": "run_01m4c0b7sal3hvn8r2qe",
   "details": {}
  },
  {
   "seq": 4815,
   "ts": "2026-10-07T09:12:05.700Z",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": null,
   "resource": null,
   "decision": "allow",
   "reason": "route_by_level",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4c0b7sal3hvn8r2qe",
   "details": {
    "hwm": 3,
    "content_level": 1,
    "findings": [],
    "usage": {
     "prompt_tokens": 2310,
     "completion_tokens": 214
    },
    "latency_ms": 3800
   }
  },
  {
   "seq": 4816,
   "ts": "2026-10-07T09:14:22.512Z",
   "actor": "u0011",
   "act_chain": [
    "agent:sales_profile"
   ],
   "event": "proposal_created",
   "action": "crm.send_email",
   "resource": "prop_01m4c0h6sal2eml7w3kd",
   "decision": "allow",
   "reason": null,
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4c0h5sal6prf9d2ue",
   "details": {
    "tier": 3,
    "params_hash": "b4b446c4db6cbe1ca3974741307f6204b108cb412a88d0a4be6ac2939aff73db"
   }
  },
  {
   "seq": 4817,
   "ts": "2026-10-07T10:02:40.003Z",
   "actor": "u0060",
   "act_chain": [],
   "event": "outage_switch",
   "action": "local_down",
   "resource": null,
   "decision": "allow",
   "reason": "maintenance drill: on-prem GPU node patching",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "on": true
   }
  },
  {
   "seq": 4818,
   "ts": "2026-10-07T10:03:12.441Z",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": null,
   "resource": null,
   "decision": "deny",
   "reason": "local_down",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4c0f4sal8dwn2q9hc",
   "details": {
    "hwm": 3
   }
  },
  {
   "seq": 4819,
   "ts": "2026-10-07T10:03:30.118Z",
   "actor": "u0021",
   "act_chain": [
    "agent:qa"
   ],
   "event": "route_decision",
   "action": null,
   "resource": null,
   "decision": "reroute",
   "reason": "external_down_fallback",
   "route": "local",
   "level": 2,
   "approval_id": null,
   "run_id": null,
   "details": {
    "from": "external"
   }
  },
  {
   "seq": 4820,
   "ts": "2026-10-07T10:11:05.774Z",
   "actor": "u0060",
   "act_chain": [],
   "event": "outage_switch",
   "action": "local_down",
   "resource": null,
   "decision": "allow",
   "reason": "drill finished",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "on": false
   }
  },
  {
   "seq": 4821,
   "ts": "2026-10-07T10:20:44.902Z",
   "actor": "u0010",
   "act_chain": [],
   "event": "proposal_approved",
   "action": "crm.send_email",
   "resource": "prop_01m4bz2k8sal4wks9n1p",
   "decision": "allow",
   "reason": "four-eyes: head of sales",
   "route": null,
   "level": null,
   "approval_id": "prop_01m4bz2k8sal4wks9n1p",
   "run_id": null,
   "details": {
    "tier": 3
   }
  },
  {
   "seq": 4822,
   "ts": "2026-10-07T10:20:45.160Z",
   "actor": "svc:executor",
   "act_chain": [],
   "event": "proposal_executed",
   "action": "crm.send_email",
   "resource": "prop_01m4bz2k8sal4wks9n1p",
   "decision": "allow",
   "reason": null,
   "route": null,
   "level": null,
   "approval_id": "prop_01m4bz2k8sal4wks9n1p",
   "run_id": null,
   "details": {
    "outbox_id": "OB-000017",
    "params_hash_rechecked": true
   }
  },
  {
   "seq": 4823,
   "ts": "2026-10-07T10:31:09.006Z",
   "actor": "u0061",
   "act_chain": [],
   "event": "audit_read",
   "action": "read_log",
   "resource": null,
   "decision": "allow",
   "reason": null,
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "after_seq": 4800,
    "limit": 200
   }
  }
 ],
 "chain": {
  "ok": true,
  "checked": 4823,
  "first_bad_seq": null
 }
};
