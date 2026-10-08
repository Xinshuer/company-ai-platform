window.FIXTURES.audit = {
 "entries": [
  {
   "seq": 3,
   "ts": "2026-10-08T08:05:43.256798+00:00",
   "actor": "u0060",
   "act_chain": [],
   "event": "demo_login",
   "action": "login",
   "resource": null,
   "decision": "allow",
   "reason": "persona_picker",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "persona_key": "platform_admin"
   }
  },
  {
   "seq": 4,
   "ts": "2026-10-08T08:05:43.428884+00:00",
   "actor": "u0060",
   "act_chain": [
    "agent:qa"
   ],
   "event": "retrieval",
   "action": "search",
   "resource": "rag:qa",
   "decision": "allow",
   "reason": "rag_filter",
   "route": null,
   "level": 2,
   "approval_id": null,
   "run_id": "run_01m4d8qy7e5c59zfjmet",
   "details": {
    "k": 8,
    "count": 8,
    "doc_ids": [
     "gen-data-protection-policy",
     "pub-fleetpilot",
     "pub-home",
     "pub-jd-R-01",
     "pub-jd-R-02",
     "pub-jd-R-06",
     "pub-press-2025-10-fleetpilot-3",
     "pub-press-2026-07-rutamar"
    ],
    "purpose": "qa",
    "min_trust": null,
    "collections": [
     "general",
     "public"
    ],
    "hit_collections": [
     "general",
     "public"
    ]
   }
  },
  {
   "seq": 5,
   "ts": "2026-10-08T08:05:45.233902+00:00",
   "actor": "u0060",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "external",
   "level": 2,
   "approval_id": null,
   "run_id": "run_01m4d8qy7e5c59zfjmet",
   "details": {
    "hwm": 2,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [],
    "egress_id": 1,
    "json_mode": false,
    "latency_ms": 1656,
    "redactions": 0,
    "tool_calls": 1,
    "public_only": false,
    "total_tokens": 478,
    "content_level": 1,
    "prompt_tokens": 398,
    "completion_tokens": 80
   }
  },
  {
   "seq": 6,
   "ts": "2026-10-08T08:05:45.340113+00:00",
   "actor": "u0060",
   "act_chain": [
    "agent:qa"
   ],
   "event": "retrieval",
   "action": "search",
   "resource": "rag:qa",
   "decision": "allow",
   "reason": "rag_filter",
   "route": null,
   "level": 1,
   "approval_id": null,
   "run_id": "run_01m4d8qy7e5c59zfjmet",
   "details": {
    "k": 5,
    "count": 5,
    "doc_ids": [
     "pub-fleetpilot",
     "pub-jd-R-02",
     "pub-jd-R-03",
     "pub-press-2025-10-fleetpilot-3",
     "pub-press-2026-07-rutamar"
    ],
    "purpose": "qa",
    "min_trust": null,
    "collections": [
     "general",
     "public"
    ],
    "hit_collections": [
     "public"
    ]
   }
  },
  {
   "seq": 7,
   "ts": "2026-10-08T08:05:45.370998+00:00",
   "actor": "u0060",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "docs.search_docs",
   "resource": null,
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 1,
   "approval_id": null,
   "run_id": "run_01m4d8qy7e5c59zfjmet",
   "details": {
    "jti": "jti_01m4d8r06axf10rxb5af",
    "denied": 0,
    "rule_id": "tool-docs-humans",
    "sources": 5
   }
  },
  {
   "seq": 8,
   "ts": "2026-10-08T08:05:46.784795+00:00",
   "actor": "u0060",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "external",
   "level": 2,
   "approval_id": null,
   "run_id": "run_01m4d8qy7e5c59zfjmet",
   "details": {
    "hwm": 2,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [],
    "egress_id": 2,
    "json_mode": false,
    "latency_ms": 1313,
    "redactions": 0,
    "tool_calls": 1,
    "public_only": false,
    "total_tokens": 1723,
    "content_level": 1,
    "prompt_tokens": 1657,
    "completion_tokens": 66
   }
  },
  {
   "seq": 9,
   "ts": "2026-10-08T08:05:46.878519+00:00",
   "actor": "u0060",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "docs.get_doc",
   "resource": "doc:pub-press-2025-10-fleetpilot-3",
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 1,
   "approval_id": null,
   "run_id": "run_01m4d8qy7e5c59zfjmet",
   "details": {
    "jti": "jti_01m4d8r1pzwbectrawv6",
    "denied": 0,
    "rule_id": "tool-docs-humans",
    "sources": 1
   }
  },
  {
   "seq": 10,
   "ts": "2026-10-08T08:05:48.274895+00:00",
   "actor": "u0060",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "external",
   "level": 2,
   "approval_id": null,
   "run_id": "run_01m4d8qy7e5c59zfjmet",
   "details": {
    "hwm": 2,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [],
    "egress_id": 3,
    "json_mode": false,
    "latency_ms": 1296,
    "redactions": 0,
    "tool_calls": 0,
    "public_only": false,
    "total_tokens": 2114,
    "content_level": 1,
    "prompt_tokens": 1966,
    "completion_tokens": 148
   }
  },
  {
   "seq": 11,
   "ts": "2026-10-08T08:05:49.364281+00:00",
   "actor": "u0060",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "external",
   "level": 2,
   "approval_id": null,
   "run_id": "run_01m4d8qy7e5c59zfjmet",
   "details": {
    "hwm": 2,
    "agent": "qa",
    "purpose": "qa.answer",
    "attempts": 1,
    "findings": [],
    "egress_id": 4,
    "json_mode": false,
    "latency_ms": 891,
    "redactions": 0,
    "tool_calls": 0,
    "public_only": false,
    "total_tokens": 2722,
    "content_level": 1,
    "prompt_tokens": 2638,
    "completion_tokens": 84
   }
  },
  {
   "seq": 12,
   "ts": "2026-10-08T08:05:49.566010+00:00",
   "actor": "u0011",
   "act_chain": [],
   "event": "demo_login",
   "action": "login",
   "resource": null,
   "decision": "allow",
   "reason": "persona_picker",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "persona_key": "sales_staff"
   }
  },
  {
   "seq": 13,
   "ts": "2026-10-08T08:05:49.719979+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "retrieval",
   "action": "search",
   "resource": "rag:qa",
   "decision": "allow",
   "reason": "rag_filter",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8r4c5w05n23d0es",
   "details": {
    "k": 8,
    "count": 8,
    "doc_ids": [
     "GDPR-Art-15",
     "GDPR-Art-32",
     "gen-data-protection-policy",
     "gen-employee-handbook",
     "pub-fleetpilot",
     "sales-battlecard-tourenmeister",
     "sales-plan-A-0002",
     "sales-plan-A-0004"
    ],
    "purpose": "qa",
    "min_trust": null,
    "collections": [
     "general",
     "legal_regs",
     "public",
     "sales"
    ],
    "hit_collections": [
     "general",
     "legal_regs",
     "public",
     "sales"
    ]
   }
  },
  {
   "seq": 14,
   "ts": "2026-10-08T08:05:50.789939+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8r4c5w05n23d0es",
   "details": {
    "hwm": 3,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [],
    "egress_id": null,
    "json_mode": false,
    "latency_ms": 938,
    "redactions": 0,
    "tool_calls": 1,
    "public_only": false,
    "total_tokens": 868,
    "content_level": 1,
    "prompt_tokens": 811,
    "completion_tokens": 57
   }
  },
  {
   "seq": 15,
   "ts": "2026-10-08T08:05:50.876662+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "crm.search_accounts",
   "resource": null,
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8r4c5w05n23d0es",
   "details": {
    "jti": "jti_01m4d8r5kyqr91y09anb",
    "denied": 0,
    "rule_id": "tool-crm-sales-ceo",
    "sources": 1
   }
  },
  {
   "seq": 16,
   "ts": "2026-10-08T08:05:51.867144+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8r4c5w05n23d0es",
   "details": {
    "hwm": 3,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [],
    "egress_id": null,
    "json_mode": false,
    "latency_ms": 906,
    "redactions": 0,
    "tool_calls": 1,
    "public_only": false,
    "total_tokens": 1082,
    "content_level": 1,
    "prompt_tokens": 1038,
    "completion_tokens": 44
   }
  },
  {
   "seq": 17,
   "ts": "2026-10-08T08:05:51.958845+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "crm.get_account",
   "resource": "crm.account:A-0002",
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8r4c5w05n23d0es",
   "details": {
    "jti": "jti_01m4d8r6nm4zapypbp3g",
    "denied": 0,
    "rule_id": "tool-crm-sales-ceo",
    "sources": 4
   }
  },
  {
   "seq": 18,
   "ts": "2026-10-08T08:05:53.295162+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8r4c5w05n23d0es",
   "details": {
    "hwm": 3,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [
     "email",
     "person_name",
     "phone"
    ],
    "egress_id": null,
    "json_mode": false,
    "latency_ms": 1250,
    "redactions": 0,
    "tool_calls": 2,
    "public_only": false,
    "total_tokens": 1628,
    "content_level": 3,
    "prompt_tokens": 1489,
    "completion_tokens": 139
   }
  },
  {
   "seq": 19,
   "ts": "2026-10-08T08:05:53.387869+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "retrieval",
   "action": "search",
   "resource": "rag:qa",
   "decision": "allow",
   "reason": "rag_filter",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8r4c5w05n23d0es",
   "details": {
    "k": 8,
    "count": 8,
    "doc_ids": [
     "GDPR-Art-13",
     "GDPR-Art-14",
     "GDPR-Art-28",
     "GDPR-Art-30",
     "GDPR-Art-49",
     "gen-ai-usage-guideline",
     "hr-handbook-onboarding",
     "sales-plan-A-0002"
    ],
    "purpose": "qa",
    "min_trust": null,
    "collections": [
     "general",
     "legal_regs",
     "public",
     "sales"
    ],
    "hit_collections": [
     "general",
     "legal_regs",
     "sales"
    ]
   }
  },
  {
   "seq": 20,
   "ts": "2026-10-08T08:05:53.421741+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "docs.search_docs",
   "resource": null,
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8r4c5w05n23d0es",
   "details": {
    "jti": "jti_01m4d8r82dz02t0agkkr",
    "denied": 0,
    "rule_id": "tool-docs-humans",
    "sources": 8
   }
  },
  {
   "seq": 21,
   "ts": "2026-10-08T08:05:53.510629+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "retrieval",
   "action": "search",
   "resource": "rag:qa",
   "decision": "allow",
   "reason": "rag_filter",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8r4c5w05n23d0es",
   "details": {
    "k": 8,
    "count": 8,
    "doc_ids": [
     "GDPR-Art-14",
     "gen-ai-usage-guideline",
     "gen-code-of-conduct",
     "gen-data-protection-policy",
     "gen-expense-policy",
     "gen-it-security-policy",
     "pub-trust",
     "sales-plan-A-0002"
    ],
    "purpose": "qa",
    "min_trust": null,
    "collections": [
     "general",
     "legal_regs",
     "public",
     "sales"
    ],
    "hit_collections": [
     "general",
     "legal_regs",
     "public",
     "sales"
    ]
   }
  },
  {
   "seq": 22,
   "ts": "2026-10-08T08:05:53.550561+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "docs.search_docs",
   "resource": null,
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8r4c5w05n23d0es",
   "details": {
    "jti": "jti_01m4d8r864v944tec1pj",
    "denied": 0,
    "rule_id": "tool-docs-humans",
    "sources": 8
   }
  },
  {
   "seq": 23,
   "ts": "2026-10-08T08:05:55.326069+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8r4c5w05n23d0es",
   "details": {
    "hwm": 3,
    "agent": "qa",
    "purpose": "qa.answer",
    "attempts": 1,
    "findings": [
     "email",
     "money",
     "person_name",
     "phone"
    ],
    "egress_id": null,
    "json_mode": false,
    "latency_ms": 1610,
    "redactions": 0,
    "tool_calls": 0,
    "public_only": false,
    "total_tokens": 4634,
    "content_level": 3,
    "prompt_tokens": 4487,
    "completion_tokens": 147
   }
  },
  {
   "seq": 24,
   "ts": "2026-10-08T08:05:55.500563+00:00",
   "actor": "u0060",
   "act_chain": [],
   "event": "demo_login",
   "action": "login",
   "resource": null,
   "decision": "allow",
   "reason": "persona_picker",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "persona_key": "platform_admin"
   }
  },
  {
   "seq": 25,
   "ts": "2026-10-08T08:05:55.530918+00:00",
   "actor": "u0060",
   "act_chain": [],
   "event": "outage_switch",
   "action": "set",
   "resource": "switch:local_down",
   "decision": "allow",
   "reason": "demo recording: outage drill",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "on": true
   }
  },
  {
   "seq": 26,
   "ts": "2026-10-08T08:05:55.558249+00:00",
   "actor": "u0011",
   "act_chain": [],
   "event": "demo_login",
   "action": "login",
   "resource": null,
   "decision": "allow",
   "reason": "persona_picker",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "persona_key": "sales_staff"
   }
  },
  {
   "seq": 27,
   "ts": "2026-10-08T08:05:55.703357+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "retrieval",
   "action": "search",
   "resource": "rag:qa",
   "decision": "allow",
   "reason": "rag_filter",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8ra7eq2d5em3578",
   "details": {
    "k": 8,
    "count": 8,
    "doc_ids": [
     "GDPR-Art-15",
     "GDPR-Art-32",
     "gen-data-protection-policy",
     "gen-employee-handbook",
     "pub-fleetpilot",
     "sales-battlecard-tourenmeister",
     "sales-plan-A-0002",
     "sales-plan-A-0004"
    ],
    "purpose": "qa",
    "min_trust": null,
    "collections": [
     "general",
     "legal_regs",
     "public",
     "sales"
    ],
    "hit_collections": [
     "general",
     "legal_regs",
     "public",
     "sales"
    ]
   }
  },
  {
   "seq": 28,
   "ts": "2026-10-08T08:05:55.804983+00:00",
   "actor": "u0011",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "deny",
   "reason": "local_down",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8ra7eq2d5em3578",
   "details": {
    "hwm": 3,
    "agent": "qa",
    "purpose": "qa.tools",
    "findings": [],
    "redactions": 0,
    "public_only": false,
    "content_level": 1
   }
  },
  {
   "seq": 29,
   "ts": "2026-10-08T08:05:55.859649+00:00",
   "actor": "u0060",
   "act_chain": [],
   "event": "demo_login",
   "action": "login",
   "resource": null,
   "decision": "allow",
   "reason": "persona_picker",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "persona_key": "platform_admin"
   }
  },
  {
   "seq": 30,
   "ts": "2026-10-08T08:05:55.888951+00:00",
   "actor": "u0060",
   "act_chain": [],
   "event": "outage_switch",
   "action": "set",
   "resource": "switch:local_down",
   "decision": "allow",
   "reason": "demo recording: outage drill",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "on": false
   }
  },
  {
   "seq": 31,
   "ts": "2026-10-08T08:05:55.920342+00:00",
   "actor": "u0011",
   "act_chain": [],
   "event": "demo_login",
   "action": "login",
   "resource": null,
   "decision": "allow",
   "reason": "persona_picker",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "persona_key": "sales_staff"
   }
  },
  {
   "seq": 32,
   "ts": "2026-10-08T08:05:55.951537+00:00",
   "actor": "u0021",
   "act_chain": [],
   "event": "demo_login",
   "action": "login",
   "resource": null,
   "decision": "allow",
   "reason": "persona_picker",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "persona_key": "eng_staff"
   }
  },
  {
   "seq": 33,
   "ts": "2026-10-08T08:05:56.090643+00:00",
   "actor": "u0021",
   "act_chain": [
    "agent:qa"
   ],
   "event": "retrieval",
   "action": "search",
   "resource": "rag:qa",
   "decision": "allow",
   "reason": "rag_filter",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8rakp110m9exyyg",
   "details": {
    "k": 8,
    "count": 8,
    "doc_ids": [
     "GDPR-Art-15",
     "GDPR-Art-32",
     "eng-P-02-readme",
     "eng-postmortem-2026-04-22",
     "gen-data-protection-policy",
     "gen-employee-handbook",
     "pub-fleetpilot",
     "pub-jd-R-02"
    ],
    "purpose": "qa",
    "min_trust": null,
    "collections": [
     "eng",
     "general",
     "legal_regs",
     "public"
    ],
    "hit_collections": [
     "eng",
     "general",
     "legal_regs",
     "public"
    ]
   }
  },
  {
   "seq": 34,
   "ts": "2026-10-08T08:05:57.499764+00:00",
   "actor": "u0021",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "external",
   "level": 1,
   "approval_id": null,
   "run_id": "run_01m4d8rakp110m9exyyg",
   "details": {
    "hwm": 1,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [],
    "egress_id": 5,
    "json_mode": false,
    "latency_ms": 1264,
    "redactions": 0,
    "tool_calls": 0,
    "public_only": false,
    "total_tokens": 841,
    "content_level": 1,
    "prompt_tokens": 745,
    "completion_tokens": 96
   }
  },
  {
   "seq": 35,
   "ts": "2026-10-08T08:05:58.919307+00:00",
   "actor": "u0021",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "external",
   "level": 1,
   "approval_id": null,
   "run_id": "run_01m4d8rakp110m9exyyg",
   "details": {
    "hwm": 1,
    "agent": "qa",
    "purpose": "qa.answer",
    "attempts": 1,
    "findings": [],
    "egress_id": 6,
    "json_mode": false,
    "latency_ms": 1297,
    "redactions": 0,
    "tool_calls": 0,
    "public_only": false,
    "total_tokens": 126,
    "content_level": 1,
    "prompt_tokens": 84,
    "completion_tokens": 42
   }
  },
  {
   "seq": 36,
   "ts": "2026-10-08T08:05:59.048981+00:00",
   "actor": "u0001",
   "act_chain": [],
   "event": "demo_login",
   "action": "login",
   "resource": null,
   "decision": "allow",
   "reason": "persona_picker",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "persona_key": "ceo"
   }
  },
  {
   "seq": 37,
   "ts": "2026-10-08T08:05:59.202541+00:00",
   "actor": "u0001",
   "act_chain": [
    "agent:qa"
   ],
   "event": "retrieval",
   "action": "search",
   "resource": "rag:qa",
   "decision": "allow",
   "reason": "rag_filter",
   "route": null,
   "level": 2,
   "approval_id": null,
   "run_id": "run_01m4d8rdmf9q8psm683b",
   "details": {
    "k": 8,
    "count": 8,
    "doc_ids": [
     "GDPR-Art-15",
     "GDPR-Art-32",
     "GDPR-Art-46",
     "gen-data-protection-policy",
     "gen-employee-handbook",
     "pub-coldchain",
     "pub-fleetpilot",
     "pub-jd-R-02"
    ],
    "purpose": "qa",
    "min_trust": null,
    "collections": [
     "exec",
     "general",
     "legal_regs",
     "public"
    ],
    "hit_collections": [
     "general",
     "legal_regs",
     "public"
    ]
   }
  },
  {
   "seq": 38,
   "ts": "2026-10-08T08:06:00.251472+00:00",
   "actor": "u0001",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "external",
   "level": 1,
   "approval_id": null,
   "run_id": "run_01m4d8rdmf9q8psm683b",
   "details": {
    "hwm": 1,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [],
    "egress_id": 7,
    "json_mode": false,
    "latency_ms": 906,
    "redactions": 0,
    "tool_calls": 2,
    "public_only": false,
    "total_tokens": 1166,
    "content_level": 1,
    "prompt_tokens": 1096,
    "completion_tokens": 70
   }
  },
  {
   "seq": 39,
   "ts": "2026-10-08T08:06:00.345691+00:00",
   "actor": "u0001",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "crm.search_accounts",
   "resource": null,
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8rdmf9q8psm683b",
   "details": {
    "jti": "jti_01m4d8revv3aj7g9d9sb",
    "denied": 0,
    "rule_id": "tool-crm-sales-ceo",
    "sources": 1
   }
  },
  {
   "seq": 40,
   "ts": "2026-10-08T08:06:00.438570+00:00",
   "actor": "u0001",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "legal.contracts_expiring",
   "resource": null,
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 1,
   "approval_id": null,
   "run_id": "run_01m4d8rdmf9q8psm683b",
   "details": {
    "jti": "jti_01m4d8reyshgscaa8nhd",
    "denied": 0,
    "rule_id": "tool-legal-dept-ceo",
    "sources": 0
   }
  },
  {
   "seq": 41,
   "ts": "2026-10-08T08:06:01.545653+00:00",
   "actor": "u0001",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8rdmf9q8psm683b",
   "details": {
    "hwm": 3,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [],
    "egress_id": null,
    "json_mode": false,
    "latency_ms": 1015,
    "redactions": 0,
    "tool_calls": 2,
    "public_only": false,
    "total_tokens": 1478,
    "content_level": 1,
    "prompt_tokens": 1400,
    "completion_tokens": 78
   }
  },
  {
   "seq": 42,
   "ts": "2026-10-08T08:06:01.640415+00:00",
   "actor": "u0001",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "crm.get_account",
   "resource": "crm.account:A-0002",
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8rdmf9q8psm683b",
   "details": {
    "jti": "jti_01m4d8rg43m6fk5t9116",
    "denied": 0,
    "rule_id": "tool-crm-sales-ceo",
    "sources": 4
   }
  },
  {
   "seq": 43,
   "ts": "2026-10-08T08:06:01.734581+00:00",
   "actor": "u0001",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "legal.contracts_expiring",
   "resource": null,
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8rdmf9q8psm683b",
   "details": {
    "jti": "jti_01m4d8rg74br8pb4appc",
    "denied": 0,
    "rule_id": "tool-legal-dept-ceo",
    "sources": 7
   }
  },
  {
   "seq": 44,
   "ts": "2026-10-08T08:06:03.643475+00:00",
   "actor": "u0001",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8rdmf9q8psm683b",
   "details": {
    "hwm": 3,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [
     "email",
     "person_name",
     "phone"
    ],
    "egress_id": null,
    "json_mode": false,
    "latency_ms": 1797,
    "redactions": 0,
    "tool_calls": 0,
    "public_only": false,
    "total_tokens": 2922,
    "content_level": 3,
    "prompt_tokens": 2667,
    "completion_tokens": 255
   }
  },
  {
   "seq": 45,
   "ts": "2026-10-08T08:06:05.500448+00:00",
   "actor": "u0001",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8rdmf9q8psm683b",
   "details": {
    "hwm": 3,
    "agent": "qa",
    "purpose": "qa.answer",
    "attempts": 1,
    "findings": [
     "email",
     "person_name",
     "phone"
    ],
    "egress_id": null,
    "json_mode": false,
    "latency_ms": 1702,
    "redactions": 0,
    "tool_calls": 0,
    "public_only": false,
    "total_tokens": 1854,
    "content_level": 3,
    "prompt_tokens": 1657,
    "completion_tokens": 197
   }
  },
  {
   "seq": 46,
   "ts": "2026-10-08T08:06:05.647975+00:00",
   "actor": "u0021",
   "act_chain": [],
   "event": "demo_login",
   "action": "login",
   "resource": null,
   "decision": "allow",
   "reason": "persona_picker",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "persona_key": "eng_staff"
   }
  },
  {
   "seq": 47,
   "ts": "2026-10-08T08:06:05.810561+00:00",
   "actor": "u0021",
   "act_chain": [
    "agent:qa"
   ],
   "event": "retrieval",
   "action": "search",
   "resource": "rag:qa",
   "decision": "allow",
   "reason": "rag_filter",
   "route": null,
   "level": 2,
   "approval_id": null,
   "run_id": "run_01m4d8rm2qcqfhe1vesr",
   "details": {
    "k": 8,
    "count": 8,
    "doc_ids": [
     "AI-Act-Annex-III-4",
     "GDPR-Art-30",
     "GDPR-Art-6",
     "gen-employee-handbook",
     "hr-handbook-onboarding",
     "pub-jd-R-01",
     "pub-jd-R-06",
     "pub-press-2025-10-fleetpilot-3"
    ],
    "purpose": "qa",
    "min_trust": null,
    "collections": [
     "eng",
     "general",
     "legal_regs",
     "public"
    ],
    "hit_collections": [
     "general",
     "legal_regs",
     "public"
    ]
   }
  },
  {
   "seq": 48,
   "ts": "2026-10-08T08:06:06.941740+00:00",
   "actor": "u0021",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "external",
   "level": 1,
   "approval_id": null,
   "run_id": "run_01m4d8rm2qcqfhe1vesr",
   "details": {
    "hwm": 1,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [],
    "egress_id": 8,
    "json_mode": false,
    "latency_ms": 984,
    "redactions": 0,
    "tool_calls": 2,
    "public_only": false,
    "total_tokens": 828,
    "content_level": 1,
    "prompt_tokens": 740,
    "completion_tokens": 88
   }
  },
  {
   "seq": 49,
   "ts": "2026-10-08T08:06:07.029158+00:00",
   "actor": "u0021",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "code.get_project_status",
   "resource": "eng.project:P-02",
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8rm2qcqfhe1vesr",
   "details": {
    "jti": "jti_01m4d8rnchmc9yxf185j",
    "denied": 0,
    "rule_id": "tool-code-eng-ceo",
    "sources": 1
   }
  },
  {
   "seq": 50,
   "ts": "2026-10-08T08:06:07.118244+00:00",
   "actor": "u0021",
   "act_chain": [
    "agent:qa"
   ],
   "event": "tool_call",
   "action": "code.search_tickets",
   "resource": null,
   "decision": "allow",
   "reason": "ok",
   "route": null,
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8rm2qcqfhe1vesr",
   "details": {
    "jti": "jti_01m4d8rnfamfn7m8ng8m",
    "denied": 0,
    "rule_id": "tool-code-eng-ceo",
    "sources": 20
   }
  },
  {
   "seq": 51,
   "ts": "2026-10-08T08:06:09.605809+00:00",
   "actor": "u0021",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8rm2qcqfhe1vesr",
   "details": {
    "hwm": 3,
    "agent": "qa",
    "purpose": "qa.tools",
    "attempts": 1,
    "findings": [],
    "egress_id": null,
    "json_mode": false,
    "latency_ms": 2391,
    "redactions": 0,
    "tool_calls": 0,
    "public_only": false,
    "total_tokens": 3183,
    "content_level": 1,
    "prompt_tokens": 2783,
    "completion_tokens": 400
   }
  },
  {
   "seq": 52,
   "ts": "2026-10-08T08:06:12.747645+00:00",
   "actor": "u0021",
   "act_chain": [
    "agent:qa"
   ],
   "event": "model_call",
   "action": "chat",
   "resource": "model:deepseek-flash",
   "decision": "allow",
   "reason": "route_by_level",
   "route": "local",
   "level": 3,
   "approval_id": null,
   "run_id": "run_01m4d8rm2qcqfhe1vesr",
   "details": {
    "hwm": 3,
    "agent": "qa",
    "purpose": "qa.answer",
    "attempts": 1,
    "findings": [],
    "egress_id": null,
    "json_mode": false,
    "latency_ms": 2968,
    "redactions": 0,
    "tool_calls": 0,
    "public_only": false,
    "total_tokens": 2911,
    "content_level": 1,
    "prompt_tokens": 2291,
    "completion_tokens": 620
   }
  },
  {
   "seq": 53,
   "ts": "2026-10-08T08:06:12.916195+00:00",
   "actor": "u0061",
   "act_chain": [],
   "event": "demo_login",
   "action": "login",
   "resource": null,
   "decision": "allow",
   "reason": "persona_picker",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "persona_key": "auditor"
   }
  },
  {
   "seq": 54,
   "ts": "2026-10-08T08:06:12.948492+00:00",
   "actor": "u0061",
   "act_chain": [],
   "event": "audit_read",
   "action": "read_log",
   "resource": "audit.log",
   "decision": "allow",
   "reason": "auditor",
   "route": null,
   "level": null,
   "approval_id": null,
   "run_id": null,
   "details": {
    "limit": 1000,
    "after_seq": 2
   }
  }
 ],
 "chain": {
  "ok": true,
  "checked": 54,
  "first_bad_seq": null
 }
};
