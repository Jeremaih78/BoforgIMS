# Stage 13: controlled pilot, measurement and release

31 October 2026, Africa/Harare. Owner-nominated pilot lead: **admin**. Selected design: **staggered rollout of matched roles**, with the later group held out during the first comparison window. This nomination does not identify or enroll other employees.

## Current decisions

The application now supports prospective protocols, an explicit roster, independently accepted daily measurements, quality/effort safeguards, comparative output reporting, scoped CSV exports, an operating-evidence ledger and optional cohort access enforcement. The existing Company, Team, baseline, accepted goal outcomes, source responsibility, AI gateway, budgets and outbox remain authoritative.

Three separate verdicts are displayed: local software verification; readiness to authorize a real pilot; and productivity validation. Local fixtures cannot satisfy the latter two. No real roster, observation dates or source definitions were invented. No production migration, commit, push, invitation, provider request or external message was performed for this stage. The previously deferred private OpenAI replacement-key/dotenv work remains deferred.

Final verification results are recorded below and in `output/tasker-stage13-release-evidence.json`. Source changes invalidate that local evidence. This artifact is an operator-maintained verification record, not an independently signed certification.

## How admin should prepare the real pilot

1. Confirm the registered `admin` account's company/team management scope. Choose actual employees, their manager, role/workload stratum and matched blocks. Existing permission groups do not establish membership or manager authority. The default comparison needs four matched pairs (eight people); a smaller usability group remains exploratory and cannot close the comparison gate.
2. In **Goals / Productivity baseline**, define accepted business output, its unit and independent acceptance rule, the staffing source including overtime, quality/rework sources, role/workload, baseline dates and comparison plan. Start collection before its declared first observation day. Do not backdate the collection timestamp or use login/arrival/Focus duration as staffed hours.
3. Open **Pilot → the baseline → Draft protocol**. Choose the registered owner, method, future follow-up dates, observation weekdays, holidays, matching/workload policy, spillover rules, missing-data/withdrawal treatment, independent review and parallel-trends review. Select the features the cohort may use. Exclude AI until its separate setup/evaluation is complete.
4. Add each registered participant to an early or later group with a matched-block identifier. Each block has one employee in each arm, within the same role, workload and output unit. Early release is the follow-up first day. Later release must be after the comparison ends; reaching that date alone never grants access. Draft mistakes can be corrected by creating a replacement draft before approval; the approved roster is retained.
5. Review the prospective protocol and freeze it before early rollout. The baseline definitions, protocol, dates, matching and synthetic/real identity become fixed. A delayed start or a changed design needs a new protocol. A changed canonical baseline invalidates the stored protocol proof.
6. Complete every required **Release readiness** operating check with a concrete reviewed reference. A mandatory check marked Pending, Failed or Deferred blocks release. AI evaluation is conditionally deferred only when AI is excluded. Obtain separate deployment and actual cohort-release authorization before the first start date.
7. After an authorized deployment, enable the frozen release, then deliberately release each member's selected features. Ordinary unlisted employees remain outside enforced rollout. Pause immediately for privacy, quality, source-credit or recovery failures. Withdrawal preserves expected employee-days and requires review; it cannot make the denominator look better.

The first cohort should be small and supervised. The four-pair/20-day defaults are screening minima, **not a validated power calculation**. A qualified reviewer must prospectively assess role comparability, variance, serial correlation, parallel trends, spillover and statistical power. With insufficient staff or evidence, collect usability/operating feedback and keep productivity validation pending.

## Daily measurements and honest conclusions

Employees open **Pilot → Measurement → Record measurements**. They see only their own rows. A scoped manager sees the frozen cohort and accepts source evidence independently; self-acceptance is rejected. Baseline supplements link Stage 2 observations and add missing timing/quality denominators without overriding its accepted output, hours, passed quality checks or rework. Follow-up rows use an approved business register, or currently valid accepted goal outcomes with matching employee, company, team, day, unit and total.

Record staffed hours, accepted units, quality passed/checked, rework incidents, total cycle minutes/sample count, blocked hours, deadlines due/met, overtime and administration hours. Attest actual Tasker exposure for every observed day, including unexpected use in a control group. Enter zero only when verified. Blank remains unknown. Missing expected days, incomplete safeguards, contaminated/unknown exposure, source retraction, scope loss or withdrawal block final validation. Do not store private notes, reflections, customer contacts or credentials in shared measurement evidence.

