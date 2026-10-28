# 图仓 Implementation Plan

**Goal:** Build and verify the approved account-based image crawler website.
**Architecture:** Flask same-origin API, SQLite metadata and opaque server sessions, private local image storage, bounded background worker, static responsive frontend.
**Tech Stack:** Python 3.14, Flask, Pillow, Waitress, pytest, Playwright for browser verification.
**Spec:** ../specs/2026-10-22-image-vault-design.md

## Global Constraints

Preserve original EXE. Keep reverse engineering observations distinct from new design. No captcha bypass. User data remains private. Per job 1–200 images; max 2 workers and 1 active job per user; per image 12 MiB; default quota 500 MiB per user.

## Task 1 — Account and storage boundary
- [x] Write tests exercising real Flask clients with a temporary SQLite DB: registration/login, persistent sessions, CSRF rejection, cross-account image isolation and deduplication. Run `.venv/Scripts/python -m pytest tests/test_app.py -q` and observe missing implementation.
- [x] Implement `image_vault/db.py`, `auth.py`, `app.py`, `storage.py`. Interface: `create_app(config=None)` returns Flask app; `get_db()` returns per-context sqlite connection; `store_image(user_id, job_id, keyword, candidate, bytes)` stores validated image or reports duplicate/quota.
- [x] Re-run API tests and resolve observed failures.

## Task 2 — Collection and persistent jobs
- [x] Add tests for Baidu empty sentinel/antiFlag, Bing metadata, unsafe URL/redirect rejection, actual worker completion, cancellation, and restart recovery. Fixture only external network boundaries; use real DB/files.
- [x] Implement `image_vault/crawler.py`, `jobs.py`, `network.py`; explicit limits, deadlines, source reporting and durable progress. `collect_candidates(keyword, source, page)` returns candidates; `run_job(app, job_id)` executes with context.
- [x] Run all tests; verify no cross-user mutation or unbounded fetches.

## Task 3 — Browser surface and delivery
- [x] Implement `static/index.html`, `app.js`, `style.css`; accessible forms, task polling, gallery search/filter, selection, image dialog, ZIP/delete and responsive empty states.
- [x] Add `run.py`, `start.ps1`, `start.bat`, dependency pins, environment example and README.
- [x] Run real browser registration/login, create/cancel tasks, logout and second user isolation at desktop/mobile sizes. Save screenshots to analysis; inspect visually.
- [x] Verify live collection where public source responds; document external-source blocks honestly. Re-run automated checks, verify no passwords/data tracked, deliver local URL and startup instructions.
