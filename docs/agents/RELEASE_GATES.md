# Verification and Release Gates

Run gates in this order and stop on the first non-zero exit code:

1. `git diff --check` and `git diff --cached --check`.
2. Python compile/import contract checks for changed modules.
3. Alembic head/history checks when persistence changed.
4. New and targeted regression tests.
5. Full backend regression.
6. Frontend TypeScript/Vite production build when the release script requires
   it or frontend/contracts changed.
7. Runtime image build/preflight for isolated runtime changes.
8. Standards review, Spec review, and Simplification review.

The handoff must distinguish verified, skipped, unavailable, and inferred
checks. Record exact pass/skip counts, warnings, exit codes, Alembic head, branch,
and `HEAD` SHA. Never claim a check passed from an earlier commit or another
working tree.

After all gates pass, show the exact `git add` manifest and expected diff. Do
not commit, push, merge, tag, reset, or force-update history until Human Control
explicitly approves that separate action.
