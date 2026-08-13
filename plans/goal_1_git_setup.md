# Goal 1: Connect Fantasy Football Project to Git

## Objective
Initialize a local git repository for `~/fantasy_football_projects` and connect it to a remote GitHub repository, mirroring the setup used in `~/nba_projects`.

---

## Steps

### 1. Initialize the Local Git Repository
- Run `git init` inside `~/fantasy_football_projects`
- This creates a `.git` folder and sets up local version control

### 2. Create a `.gitignore` File
- Add a `.gitignore` appropriate for a Python data project, ignoring:
  - `__pycache__/` and `*.pyc`
  - Virtual environment folders (`.venv/`, `venv/`, `env/`)
  - `.env` files (for secrets/API keys)
  - Database files (`*.db`, `*.sqlite`)
  - OS/editor files (`.DS_Store`, `.vscode/`)
  - Data/output files that shouldn't be tracked (e.g., `data/raw/`)

### 3. Make the Initial Commit
- Stage all current files (`PLANNING.md`, `plans/`, `.gitignore`)
- Commit with a message like `"Initial commit"`

### 4. Create a Remote Repository on GitHub
- Go to [github.com](https://github.com) and create a new repository named `fantasy_football_projects`
- Keep it empty (no README, no license) so it doesn't conflict with the local init
- Note the remote URL (e.g., `https://github.com/<username>/fantasy_football_projects.git`)

### 5. Connect Local Repo to Remote
- Add the remote origin:
  ```
  git remote add origin https://github.com/<username>/fantasy_football_projects.git
  ```
- Verify with:
  ```
  git remote -v
  ```

### 6. Push to GitHub
- Set the upstream branch and push:
  ```
  git push -u origin main
  ```
- If the default branch is `master`, rename it first:
  ```
  git branch -M main
  git push -u origin main
  ```

### 7. Verify
- Confirm the repository appears on GitHub with all files
- Confirm `git status` shows a clean working tree locally

---

## Notes
- The `nba_projects` repo uses `https://github.com/salaamout/nba_projects.git` as its remote — use the same GitHub account or the appropriate account for this project
- Consider adding branch protection rules on `main` in GitHub settings if collaborating
