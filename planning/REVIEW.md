# Review of Uncommitted Changes

_Re-checked 2026-11-12: no file in scope has changed since the 2026-11-10 review below. The modification times of everything in scope predate this file. I re-verified the critical findings against the working tree, and they all still hold. They are: the key line in `PLAN copy.md`, `massive_client.py:103`, the Stop hook, the empty plugin files, the tracked Cerebras skill and the `.gitignore` gaps. The only new evidence is a fresh coverage run (`pytest --cov=app`): 73 passed, 91% total. That run adds items 26 and 27 and updates item 22._

_Reviewed 2026-11-10. Scope: everything since commit `14550e1`. That is 3 modified tracked files (`.claude/settings.json`, `README.md`, `planning/PLAN.md`) and the untracked `.claude/agents/`, `.claude/commands/`, `independent-reviewer/`, `curl`, `planning/PLAN copy.md` and the three new market docs (`MASSIVE_API.md`, `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`). Docs were checked against `backend/app/market/` and the installed `massive` 2.2.0 client. The backend suite still passes (73 tests). Secret values are never reproduced here. This file replaces all earlier review sections; points from them that still apply are carried forward and marked (carried)._

## Summary

The three new market docs are well researched and mostly accurate. The main bug they report is real: `massive_client.py` reads `last_trade.timestamp`, which doesn't exist (the client has `sip_timestamp`, in nanoseconds), so Massive mode caches nothing. That should be fixed in code before anyone builds on it.

The new EOD/auto-detect design has two holes that an implementing agent would copy:
- Network, 429 and 5xx errors during the startup probe crash app boot.
- An unknown symbol in EOD mode causes repeated full-market refetches.

The blocking repo-hygiene items from earlier reviews are all still open: a live key in `planning/PLAN copy.md`, the Stop hook in the shared settings, the empty plugin, the stray `curl` file, the tracked Cerebras skill and the `.gitignore` gaps. PLAN.md and the README now contradict the new docs on Massive tiers, env vars and the SSE payload.

## Critical

1. **A live API key is in an untracked file** — `planning/PLAN copy.md:125` (carried)
   - The file is a stale pre-migration copy of PLAN.md with no unique content. Nothing ignores it, so `git add -A` would commit it.
   - **Fix:** delete the file, rotate the key in the Anthropic console, and stage files by name for the next commit.

2. ~~**Massive mode produces no prices**~~ **Resolved 2026-11-12:** `parse_snapshot()` now reads `sip_timestamp` (ns) and falls back through the minute bar, day bar and previous close. The tests use real `TickerSnapshot.from_dict` inputs. — `backend/app/market/massive_client.py:103`
   - The line reads `snap.last_trade.timestamp / 1000.0`, but `LastTrade` has no `timestamp` field (verified: `hasattr(...) == False`). The resulting `AttributeError` is swallowed at line 110, so nothing is cached.
   - `tests/market/test_massive.py` uses `MagicMock` snapshots, which accept any attribute, so the tests don't catch it.
   - The README status table (line 61) marks the Massive client "✅ Done".
   - **Fix:** apply `parse_snapshot` from MARKET_INTERFACE §6.1 and build the test inputs with `TickerSnapshot.from_dict(...)`, in one commit. Until then, change the README row to "Massive client: known bug (MARKET_INTERFACE §6.1)".

3. ~~**The Stop hook in the shared `.claude/settings.json` forces a review on every turn**~~ **Resolved 2026-11-12:** the hook was removed, and the file now matches the last commit. — `.claude/settings.json:7-17` (carried)
   - It is a `type: "prompt"` hook, so a model judges whether Claude may stop. Every turn, including unrelated ones and turns that only rewrote this file, is blocked until REVIEW.md is rewritten. That costs tokens, keeps rewriting this file, and the file is committed for every collaborator.
   - "Use the OPUS model" in the prompt text selects nothing.
   - The file also lost its trailing newline.
   - **Fix:** remove the hook. Run reviews on demand with a slash command (e.g. `.claude/commands/review-changes.md`) or the `reviewer` agent. If a hook is really wanted, put it in `settings.local.json` and guard it with `stop_hook_active`.

## Important

4. **The MARKET_INTERFACE §6.2 startup probe can stop the app from booting** (contradicts §5)
   - `_detect_mode` catches only `BadResponse`. The client sends 413/429/499/5xx to urllib3 `Retry` (`raise_on_status=True`), and once retries run out it raises `urllib3.exceptions.MaxRetryError`, not `BadResponse`. Connection failures raise the same error.
   - Both escape `start()`, then the lifespan, so the app fails to start. §5 says the opposite: "app still starts … `/api/health` reports `massive:error`".
   - `massive:error` is also missing from the `description` values in §3.4, and nothing sets it.
   - **Fix:** in `_detect_mode` and the first `_poll_once`, catch `Exception`, log the type only, and fall back to `snapshot` mode, or retry detection on the next cycle. Add `massive:error` to §3.4 and define when it is set (e.g. after N consecutive failed polls).

