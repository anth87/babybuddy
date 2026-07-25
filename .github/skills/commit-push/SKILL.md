---
name: commit-push
description: "Use when: committing and pushing outstanding repository changes, especially when Git hooks, Lefthook, Gulp, Pipenv, formatting, or lint failures must be diagnosed and handled safely."
---

# Commit And Push

Use this workflow when the user asks to commit, push, or commit and push changes.

## Inspect Before Committing

1. Work from the repository root.
2. Run `git status --short`, `git branch --show-current`, `git diff --stat`, and `git diff --cached --stat`.
3. Inspect the actual staged diff with `git diff --cached`. If nothing is staged, inspect the unstaged diff and stage only files that are clearly within the user's request.
4. Keep unrelated untracked artifacts, including development-server logs, out of the commit. Do not use `git add -A` or `git add .` unless the user explicitly requests every untracked file.
5. Run `git diff --cached --check` before committing. Confirm the push remote and branch with `git remote -v`.

## Validate Hooks

1. Read `lefthook.yml` and any configured hook path before committing.
2. Run the hook command directly when practical. Capture its exact failure, rather than assuming why it failed.
3. Do not bypass a hook for a real lint, test, format, or build failure. Repair the issue and rerun the relevant check.

### Baby Buddy Gulp And Pipenv Fallback

In this repository, the pre-commit hook runs `gulp lint`. That task calls `pipenv run black`, `pipenv run djlint`, Prettier, and Stylelint.

If `gulp lint` fails specifically because `pipenv` is unavailable or not on `PATH`:

1. Verify that the repository virtual environment exists, such as `.venv\\Scripts\\` on Windows.
2. For staged Django template changes, run the focused equivalents:

   ```powershell
   .\.venv\Scripts\djlint.exe --check <staged-template-paths>
   npx prettier <staged-template-paths> --check
   ```

3. For Python, Sass, JavaScript, or broad changes, do not claim the template-only fallback covers the hook. Restore `pipenv`, or run every applicable lint task from `gulp lint` using the project environment.
4. Only after applicable equivalent checks pass, and only when the user has asked to proceed, use `git commit --no-verify`. State clearly that the bypass was caused by missing `pipenv` and record the passing replacement checks.

## Commit And Push

1. Use a concise imperative commit subject that describes the staged changes.
2. Run `git commit` normally unless the narrow missing-Pipenv exception above applies.
3. Push explicitly with `git push <remote> <branch>`.
4. Verify the result with `git status --short` and `git log -1 --oneline`.
5. Report the commit hash, destination branch, validation performed, any hook issue, and any intentionally uncommitted files.

## Guardrails

- Never force-push, amend, reset, or discard changes unless the user explicitly requests it.
- Never include credentials, generated logs, or unrelated local work by accident.
- Do not install or modify tooling solely to get a hook past an environment issue unless the user asks to repair the environment.
