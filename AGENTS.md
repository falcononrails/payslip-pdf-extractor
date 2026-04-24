# Git Naming Conventions

- Use commit prefixes in this form: `[REFACTO]`, `[FEATURE]`, `[BUGFIX]`, `[CHORE]`.
- Capitalize the prefix and write commit subjects like `[BUGFIX] Fix flaky payments links e2e`.
- Use branch names with the matching lowercase prefix, such as `refacto/branch-name`, `feature/branch-name`, `bugfix/branch-name`, or `chore/branch-name`.

# Git Push Policy

- Do not force-push by default.
- Use normal commits for review fixes unless the user explicitly asks to rewrite history.
- Only force-push when the user explicitly requests it, or when repairing broken history/signatures where rewriting is clearly necessary.
- If force-push seems justified, explain why before doing it.