5. **EOD mode refetches over and over for unknown symbols, and its "bars cache" is described but never implemented** (MARKET_INTERFACE §6.2, §6.3)
   - `_eod_missing()` forces a refetch whenever a tracked ticker has no cache entry. An invalid or OTC symbol never gets one, so every hourly poll makes 2–5 Grouped Daily calls of several MB each, indefinitely.
   - The prose says "Bars are cached by the source between fetches", but `_fetch_eod_rows` keeps no bars.
   - A newly added ticker in EOD mode waits up to `3600 s` for a price, and trades are rejected in the meantime. §6.3's prefetch covers snapshot mode only.
   - **Fix:**
     - Store the latest and prior grouped dicts on the source.
     - In `add_ticker()`, price the ticker from them immediately.
     - Record tickers that are absent from the grouped response as "unknown for <date>", so they don't trigger a refetch.

6. **The EOD request budget and the 403 on the current date are not handled** (MASSIVE_API §4.1, MARKET_INTERFACE §6.2) (partly carried)
   - Call count: the auto probe (1 call) plus a long-weekend lookback (up to 5 calls) is 6 calls at startup, above Basic's 5/min.
   - Retry timing: urllib3 retries 429s with `backoff_factor=0.1`, which is under 1 s in total, unless the server sends `Retry-After`. Those retries spend the same budget.
   - Error handling: `last_sessions()` doesn't catch the per-date 403 that MASSIVE_API §2 item 2 describes. Just after midnight ET, "yesterday" can still return 403, and that aborts the whole fetch instead of stepping back.
   - **Fix:**
     - In `last_sessions`, treat a 403 `BadResponse` for one date as "not published, step back".
     - Skip the probe when `MASSIVE_MODE=eod`.
     - Cap the lookback so probe plus fetch stays at 5 calls or fewer.
     - Pass `RESTClient(retries=0)` in EOD mode and let the hourly loop do the retrying.

7. **MASSIVE_API §6 error table and examples are incomplete**
   - The "HTTP 413, 429, … retried then raised" row doesn't name the exception (`MaxRetryError`), so the `except BadResponse` example misses it.
   - The `"403" in body` check is ineffective: `BadResponse` carries only the response body, not the status code. Only the `NOT_AUTHORIZED` match does anything. That string has still not been checked against a real Basic key (carried).
   - **Fix:** document `MaxRetryError`. Detect the plan with `raw=True` and check `resp.status == 403`. Record one real Basic-key response in §6.

