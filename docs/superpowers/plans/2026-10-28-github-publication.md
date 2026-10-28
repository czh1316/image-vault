# GitHub Public Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assemble the existing Image Vault project into a clean, public-ready Git repository that includes the login cover assets while excluding secrets, private data, local environments, reverse-engineering evidence, and generated files.

**Architecture:** Keep the existing Flask application layout and runtime data boundary unchanged. Use a root allowlist enforced by `.gitignore`, preserve deployment configuration through `.env.example` and external `VAULT_DATA_DIR`/`VAULT_COVERS_DIR` settings, and validate the exact staged tree before the first source commit.

**Tech Stack:** Python 3.14 virtual environment, Flask, Pillow, Waitress, Playwright, pytest, Git.

**Spec:** `docs/superpowers/specs/2026-10-28-github-publication-design.md`

## Global Constraints

- Never stage `data/`, `private-settings.json`, databases, account images, or API keys.
- Never stage `.venv/`, Python caches, `.pytest_cache/`, `.idea/`, `analysis/`, logs, or EXE files.
- Include only the eight named login cover JPGs; keep other future local images ignored.
- Preserve current application behavior and deployment configuration; do not refactor business logic.
- Do not add a license or create a GitHub repository without explicit repository details.

---

### Task 1: Lock the public file boundary

**Files:**
- Modify: `.gitignore`
- Include: `image/README.md`, `image/.gitkeep`, eight whitelisted `image/*.jpg` assets
- Reference: `docs/superpowers/specs/2026-10-28-github-publication-design.md`

**Interfaces:**
- Produces a Git ignore boundary where `git check-ignore` rejects local data and accepts only the curated cover files.

- [x] **Step 1: Check each sensitive path is ignored**

Run:

```powershell
git check-ignore -v data/private-settings.json data/vault.sqlite3 analysis/server.log .venv/Scripts/python.exe .idea/workspace.xml 041c7e6593a75ad4684dc581bcf2180e.exe
```

Expected: every path prints a matching `.gitignore` rule.

- [x] **Step 2: Check the curated cover allowlist is visible**

Run:

```powershell
git check-ignore -v image/3f5a5912e1064d25986a40bc0aa58c61.jpg image/77b915aed32b45369edb23e28b27526a.jpg image/new-local-cover.jpg
```

Expected: the two named files are not ignored; `image/new-local-cover.jpg` is ignored by the generic image rule.

- [x] **Step 3: Verify cover metadata is clean**

Run:

```powershell
@'
from pathlib import Path
from PIL import Image
for path in sorted(Path("image").glob("*.jpg")):
    with Image.open(path) as image:
        assert not image.getexif(), path
print("all cover images have empty EXIF metadata")
'@ | .venv\Scripts\python.exe -
```

Expected: the command prints the success line and exits 0.

- [x] **Step 4: Commit the boundary documentation**

Run:

```powershell
git add .gitignore image image/README.md docs/superpowers/specs/2026-10-28-github-publication-design.md
git diff --cached --check
git commit -m "docs: define public release boundary"
```

Expected: Git creates a commit containing only the boundary rules, cover assets, and release specification.

### Task 2: Stage the application source and documentation

**Files:**
- Add: `image_vault/`, `static/`, `tests/`, `scripts/`
- Add: `run.py`, `start.bat`, `start.ps1`, `README.md`
- Add: `requirements.txt`, `requirements-dev.txt`, `.env.example`
- Add: `docs/superpowers/specs/2026-10-22-image-vault-design.md`, `docs/superpowers/plans/2026-10-22-image-vault.md`

**Interfaces:**
- Consumes the existing Flask `create_app(config=None)` entry point and existing test suite.
- Produces a source tree that can be installed and launched with the documented environment variables.

- [ ] **Step 1: Add only the public source paths**

Run:

```powershell
git add .env.example README.md docs/superpowers/plans/2026-10-22-image-vault.md docs/superpowers/specs/2026-10-22-image-vault-design.md image_vault requirements-dev.txt requirements.txt run.py scripts start.bat start.ps1 static tests
```

- [ ] **Step 2: Inspect the staged path list**

Run:

```powershell
git diff --cached --name-only
```

Expected: only source, tests, scripts, public docs/config samples, and the eight curated cover JPGs appear; no path under `data/`, `analysis/`, `.venv/`, `.idea/`, or any `__pycache__/` appears.

- [ ] **Step 3: Scan staged text for credential patterns**

Run:

```powershell
git grep --cached -n -I -E "(AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|ghp_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z]{20,}|PIXABAY_API_KEY[[:space:]]*[:=][[:space:]]*[^#[:space:]]+)" -- .
```

Expected: no output and exit status 1 (no matching credential).

- [ ] **Step 4: Commit the public source tree**

Run:

```powershell
git diff --cached --check
git commit -m "feat: publish image vault application"
```

Expected: Git creates the initial source commit without private files.

### Task 3: Verify runtime and tests

**Files:**
- Test: `tests/`
- Verify: `image_vault/`, `run.py`, `README.md`

**Interfaces:**
- Consumes the staged source tree and the local Python 3.14 environment.
- Produces fresh evidence that the published code imports, tests pass, and no sensitive path is tracked.

- [ ] **Step 1: Run the full test suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

Expected: pytest exits 0 with all collected tests passing.

- [ ] **Step 2: Run a clean app health check using temporary data**

Run:

```powershell
@'
import tempfile
from image_vault import create_app

with tempfile.TemporaryDirectory() as directory:
    app = create_app({"TESTING": True, "DATA_DIR": directory, "COVERS_DIR": "image", "RUN_WORKERS": False})
    response = app.test_client().get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
print("health check passed")
'@ | .venv\Scripts\python.exe -
```

Expected: the command prints `health check passed` and exits 0.

- [ ] **Step 3: Verify the final tracked tree**

Run:

```powershell
git ls-files | rg '(^|/)(data|analysis|\.venv|\.idea|__pycache__|\.pytest_cache)(/|$)|(^|/).*\.exe$|private-settings\.json|\.sqlite3($|-)'
```

Expected: no output and exit status 1.

- [ ] **Step 4: Record clean status and commit verification notes**

Run:

```powershell
git status --short --branch
git log --oneline --decorate -3
```

Expected: the working tree is clean and `main` contains the boundary and source commits.

### Task 4: Configure the GitHub remote and push

**Files:**
- Modify: local Git remote configuration only; no project files

**Interfaces:**
- Consumes a user-provided GitHub repository URL or repository name and available GitHub authentication.
- Produces `origin/main` pointing to the same verified commits.

- [ ] **Step 1: Confirm the destination**

Obtain the exact GitHub repository URL and whether the repository is public or private before changing remotes.

- [ ] **Step 2: Configure and inspect the remote**

Run with the confirmed URL:

```powershell
git remote add origin <confirmed-repository-url>
git remote -v
```

Expected: fetch and push URLs match the confirmed destination.

- [ ] **Step 3: Push the verified main branch**

Run:

```powershell
git push -u origin main
```

Expected: Git reports a successful new `main` branch on the confirmed remote.

- [ ] **Step 4: Verify remote alignment**

Run:

```powershell
git ls-remote --heads origin main
git status --short --branch
```

Expected: remote `main` resolves to the local `HEAD`, and the local working tree remains clean.

