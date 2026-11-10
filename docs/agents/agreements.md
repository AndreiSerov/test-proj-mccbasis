# Working agreements

- Work through approved steps without asking for approval at each one. Ask first for any significant decision the spec doesn't cover, such as swapping a library or changing a protocol.
- Build test first, and have the `reviewer` subagent check each step.
- Make small, focused commits on one branch per milestone. The user merges.
- Test logic. Mark anything needing real hardware, permissions or accounts as `manual`, and report it as not run.
- Keep the README current with setup and how to run things.
- Commit messages end at the body, without a `Co-Authored-By` line. `.claude/settings.json` turns off the generated attribution.
- Turn a rule into a check when you can: a failing `scripts/check` beats a sentence in this file.
- A `# noqa` states its reason on the same or the previous line.

## Private data

Secrets stay on this machine and out of git. The deny rules in `.claude/settings.json` guide agents but aren't a security boundary for shell commands, so don't work around them. Anything that sends this data to a hosted service must be selected explicitly.
