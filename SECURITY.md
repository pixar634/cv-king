# Security policy

## Reporting a problem

**Please don't open a public issue for a security problem.** Report it privately instead:

1. Open the repository's **Security** tab.
2. Press **Report a vulnerability**.
3. Describe what you found, how to reproduce it, and what someone could do with it.

Only the maintainers can see the report. You'll get a first answer within 7 days. Once a fix is out, you'll be credited in
the advisory unless you'd rather not be.

## What's in scope

JobMagnet runs on your own computer and keeps your data there. These matter most:

- **Your data leaving your computer** in a way the README doesn't describe. The only things that should leave are the
  text sent to the writer you chose (Claude or Gemini), the job pages and job boards you scan, and the web searches
  that research features run.
- **A job posting that takes control of the writer.** Job descriptions are untrusted text. A posting that makes the
  writer invent experience, break out of its brief, or use a tool it shouldn't is a security bug.
- **Gmail drafts.** Cold mail may only create drafts, never send, read or delete mail. Anything that gets around that
  is a security bug.
- **The local server** (`127.0.0.1:8765`): another website or another user on the machine reading or changing your
  profile through it.
- **Paths:** any request that reads or writes files outside the app's own folders.

Out of scope: the terms of service of job sites (see the README's notes on scraping), and problems inside the
Claude or Gemini CLIs themselves (report those to Anthropic or Google).

## Supported versions

Only the latest `main` gets fixes. Update with `git pull` before reporting.
