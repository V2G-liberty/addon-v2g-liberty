# Contributing

## Branch strategy

| Branch | Purpose |
| ------ | ------- |
| `main` | Production. Home Assistant pulls the add-on from here. Bumping `config.yaml` version flags "has update" in HA. |
| `dev`  | Default integration branch. **All feature/fix PRs target `dev`.** Releases happen as a separate `dev` → `main` PR. |
| `<issue-number>-<slug>` | Short-lived feature/fix branches, branched from `dev`, merged back to `dev`. Deleted after merge. |
| `development-environment` | **Long-running** branch for devcontainer / docker-compose / scripts / `.vscode` / tooling tweaks. See below. |

### `development-environment` branch

This branch exists because devenv changes are a continuous trickle of small unrelated tweaks (HA version bumps, devcontainer extensions, helper scripts, gitignore entries, …) that don't fit on any feature branch and don't deserve a fresh branch each time.

**Rules:**

- **Never deleted.** It is a permanent branch.
- **Branched from `dev`** the first time, and kept in sync afterwards.
- **Commit devenv tweaks here directly**, also while you are mid-flight on a feature branch:
  ```bash
  git stash
  git checkout development-environment
  git pull
  git stash pop
  # commit only the devenv files
  git checkout <feature-branch>
  ```
- **PR to `dev`** whenever something stable is ready to share. No fixed cadence — small PRs are fine, batching 2–3 related tweaks is also fine.
- **After merge into `dev`**: do NOT delete the branch. Instead, merge the latest `dev` back into it so it stays in sync:
  ```bash
  git checkout development-environment
  git pull origin dev
  git push
  ```
- Conflicts that arise because `dev` also touched a devcontainer file are resolved during that back-merge.

**What does NOT belong on this branch:**

- Application code (`rootfs/root/appdaemon/...`).
- Home Assistant package YAML or dashboards.
- Anything that needs a CHANGELOG entry — devenv tweaks generally don't.

If you find yourself wanting to mix application changes with devenv changes, split them: devenv part on `development-environment`, application part on a fresh feature branch.

## Pull requests

- Always target `dev`, never `main`.
- Keep PRs focused; one logical change per PR.
- Reference the issue number in the title (e.g. `BUG: ... (#123)`) so the changelog entry is easy to write.
- Add a changelog entry to both `CHANGELOG.md` and `changelog_of_all_releases.md` under the current development version, in the right section (Fixed / Added / Changed). Devenv-only PRs from `development-environment` are exempt.

## Commit messages

- British English spelling everywhere (initialise, colour, optimise, behaviour).
- No `Co-Authored-By: Claude` trailer.
- Imperative mood, concise subject line, body for the why.
