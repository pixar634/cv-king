<!--
Title: [TYPE]: Subject, e.g. "[BUG]: Stop losing saved letters" (a check enforces it; see CONTRIBUTING.md).
The PR is squash-merged, so the title becomes the commit message on main.
-->

## What and why

<!-- One or two sentences: what changes for someone using the app, and why. -->

Fixes #

## How I tested it

<!-- What you ran or clicked. Which writer (Claude or Gemini) if the change touches one. -->

## Screenshots

<!-- For anything visible in the UI: before and after. Use a demo profile, never your own CV. -->

## Checklist

- [ ] `ruff check .` and `pytest` pass
- [ ] No personal data in the code, tests, screenshots or commits (names, CVs, emails, employers)
- [ ] The README says the right thing if behaviour changed
- [ ] New UI matches the existing design tokens (`app/static/css/tokens.css`)
