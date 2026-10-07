// Loaded before ui/fixtures/*.js (which js/fixtures_loader.js adds in demo mode only). Each
// fixture file is exactly one statement, `window.FIXTURES.<name> = <strict JSON>;`, so
// `python -m corpai demo-record` can rewrite it and tests can json-load it. The page never fetches
// local files (file:// must work).
//
// Fixture names and shapes (payloads as in architecture §18):
//   personas   {"personas": [GET /api/personas item, ...]}
//   me         {"by_persona": {persona_key: GET /api/me}}
//   health     GET /api/health                     switches   GET /api/admin/switches
//   runs       {"items": [{"persona_key": key | "*", "app", "input", "switches"?: {...}, "run": POST /api/runs}]}
//              "switches" marks a recording made under those outage switches (e.g. local_down)
//   compare    {"question", "app", "personas": [three persona keys]}
//   proposals  {"by_persona": {key: {"mine": [...], "to_approve": [...]}}, "resumed_runs": {proposal_id: run}}
//   traces     {"by_run": {run_id: GET /api/runs/{id}/trace}}
//   audit      GET /api/audit
// One file per milestone app area, owned by that milestone (legal M2, report M3, sales and hr M4):
//   legal | report | sales | hr
//              {"runs": [runs item, ...], "traces": {run_id: trace}, "proposals": {"by_persona": {...},
//               "resumed_runs": {...}}, ...any keys its js/demo_<name>.js reads}
//              js/demo.js merges runs, traces and proposals with the shared ones above.
window.FIXTURES = window.FIXTURES || {};
// the milestone fixture names above (js/fixtures_loader.js loads them; js/demo.js merges them)
window.FIXTURE_APPS = ["legal", "report", "sales", "hr"];
