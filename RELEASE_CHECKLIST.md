# Release checklist

Steps to complete before making this repository public.

## 1. Run the anonymity check

```bash
bash scripts/check_pii.sh
```

Must exit 0. If it exits non-zero:

| Finding | Action |
|---|---|
| Real name in a tracked file | `git rm --cached <file>`, rewrite with `git filter-repo --path <file> --invert-paths` if already committed |
| Phone number in a tracked file | Same — `git filter-repo` to excise the commit; do not force-push without understanding the blast radius |
| `sample-archive/` not gitignored | Add `sample-archive/` to `.gitignore`, verify with `git check-ignore -v sample-archive/` |
| Name found in git log (author/committer) | Configure git identity: `git config user.name` / `git config user.email` — use a handle, not a real name |

**Do not auto-rewrite history.** The script reports only. You decide whether to rewrite, reset `main`, or make the release private. Forced history rewrites on shared branches destroy collaborator clones.

## 2. Verify test suite

```bash
pytest
```

Must exit 0 with no failures or errors.

## 3. Check gitignore coverage

```bash
git check-ignore -v sample-archive/
git status --short
```

- `sample-archive/` must appear in the `git check-ignore` output.
- `git status` must not list any `sample-archive/` file as staged or untracked (they should be invisible).
- `*.db` files (e.g. `articles.db`) must also be absent from `git status`.

## 4. Verify no derivative artifacts are tracked

```bash
git ls-files | grep -E '\.(db|json|csv|pkl|npy|parquet)$'
```

Must return nothing. These extensions can carry scraped or enriched content derived from private chats.

## 5. Scan commit history one more time

```bash
git log --format='%H %s' | head -20
git log -p | grep -i dylan | head -20
```

The anonymity script checks commit metadata but not diff content. Run `git log -p` grep as a spot-check if you added any commits that touched message text.

## 6. Final manual review

Read through `README.md`. Confirm:
- No sample chat transcript excerpts with real names/numbers embedded.
- Installation and usage examples use synthetic data or placeholders.

---

After all steps pass, the repository is safe to make public.
