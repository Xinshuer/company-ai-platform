window.FIXTURES.proposals = {
 "by_persona": {
  "sales_staff": {
   "mine": [
    {
     "proposal_id": "prop_01m4c0h6sal2eml7w3kd",
     "created_at": "2026-10-07T09:14:22Z",
     "requester": {
      "user_id": "u0011",
      "name": "Erik Lindqvist"
     },
     "agent": "sales_profile",
     "domain": "crm",
     "tool": "send_email",
     "tier": 3,
     "status": "pending",
     "summary": "Send renewal follow-up email to Havenmeer Distributie B.V. (Simon Jaceps)",
     "params": {
      "account_id": "A-0002",
      "to": "simon.jaceps@havenmeer-distributie.example",
      "cc": [
       "erik.lindqvist@abctech.example"
      ],
      "subject": "FleetPilot renewal and store-delivery results",
      "body": "Dear Mr Jaceps,\n\nthank you for the good collaboration on the store-delivery optimisation project. Ahead of the renewal on 30 November, I would like to walk you through the first results and the multi-depot options for 2027. Would a call in the week of 12 October suit you?\n\nBest regards,\nErik Lindqvist\nABC Tech AB"
     },
     "params_hash": "d9b1ea48190233d230ddc91bde9349485f9bad8a9f6ef4873fc160adca639197",
     "can_approve": false,
     "why_not": "Tier 3 needs four eyes: the requester cannot approve their own proposal."
    },
    {
     "proposal_id": "prop_01m4c03r9sal1log5t7x",
     "created_at": "2026-10-05T11:40:12Z",
     "requester": {
      "user_id": "u0011",
      "name": "Erik Lindqvist"
     },
     "agent": "sales_profile",
     "domain": "crm",
     "tool": "log_interaction",
     "tier": 2,
     "status": "pending",
     "summary": "Log call with Donauzeile Frischdienst GmbH",
     "params": {
      "account_id": "A-0004",
      "kind": "call",
      "summary": "Discussed P-02 testing delay; customer asks for weekly status.",
      "occurred_on": "2026-10-05"
     },
     "params_hash": "ebf9240d039157e53f6265a78a40a7fc363c920821d2b79b6c56830ecf1cc8c8",
     "can_approve": true,
     "why_not": null
    },
    {
     "proposal_id": "prop_01m4bz2k8sal4wks9n1p",
     "created_at": "2026-10-02T14:03:51Z",
     "requester": {
      "user_id": "u0011",
      "name": "Erik Lindqvist"
     },
     "agent": "sales_profile",
     "domain": "crm",
     "tool": "send_email",
     "tier": 3,
     "status": "executed",
     "summary": "Send workshop invitation to Portaluce Trasporti S.r.l. (Chiara Ferri)",
     "params": {
      "account_id": "A-0006",
      "to": "chiara.ferri@portaluce-trasporti.example",
      "cc": [],
      "subject": "Multi-depot planning workshop",
      "body": "Dear Ms Ferri,\n\nas discussed, here are three dates for the multi-depot planning workshop in Bologna: 20, 22 or 27 October. Please let me know which one works best.\n\nBest regards,\nErik Lindqvist\nABC Tech AB"
     },
     "params_hash": "75e7a4a08e7c8cabbceabbe556d77ee3b75a0c93ea6cdf0e92b074bfdea46c17",
     "can_approve": false,
     "why_not": "Already decided.",
     "approver": {
      "user_id": "u0010",
      "name": "Karin Holm"
     },
     "decided_at": "2026-10-02T15:20:07Z",
     "reason": "Dates agreed in the last call; wording ok.",
     "result": {
      "outbox_id": "OB-000017",
      "sent_at": "2026-10-02T15:20:08Z",
      "simulated": true
     }
    }
   ],
   "to_approve": []
  },
  "sales_head": {
   "mine": [],
   "to_approve": [
    {
     "proposal_id": "prop_01m4c0h6sal2eml7w3kd",
     "created_at": "2026-10-07T09:14:22Z",
     "requester": {
      "user_id": "u0011",
      "name": "Erik Lindqvist"
     },
     "agent": "sales_profile",
     "domain": "crm",
     "tool": "send_email",
     "tier": 3,
     "status": "pending",
     "summary": "Send renewal follow-up email to Havenmeer Distributie B.V. (Simon Jaceps)",
     "params": {
      "account_id": "A-0002",
      "to": "simon.jaceps@havenmeer-distributie.example",
      "cc": [
       "erik.lindqvist@abctech.example"
      ],
      "subject": "FleetPilot renewal and store-delivery results",
      "body": "Dear Mr Jaceps,\n\nthank you for the good collaboration on the store-delivery optimisation project. Ahead of the renewal on 30 November, I would like to walk you through the first results and the multi-depot options for 2027. Would a call in the week of 12 October suit you?\n\nBest regards,\nErik Lindqvist\nABC Tech AB"
     },
     "params_hash": "d9b1ea48190233d230ddc91bde9349485f9bad8a9f6ef4873fc160adca639197",
     "can_approve": true,
     "why_not": null
    }
   ]
  },
  "ceo": {
   "mine": [],
   "to_approve": [
    {
     "proposal_id": "prop_01m4c0h6sal2eml7w3kd",
     "created_at": "2026-10-07T09:14:22Z",
     "requester": {
      "user_id": "u0011",
      "name": "Erik Lindqvist"
     },
     "agent": "sales_profile",
     "domain": "crm",
     "tool": "send_email",
     "tier": 3,
     "status": "pending",
     "summary": "Send renewal follow-up email to Havenmeer Distributie B.V. (Simon Jaceps)",
     "params": {
      "account_id": "A-0002",
      "to": "simon.jaceps@havenmeer-distributie.example",
      "cc": [
       "erik.lindqvist@abctech.example"
      ],
      "subject": "FleetPilot renewal and store-delivery results",
      "body": "Dear Mr Jaceps,\n\nthank you for the good collaboration on the store-delivery optimisation project. Ahead of the renewal on 30 November, I would like to walk you through the first results and the multi-depot options for 2027. Would a call in the week of 12 October suit you?\n\nBest regards,\nErik Lindqvist\nABC Tech AB"
     },
     "params_hash": "d9b1ea48190233d230ddc91bde9349485f9bad8a9f6ef4873fc160adca639197",
     "can_approve": true,
     "why_not": null
    }
   ]
  }
 },
 "resumed_runs": {
  "prop_01m4c0h6sal2eml7w3kd": {
   "run_id": "run_01m4c0p2sal6prf9res1",
   "thread_id": "thr_01m4c0h5salthr3n6k",
   "app": "sales_profile",
   "status": "done",
   "output": {
    "text": "邮件已通过审批并发送（模拟发件箱 OB-000023）。已在 CRM 中记录这次联系。",
    "citations": [],
    "data": {
     "outbox_id": "OB-000023"
    }
   },
   "hwm": 3,
   "route": "local",
   "events": [
    {
     "t": "approval",
     "proposal_id": "prop_01m4c0h6sal2eml7w3kd",
     "tier": 3
    },
    {
     "t": "route",
     "route": "local",
     "level": 3,
     "reason": "route_by_level"
    }
   ],
   "pending_approval": null,
   "error": null
  }
 }
};
