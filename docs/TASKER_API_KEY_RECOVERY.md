# Tasker API key recovery

Prepared 22 October 2026; status checked 26 October 2026. This procedure addresses the previously recorded exposed OpenAI credential. A replacement key does not revoke the old one; a clean working file does not erase Git history.

## Owner: revoke the exposed key

1. Open [OpenAI API keys](https://platform.openai.com/api-keys) and select the organization and project used by Boforg Tasker.
2. Find the previously exposed key and revoke/delete that key. If identification is uncertain, inspect its name and creation information privately; do not send key text to this chat. Consider other applications using that key before changing their configuration.
3. Review the project's recent usage for unfamiliar activity. Record only a confirmation and date here, never credential values.

OpenAI recommends promptly revoking and replacing compromised credentials: [official safety guidance](https://developers.openai.com/api/docs/guides/safety-best-practices#revoke-compromised-api-keys).

## Repository cleanup

The recorded affected path is `tasker/README_AI_Extensions.md`. On 26 October 2026 the owner explicitly authorized targeted cleanup and a non-printing history assessment. The helper removed **one** recognized OpenAI key token while preserving all other bytes. A follow-up dry run found **zero** remaining matching tokens. No document excerpt or key value was displayed. This authorization was limited to the targeted tools; the general exclusion from raw reads/diffs remains in place.

The targeted helper is `tools/redact_tasker_api_key.py`. It replaces recognized OpenAI key tokens with `<REDACTED_OPENAI_API_KEY>`, preserving other bytes. Its default is a dry run; `--write` requests the actual replacement. It prints fixed status/counts rather than matches or document excerpts. Do not run it without authorization to read this specific file. It does not modify `.env`, Git history or the index.

```powershell
# From C:\Users\admin\Videos\Boforg\IMS\BoforgIMS
# Only after authorizing the helper to read this affected document:
.\venv\Scripts\python.exe tools/redact_tasker_api_key.py
.\venv\Scripts\python.exe tools/redact_tasker_api_key.py --write
```

The authorized history check found **2 historical revisions, 1 unique document blob and 0 key-pattern blobs** under local Git refs for this fixed path. No history rewrite was needed for that result or performed. This is not a repository-wide or remote-copy certification. Revocation remains owner-confirmed evidence, independent of these scans.

The history helper inspects only this document path under refs already present locally and prints counts; it does not certify other paths, earlier rename paths, reflogs, remote copies or artifacts:

```powershell
.\venv\Scripts\python.exe -m tools.audit_tasker_key_history
```

`.gitignore` now covers `.env` and `.env.*`, while allowing a placeholder-only `.env.example`. Ignore rules do not untrack previously committed files. The existing manual staged gate remains available:

```powershell
.\venv\Scripts\python.exe tools/check_staged_secrets.py
```

That gate deliberately blocks the affected README and secret environment paths without reading their contents. It scans staged data only; an empty-index pass is not proof of cleanup or revocation. Do not stage the excluded README until its separate review policy is deliberately resolved.

## Owner: enter a replacement privately

1. Create a replacement key in the intended OpenAI project using the appropriate account and permissions. Do not create an organization admin key for Tasker inference.
2. Put it into the application's private secret configuration as `OPENAI_API_KEY`. The application already reads this variable; no literal key belongs in Python, templates, Markdown, chat or a shell command saved in history.
3. For this local checkout, edit the existing ignored `.env` privately and replace only its `OPENAI_API_KEY` value. Preserve unrelated settings and do not make a second duplicate assignment. For a deployed application, use its service environment or secret manager.
4. An explicit process/service `OPENAI_API_KEY` overrides this checkout's `.env` because dotenv uses `override=False`. If a stale environment value exists, update it in that service's configuration rather than repeatedly editing `.env`.
5. Restart the actual application process after updating its environment. The local review preview deliberately uses test settings that blank the key and disable AI; it is not a live credential test.

OpenAI recommends server-side environment variables or a secret-management service: [official production guidance](https://developers.openai.com/api/docs/guides/production-best-practices#api-keys).

For private local entry on Windows, press **Win+R** and open the file in your own editor:

```text
notepad "C:\Users\admin\Videos\Boforg\IMS\BoforgIMS\.env"
```

Replace the existing assignment with the following form, entering the real new value only inside that editor, then save and close it:

```dotenv
OPENAI_API_KEY="your_api_key_here"
```

Do not paste the actual value into this document, chat, a screenshot or a shell assignment. Keep other `.env` entries intact. The assistant has not opened or modified this secret file.

## Check without displaying the key

The offline management command reports only whether configuration is present and whether it contains whitespace. It does not authenticate the key, query the database, call the provider or enable AI:

```powershell
# Run privately with the settings/environment used by the actual application:
.\venv\Scripts\python.exe manage.py check_tasker_ai_credentials
```

Do not use a command that prints settings, environment variables, prefixes, suffixes or key hashes. If sharing the result, share only the command's fixed status message.

Live provider validation and AI activation are separate steps. Tasker's per-employee $0.50 weekly monetary governance remains Stage 9 work. This credential cleanup does not implement or certify that monetary cap.

## Evidence ledger

| Action | Status on preparation |
| --- | --- |
| Exposed key revoked in OpenAI | Owner explicitly confirmed “The exposed key is revoked” on 22 October 2026; not independently authenticated |
| Affected working document redacted | Authorized and completed 26 October: 1 token removed, follow-up scan 0; other document bytes preserved |
| Historical exposure assessed | Authorized and completed 26 October: 2 revisions, 1 unique blob, 0 key-pattern blobs for this path under local refs; no history changed |
| Replacement privately configured | Owner supplied offline diagnostic output on 26 October: API key configured; organization/project overrides not configured. No key value supplied or inspected. This is presence evidence only, not proof of validity or which environment source supplied it. |
| Replacement authenticated | Not performed |
| Preview AI activation | Disabled and unchanged |
| `.env` variants excluded from new Git additions | Implemented; `.env`, `.env.local`, `.env.production` ignore checks pass |

The targeted cleanup helper, history helper, offline diagnostic and existing staged gate have **28 passing synthetic tests** (`manage.py test tasker.tests.test_key_history_audit tasker.tests.test_key_redaction tasker.tests.test_key_diagnostic tasker.tests.test_secret_gate --settings=config.test_settings --noinput`, rerun 26 October in 0.172 seconds). These tests used temporary synthetic files/settings and made no database/provider requests. They did not read the actual excluded README, actual historical document blobs or secret files. The test-settings diagnostic correctly reported the deliberately blank review key as missing; no real credential was authenticated. Explicit-file whitespace checks and the manual staged gate also passed; an empty-index pass is not independent cleanup evidence.

The owner's subsequent application-settings diagnostic reported `OpenAI API key: configured.` with no provider/database request. It also retained the existing dotenv parse warning at line 9. The owner must correct that entry privately; its contents have not been read, inferred or modified by the assistant. The warning does not establish whether the detected credential came from `.env` or a higher-priority process environment. Authentication and AI activation remain unperformed.

Stage 1 credential cleanup acceptance is recorded for the defined scope: owner-confirmed revocation, targeted working-file cleanup, local path-history assessment and secret-scanning safeguards. Replacement presence is owner-reported; authentication and the dotenv warning remain separate actions. No deployment, history rewrite, Git commit/push or paid provider call was performed or authorized by this document.