The dashboard reports accepted units per staffed hour for each arm/window, cycle time, blocked time, deadline reliability, quality/rework, effort, coverage, currently valid goal output and known/uncertain attributed AI cost. Different output units/roles are never pooled into a company-wide score. The core accepted Stage 2 baseline remains visible even when its required supplements are incomplete. CSV follows the same current scope, escapes formula-leading values and marks current source validity; it can contain authorized shared evidence and must stay with its authorized audience.

The prespecified estimator is a rate-ratio difference-in-differences:

```text
100 × (((early follow-up output/hour) / (early baseline output/hour))
      / ((later follow-up output/hour) / (later baseline output/hour)) − 1)
```

Uncertainty uses 1,000 deterministic paired-cluster bootstrap resamples of intact matched blocks (seed 7132026), with 2.5th/97.5th percentile bounds. Employee-days within a block are kept together. A before/after-only design is exploratory. The interval is conditional on the protocol's assumptions, not a causal guarantee. The small default number of clusters particularly needs independent review. See the primary [difference-in-differences serial-correlation research](https://www.nber.org/papers/w8841) and [paired/percentile bootstrap reference](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html).

The hypothesis is **strictly greater than 70%**, with a threshold that cannot be lowered below 70%. Final review requires a real protocol and separately authorized rollout before its start, the completed observation window, full expected-day coverage, current accepted sources, minimum pairs/days/output, no contamination/withdrawal, maintained quality/rework/overtime/admin safeguards, and a lower conditional interval bound above the target. An independent methodology/data review reference and current report digest are required to record validation. New/retracted data invalidates the old validation receipt. A high invented demonstration percentage never proves a real benefit, and no task-count leaderboard or opaque individual score is introduced.

## Staff training and feedback

Use this short practice sequence in staging, then conduct real staff sessions before release:

1. **Employee:** capture a private idea; review the audience and promote only deliberate shared work. Link the approved goal, estimate remaining work and plan a feasible block. Start Focus, park private thoughts, raise a named blocker/handoff and continue after resolution. Register arrival explicitly. Close the day separately from saving private reflections.
2. **Manager:** verify membership and delegation scope, acknowledge/resolve the handoff, check shared commitments and missing confirmations in Strategy, and accept authoritative source output once. Private captures, plan reflections and Focus notes stay outside the board and reports.
3. **Measurement reviewer:** compare the approved output/staffing/quality sources, accept or retract a measurement with an audit trail, inspect missing days and interval assumptions, and read release readiness separately from measured benefit.
4. **Recovery operator:** verify worker/scheduler health, inspect generic error codes, retry a known safe failed job through authorized controls, and preserve ambiguous provider/SMTP results. Do not reset UNKNOWN spend or resend a possibly accepted email.

During real sessions measure capture time (target ≤10 seconds), Start actions (target one), planning time (target ≤90 seconds) and daily administration (target <5 minutes), plus errors/help needed and staff feedback. These targets remain **unmeasured in real staff sessions**. Test actual assistive technology, 200% native zoom and OS reduced motion; browser layout checks are not substitutes. Keep session notes within an approved retention/audience policy.

## Local verification and runnable rehearsal

Use the existing virtual environment and disposable PostgreSQL on `127.0.0.1:55441`. Never use local test/preview settings in production.

