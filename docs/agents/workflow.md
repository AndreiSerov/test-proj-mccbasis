# Workflow

Features follow Matt Pocock's skills flow. `/ask-matt` routes anything that doesn't fit, such as upkeep; hard bugs start at `diagnosing-bugs`.

The user starts each `/`-prefixed step, because the agent can't invoke those skills.

1. **Grill**: `/grill-with-docs` asks about every decision until none is left open. New terms go in `GLOSSARY.md`, and lasting decisions in `docs/adr/`.
2. **Spec**: `/to-spec` turns the grilling into a spec, and the user confirms its test seams.
3. **Tickets**: `/to-tickets` splits a multi-session build into tracer-bullet tickets with blocking edges. Work that fits one session goes straight to step 4.
4. **Build**: `/implement` runs per ticket, with a cleared context between tickets, or `/implement-spec` runs the whole spec on one integration branch. Implementers drive `tdd`, one red-green slice at a time.
5. **Review**: `code-review` checks the diff against the standards and the spec, and every finding is fixed or reported.
6. **Ship**: push the feature branch and open a PR with `pr`. The user merges.
7. **Retro**: `/retro` runs before the session is cleared. Mark each finding `project` or `harness`. Apply `project` findings here, and send `harness` findings as a change to the template named in `.copier-answers.yml`, so other repositories get them through `copier update`.

Keep steps 1-3 in one unbroken context, so the spec and tickets build on the grilling.

## Who does what

- The main session on the strongest model grills, specs, reviews and verifies the result.
- The `implementer` subagent builds on Sonnet, and the `reviewer` subagent reviews each step independently.
- Work from any model passes the same `scripts/check` and review. A ticket that fails review goes back to its implementer, at most three times. After the third failed review, stop and report what keeps failing to the user, because the ticket or the environment may be wrong.

## Harness upkeep

- `scripts/sync-skills` installs the pinned skills after a clone. `scripts/sync-skills --update` pulls newer ones; review the printed changes before committing the moved pin.
- The `skill-scout` subagent proposes external skills for this project's stack and domain, and installs nothing. To accept one, read the proposal, add its entry to `.claude/skills.toml`, run `scripts/sync-skills`, then read the installed skill before committing the pin.
- `copier update` brings in template changes for the files the template owns.
- `scripts/doctor` reports anything missing or out of date.
