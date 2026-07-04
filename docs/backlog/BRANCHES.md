# Branches

The list of **open development branches** — in-flight feature work that has not yet
landed on `main`. A feature is **finished only once it is merged to `main`**; until
then its branch lives here so nothing in-flight is lost across sessions.

This directory (`docs/backlog/`) is tracked in git and published in the doc site under
"Roadmap"; the priority tiers are [TODO.md](TODO.md) / [DEFER.md](DEFER.md) /
[FUTURE.md](FUTURE.md) / [DONE.md](DONE.md).

## Convention

- **Branch naming:** `dev/<domain>/<feature>` — `<domain>` is the area it touches
  (`engine`, `cli`, `compose`, `docs`, ...); `<feature>` is a short kebab-case slug.
- **Base off** `main` for independent work, or off another `dev/...` branch when the
  work builds on an unmerged feature (record the base in the table).
- **Add a row** the moment you cut a branch; **remove it** once the branch is merged
  to `main` (record the merge/PR in [DONE.md](DONE.md) with the landing hash, per the
  Zeroth rule).

## Open branches

| Branch | Base | Purpose | Opened |
|--------|------|---------|--------|