```powershell
$env:IMS_TEST_PORT='55441'
.\venv\Scripts\python.exe manage.py test tasker.tests.test_pilots tasker.tests.test_pilot_journey tasker.tests.test_pilot_concurrency --settings=config.test_settings --noinput
.\venv\Scripts\python.exe manage.py test tasker config inventory credit_control sales shop accounting operations --settings=config.test_settings --noinput
.\venv\Scripts\python.exe manage.py check --settings=config.test_settings
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

The guarded command `rehearse_tasker_pilot` accepts only the separately created database `ims_stage13_rehearsal` on that loopback port, debug settings, an empty AI credential override and disabled live send/rollout. It refuses an existing fixture roster. It never deletes/replaces it. After creating and migrating an empty disposable database:

```powershell
.\venv\Scripts\python.exe manage.py rehearse_tasker_pilot --settings=tmp.tasker_stage13_ui_settings
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8765 --noreload --settings=tmp.tasker_stage13_ui_settings
.\venv\Scripts\python.exe manage.py check_tasker_release --pilot 2 --user pilot-manager --settings=tmp.tasker_stage13_ui_settings
```

Use the pilot ID printed by the rehearsal command; sequence gaps from rolled-back attempts are harmless. Its fake logins are `pilot-manager`, `pilot-operator-1`, `pilot-restricted`, password `synthetic-preview-only`. These are **loopback synthetic accounts only**. Never copy them into production. The command creates eight invented employees and 320 accepted employee-days; it runs capture → goal → feasible plan → Focus → blocker/handoff → resolution → native shipment closure → accepted source output → manager board. It also verifies the restricted actor, AI-disabled manual path and real leased-job failure/retry/report recovery. No stock/finance posting is performed by accepting a Tasker output.

The rehearsal backup used PostgreSQL `pg_dump --format=custom --no-owner --no-acl` and `pg_restore --exit-on-error` into a **different** disposable database, `ims_stage13_restore`. Verification compared 171 table counts (sessions excluded), validated structural constraints by column names, exact new pilot constraint definitions and hashes of baseline/pilot/task/source/goal/report evidence. PostgreSQL 18 reorders dropped-column physical positions and reparses some existing array casts; those internal forms are not treated as logical schema differences. See `output/stage13-restore-verification.json`. This proves the synthetic restoration only; a private production-backup rehearsal is still required.

### Verification record

- Focused pilot checkpoint: **33 passing Python tests**, including three PostgreSQL race tests; see `output/stage13-focused-tests.log`.
- **77 JavaScript tests passed**; see `output/stage13-javascript-tests.log`.
- Fresh additive staging migrations 0031/0032, clean system/migration-drift checks, strict WhiteNoise manifest collection and the separate synthetic restore passed.
- Desktop/390px browser checks covered separate gates, all 153 requirement IDs, employee-only data, restricted-user denial, a retained invalid measurement draft and keyboard error focus. The 390px document width was 375px; viewport overrides were reset. Native assistive technology and real staff usability remain pending.
- The broad regression result and final local evidence digest are recorded at closeout below. No unrun check is certified by this checkpoint.

## Flags and scoped release behavior

All new flags default false:

| Setting | Meaning and rollback |
| --- | --- |
| `TASKER_PILOT_ENABLED` | Exposes pilot software. Off hides it; evidence remains in additive tables. |
| `TASKER_PILOT_ACCESS_ENFORCED` | Gates Tasker HTTP routes by approved real cohort, dates, selected features and per-member release. Off restores existing pre-pilot access rules; **it broadens access back to those rules**, so it is not a containment action. |
| `TASKER_ROLLOUT_WRITES_ENABLED` | Permits separately reviewed real release authorization/member release writes. Off blocks further release writes; it does not revoke an already released member. Pause the pilot to stop its enforced access. |

Enforcement grants no underlying model, team, source or monetary permissions. Measurement and personal interface-preference routes keep their own selectors so later-group employees can submit authorized evidence and use accessibility preferences. Work/Now/AI links and capture controls are withheld from held users; the workspace home and mobile navigation lead to Pilot. Noncohort managers retain existing scoped management access; an enrolled manager does not bypass a comparison-arm hold. Unlisted ordinary employees are held out. Synthetic membership can never grant real release. A prior closed/withdrawn cohort membership remains held under enforcement until another explicit approved release or an owner-authorized return to normal access rules. Turning the pilot module off while leaving enforcement on holds all Tasker routes; an intentional return to normal access requires reviewing both flags.

Feature selections map core capture/Work/Focus; planning/arrival; operator; coordination; reports; AI; strategy; business adapters; improvement; recurrence to their existing routes and flags. AI's shared gateway and asynchronous workflow check cohort release before dispatch. Recurrence checks release before creating a task and defers held jobs without burning retries. Other already authorized background repairs and factual reporting keep their existing source/audience checks; the cohort switch is not a server-wide scheduler or emergency egress stop. To contain external activity, turn off its live flag and restart/stop its loaded workers.

Keep existing slice flags explicit: `TASKER_DIRECTION_ENABLED`, `TASKER_COORDINATION_ENABLED`, `TASKER_REPORTS_ENABLED`, `TASKER_OPERATOR_DISPATCH_ENABLED`, `TASKER_STRATEGY_ENABLED`, `TASKER_IMPROVEMENT_ENABLED`, `TASKER_RECURRENCE_ENABLED`, and the nine independent source-adapter switches. Keep `TASKER_AI_WORKFLOW_ENABLED` and the active AI configuration disabled while the key/live evaluation is deferred. Keep `TASKER_REPORT_EMAIL_LIVE_ENABLED` false until the approved sender, top-manager recipient grants, closing/correction policy and authorized staging send pass. Company policy must also enable real email. Weekly AI limits remain USD 0.50/employee and USD 5/company, Monday 00:00 Africa/Harare; this stage did not activate or change them.

## VPS deployment preparation and recovery order

The previously identified VPS paths are application `/home/boforg/BoforgIMS` and Python `/home/boforg/ims/bin/python`. The venv is outside the checkout. Web units are `gunicorn-ims.service` and `gunicorn-ims.socket`; socket activation must be stopped as well as the service during maintenance. These paths are historical evidence, **not a fresh verification of the VPS**. Do not run local synthetic settings or the old deployment script blindly.

1. **admin** appoints a deployment/recovery operator, validates current paths/unit users/environment and PostgreSQL backup credentials privately, and reviews the exact commit and migration graph. Inspect the pending Operations/Inventory/Accounting/Shop dependencies as well as Tasker. No `git reset --hard`, secret output or automatic full migration should substitute for this review.
2. Obtain a restricted private production backup and restore it into isolated staging. Preserve migrations, uploads/private media and matching code/config backups. Keep production writers, integrations, scheduler, worker and both web units stopped consistently for the final backup/migration window. Verify restore, canonical source counts, permission boundaries and the known IMS preflight findings before reopening writers. Historical findings from 28 October were 2 nonpositive payments, 8 repeated COGS and 1 reservation mismatch; they were deliberately preserved, not repaired by Tasker.
3. Rehearse the reviewed **forward-only** migration plan. Tasker additions are `0031_pilot_protocol_and_release_evidence` and `0032_pilot_measurement_guardrails`; earlier pending Stage 9–12/IMS dependencies need their own reviewed plan. Run system checks, migrate the approved targets/graph, then `setup_tasker_roles`. That command supplies model capabilities, not actual team memberships or cohort release.
4. Run strict `collectstatic --noinput` using production storage. Verify manifest-mapped Bootstrap CSS/JS/icons and new pilot CSS over local Nginx and public HTTPS. Missing sourcemaps and unreadable public Tasker assets caused earlier deployment failures; confirm shipped references and public static directory/file modes (755/644 where appropriate), without changing private configuration, uploads, database ownership or whole checkout permissions.
5. Install/review dedicated supervised worker and scheduler units using the same verified service user, working directory and private environment as Django. Suggested **new** unit names are `tasker-worker.service` and `tasker-scheduler.service`; these are proposed, not claimed to exist. Their commands are `/home/boforg/ims/bin/python manage.py tasker_worker --loop --worker-id ims-tasker-worker --limit 25 --poll-seconds 2 --lease-seconds 600` and the matching `tasker_scheduler --loop --scheduler-id ims-tasker-scheduler --limit 100 --poll-seconds 30`. Review the AI timeout/lease policy before enabling AI. Use restart-on-failure, bounded logs and distinct heartbeat identities. No second broker/scheduler is introduced.
6. Start web, verify authenticated employee/manager/restricted screens and static responses, then start the reviewed scheduler/worker. Check `tasker_jobs_health --fail-on-unhealthy`, current heartbeat roles, failed jobs/expired leases, source reconciliation and factual reports. An unconfigured live sender remains held. A heartbeat alone does not prove supervision or actual delivery.
7. Publish only the reviewed local evidence artifact matching the deployed source digest, recheck real operating evidence, then run `check_tasker_release --pilot <real-id> --user admin --fail-on-blockers` with actual production settings. An artifact/code mismatch returns Pending. Separately authorize the frozen cohort before its first date, release individual early members and monitor a bounded initial session. Do not send invitations or enable all employees from implementation alone.

During initial rollout the named operator reviews health and failed/expired jobs each work session; managers review missing days, exposure, source-credit reversals, quality/rework, blocked/deadline risk, effort, staff friction and known/uncertain AI costs daily. Review both cohort source completeness and mail queue/held/UNKNOWN states. Never put private payloads or secrets into monitoring logs. Expand later only after the predefined comparison, independent review, maintained quality, resolved operating checks and separate authorization. Lack of evidence is a reason to keep measuring, not a benefit claim.

Stop/hold for any unauthorized disclosure or source/credit duplication, unsafe delegation, loss of required current permissions, unexplained quality/effort deterioration, repeated failed/expired jobs, unknown external send/spend ambiguity, failed static/web health or unusable employee flow. Pause the pilot (with enforcement on), disable affected recurrence/provider/live-email flags and restart or stop the loaded scheduler/worker. Setting only the rollout-write flag off does not revoke access. Do not turn enforcement off as a privacy containment shortcut.

For application rollback, stop the new units and both web units as needed, retain the additive schema, receipts, accepted/retracted evidence, job dedupe keys and UNKNOWN external intent. Restore reviewed compatible code/settings/static, verify old routes/permissions/source reconciliation, then reopen deliberately. A full database restore needs separate operator authorization and a plan to reconcile all writes and possible external side effects since the backup; it is not an automatic response to a CSS error. Do not reverse evidence migrations, erase audit history or retry ambiguous sends/spend to make a dashboard green.

## Outstanding real decisions

**admin** is the proposed accountable owner for the real roster/manager matching, study dates, registered owner scope, accepted staffing/output/quality sources, power/parallel-trends review, actual native accessibility/staff timings, production backup/restore and supervisor operator, retention/privacy and correction policy, approved email sender/top managers, company-goal metrics/horizons and separate deployment/release decision. Technical operator and independent reviewer names remain unconfirmed. The real USD 2m cumulative-sales definition is confirmed; sales recognition/baseline/horizon details and measurable supplier/AI-leadership definitions remain open. WhatsApp stays selected later. Calendar, voice, offline and other channels remain conditional, unselected.

The [complete requirement reconciliation](TASKER_STAGE13_REQUIREMENT_RECONCILIATION_2026-10-31.md) retains every matrix ID and distinguishes local implementation evidence, unresolved operating acceptance and conditional work. Software completion does not close those owner or real productivity gates.

## Final local closeout

**Local software candidate: verified within this recorded scope. Real pilot release: NO-GO pending operating evidence. Real productivity: pending.**

- The fresh broad PostgreSQL checkpoint passed **1,489 Python tests in 1,122.981 seconds** across Tasker, configuration, Inventory, Credit Control, Sales, Shop, Accounting and Operations (`output/stage13-regressions.log`).
- After cohort/navigation/dispatch refinements, **326 targeted compatibility tests passed in 424.379 seconds** (`output/stage13-final-focused-tests.log`). These cover pilot measurement, protocol and release checks; PostgreSQL races; the cross-stage journey; shell/dashboard/UI; AI gateway, quotas, budgets and asynchronous work; improvement/recurrence; and operational sources. The earlier Stage 13-only checkpoint was 33 passing tests. The final current-source synthetic access smoke additionally checks the last preference/home/sidebar refinements and normal-access rollback (`output/stage13-final-smoke.json`).
- **77 JavaScript tests passed.** System checks and migration drift were clean. Strict WhiteNoise collection copied 235 files and post-processed 1,097 variants. New pilot migrations were applied only to disposable PostgreSQL. The separate restore matched the recorded synthetic snapshot across 171 tables and retained evidence/constraints.
- A bounded real scheduler/worker pass on the synthetic database processed three jobs successfully; jobs health was healthy with no failed/expired lease. This is a local pass, not installed production supervision. Degraded provider and failure/retry proofs use the existing gateway/outbox and mocked failure only; no paid request or external message was sent.
- Browser checks covered manager/employee/restricted scope, desktop and 390px layouts, preserved invalid measurement input, linked keyboard error focus, all 153 release-ledger IDs, saving a full synthetic protocol and adding an explicit synthetic draft member while access stayed disabled. An additional enforced preview confirmed measurement/preferences remain available, with no peer evidence, AI links or capture controls. Temporary viewport overrides and the enforced-preview server were removed. The normal synthetic preview remains on loopback port 8765.
- Screenshot artifacts include `output/stage13-pilot-desktop.png`, `output/stage13-pilot-mobile.png`, `output/stage13-employee-scope.png` and `output/stage13-cohort-held.png`. The final source digest and evidence references are in `output/tasker-stage13-release-evidence.json`.

All 153 IDs retain a disposition: 86 have recorded local evidence, 62 retain explicit real acceptance work, four are conditional/unselected and WhatsApp is selected later. No real participants were enrolled, no actual productivity percentage was established, and no commit, push or production deployment was performed. Admin's next concrete step is to identify registered participants/the team manager and approve prospective business sources and dates, using the operating checklist before any rollout.