8. **PLAN.md and the README contradict the new docs** (partly carried)
   - PLAN.md §6 ("Free tier (5 calls/min): poll every 15 seconds") and the `massive_client.py:23-25` docstring conflict with MASSIVE_API §2: Basic has no snapshot access and gets EOD data only.
   - PLAN.md §5 lists 3 env vars. The docs add `MASSIVE_MODE`, `MASSIVE_POLL_INTERVAL` and `SIM_EVENT_PROBABILITY`. None of them appear in PLAN.md, the README env table or any `.env.example` (which doesn't exist yet).
   - PLAN.md §13 "Drop `change direction` from the SSE payload" conflicts with MARKET_INTERFACE §3.2, which keeps `direction` and tells the frontend to use it.
   - PLAN.md §13 items 6, 7, 8 and 9 are now answered by MARKET_INTERFACE §3.2, §3.1 and §7, but still read as open questions.
   - **Fix:**
     - Update PLAN §6 to read "Basic = EOD mode; Starter+ = snapshot polling".
     - Add the three variables to PLAN §5 and the README, and commit an `.env.example` with empty values.
     - Mark §13 items 6–9 resolved, with pointers to the docs.
     - Delete the "drop direction" bullet.

9. **Stale duplicate docs in `planning/archive/`** (carried)
   - `CLAUDE.md:5` still sends agents to `planning/archive` "for more details". The archive holds the superseded `MASSIVE_API.md`, `MARKET_INTERFACE.md` and `MARKET_SIMULATOR.md`, which mention neither the timestamp bug nor the Basic-tier limit.
   - **Fix:** add a "SUPERSEDED by planning/<file>" first line to each archived copy, or delete them. Point `CLAUDE.md` at the three new docs.

10. **`independent-reviewer/` is an empty plugin** (carried)
    - `.claude-plugin/plugin.json` and `hooks/hooks.json` are both 0 bytes. That is invalid JSON, so the plugin fails to load if enabled.
    - **Fix:** fill in the manifest (`name`, `version`, `description`) and a valid `hooks.json`, or delete the directory.

11. **A tracked Cerebras skill contradicts PLAN §9** (carried)
    - `.claude/skills/cerebras/SKILL.md` is still tracked and offered to agents for "calling an LLM", while PLAN §9 now says "no LiteLLM, no OpenRouter". As a result, PLAN §13 item 2 ("all … Cerebras references are gone") is false.
    - **Fix:** delete the skill in the same commit as the PLAN.md change.

12. **Backend dependencies are behind the plan** — `backend/pyproject.toml` (carried)
    - `anthropic` and `python-dotenv` are missing, `rich` is a production dependency, and `massive>=1.0.0` allows 1.x, although the docs and model names were verified against 2.2.0.
    - **Fix:**
      - Add `anthropic` and `python-dotenv`, and pin `massive>=2.2,<3`.
      - Move `rich` to a `demo` extra.
      - In PLAN §9, note that `RateLimitError` must be caught before `APIStatusError`, because it is a subclass.

## Minor

13. **MASSIVE_API §4.2 gets the return type backwards.**
    - `get_previous_close_agg` uses `result_key="results"`, and the API returns a list there, so 2.2.0 returns `list[PreviousCloseAgg]`, not a single object.
    - **Fix:** correct the comment. The `isinstance` guard is fine to keep.
14. **MASSIVE_API §1 "`RESTClient()` reads `MASSIVE_API_KEY`" leaves out a timing catch.**
    - The default is `api_key=os.getenv(...)` in the signature, so the variable is read once, when `massive` is first imported. If `.env` is loaded after that import, the variable is not seen.
    - An empty-string key skips `AuthError` and fails later with 401.
    - **Fix:** say this in §1, and keep passing `api_key=` explicitly, as FinAlly already does.
15. **MASSIVE_API §6 warns about `?apiKey=` in logged URLs, but the client sends `Authorization: Bearer`.**
    - **Fix:** reword it to "the client uses a header; never log headers or enable `trace=True` in production".
16. **MARKET_INTERFACE §5 imports `stream_router`, which doesn't exist until work item 8.**
    - Copying the snippet as written gives an `ImportError`. §3.2 likewise tells the frontend to use `day_change_percent`, which doesn't exist until work item 3.
    - **Fix:** mark both with 🔧, or show `create_stream_router(cache)` until item 8 lands.
17. **MARKET_INTERFACE §6.2 `start()` overwrites pending tickers.**
    - `self._tickers = [...]` contradicts §3.4 rule 5, which requires honoring `add_ticker()` calls made before `start()`. The snippets also use `dt_time` and `last_sessions` without importing them.
18. ~~**`PriceCache.remove()` doesn't bump `version`**~~ **Resolved 2026-11-12:** `remove()` now bumps the version, and the stream sends `{}` once the last ticker is removed. — `cache.py:59-62`
    - In EOD mode prices never change, so SSE clients never see a removal.
    - `stream.py:80` also skips sending an empty cache.
    - **Fix:** bump `version` in `remove()`, and send `{}` when the cache is empty.
19. **MARKET_SIMULATOR §4.1 drops a defensive copy.**
    - `TICKER_PARAMS.get(t, DEFAULT_PARAMS)` loses the `dict(...)` that the current code (`simulator.py:152`) has, so every unknown ticker would share one mutable dict.
    - **Fix:** keep the copy.
20. **MARKET_SIMULATOR §2.2 states the positive-definiteness condition incompletely.**
    - The condition also needs the cross-group ρ to be ≥ 0. Add that to the invariant.
21. **The simulator's "daily change %" is really "since server start".**
    - With S1, the reference price resets on every restart.
    - **Fix:** label the column accordingly in the UI, e.g. "Chg (session)", or say so in PLAN §10.
22. **The README is out of date in several places.**
    - The test table says `test_simulator.py` has 17 tests; it has 19 (line 496) (carried).
    - The coverage figures are stale. The status table says "about 84%", but the measured total on 2026-11-12 is 91%. The test table says `test_massive.py` covers 56%, but `massive_client.py` is now at 94%. `stream.py` (33%) is missing from the table. `MARKET_DATA_SUMMARY.md` carries the same stale numbers.
    - Line 234 ("0.1% … one event every 50 seconds") goes stale if S2 is adopted. Update it in the same commit.
    - `<this-repo-url>` is a placeholder (carried).
    - The README duplicates large parts of PLAN.md, and the two will drift.
23. **Workspace leftovers** (carried)
    - `curl` is a 0-byte file at the repo root; delete it.
    - Four `.DS_Store` files are untracked.
    - `.gitignore` lacks `.DS_Store`, `db/*.db`, `node_modules/`, `frontend/.next/` and `frontend/out/`.
24. **Agent and command definitions need small fixes.**
    - `.claude/commands/doc-review.md` has no `description` or `argument-hint` frontmatter, no trailing newline, and no guidance for a bare `/doc-review`.
    - `.claude/agents/reviewer.md` step 4 replaces REVIEW.md wholesale using its own "PLAN.md Review" format, while the Stop hook writes a different format to the same file.
    - **Fix:** pick one owner for REVIEW.md, or give the plan review its own file.
25. **PLAN.md §13 item 1 is inaccurate** (carried).
    - It says the key "was exposed in a tracked file". It was only ever in the untracked copy, which still exists (Critical 1).
26. ~~**The SSE endpoint is the least-tested module**~~ **Resolved 2026-11-12:** `test_stream.py` added, and `stream.py` is now at 97%. This also fixed `create_stream_router` registering routes on a shared module-level router. — `backend/app/market/stream.py` at 33% (24 of 36 statements missed) (new)
    - It is the one module the frontend depends on directly. Its tests don't cover the `retry:` preamble, the version-gated sends, the empty-cache skip (item 18), or the cleanup when a client disconnects.
    - **Fix:** add `tests/market/test_stream.py`. Drive the generator with a stub request whose `is_disconnected()` flips after N ticks, then assert on the first frame (`retry: 1000`), one `data:` frame per version bump, and nothing sent while the version is unchanged. This matters more than raising `massive_client.py` coverage any further.
27. ~~**High coverage on `massive_client.py` hides Critical 2**~~ **Resolved 2026-11-12** (see Critical 2). (new)
    - The 94% figure comes from `MagicMock` snapshots, which accept `.timestamp`, so those lines run "successfully" in the tests while failing in production. Coverage here measures executed lines, not correctness.
    - **Fix:** as in Critical 2, build the test snapshots with `TickerSnapshot.from_dict(...)`.

## Looks good

- PLAN.md's switch to the Anthropic SDK is correct. `claude-opus-5-5`, `client.messages.parse` with a Pydantic schema, `output_config={"effort": "low"}`, rejected prefill and the `refusal`/`max_tokens` stop-reason checks all match the current API. `effort: "low"` is worth keeping, because Opus 5.5 defaults to `medium`.
- MASSIVE_API.md matches the installed client:
  - signatures for `get_snapshot_all`, `get_grouped_daily_aggs`, `list_universal_snapshots` and `list_aggs`
  - model fields for `LastTrade`, `TickerSnapshot`, `GroupedDailyAgg`, `DailyOpenCloseAgg`, `MarketStatus` and `EquityTrade`
  - the `RESTClient` defaults (10 s timeouts, `retries=3`, `AuthError` with no key)
- The snapshot fallback chain and the nanosecond/millisecond unit table are right. The "EOD reference = prior session close" definition is now consistent between MARKET_INTERFACE §3.1 and §6.2; that earlier contradiction is resolved.
- I recomputed the MARKET_SIMULATOR §2.4 figures and they are correct: per-tick σ $0.012, hourly 0.54%, jump σ 9.7% vs 4.3%, and about 180 −5% jumps to reach $0.005. The cross-references between the three docs and PLAN.md all resolve.
- The price-universe rule (watchlist ∪ open positions), the ticker normalization at the API boundary, and the testing tables in MARKET_INTERFACE §9 and MARKET_SIMULATOR §7 are concrete enough for agents to implement.
- `.claude/agents/code-improver.md` is well-scoped: it is read-only and limited to the Read, Grep and Glob tools.

## Suggested commit order

1. Delete `planning/PLAN copy.md` and `curl`, rotate the key, remove the Stop hook, and fix or delete `independent-reviewer/`. Update `.gitignore`.
2. Fix the Massive timestamp bug and switch the tests to real models (Critical 2, item 27). Add `test_stream.py` (item 26).
3. Revise the three docs for items 4–7 and 13–20. Update PLAN.md §5, §6 and §13 and the README (items 8 and 22). Mark the archived duplicates superseded and update `CLAUDE.md`.
4. Delete the Cerebras skill and update `pyproject.toml`. Commit the docs, `.claude/agents/` and `.claude/commands/` with `git add <paths>`.
