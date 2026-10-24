# Contributing to JobMagnet

Thanks for wanting to help. Bug reports, fixes, features, docs and tests are all welcome, and you don't need to have
contributed to open source before. This page walks you through it.

- [Ways to help](#ways-to-help)
- [The rules that keep JobMagnet trustworthy](#the-rules-that-keep-jobmagnet-trustworthy)
- [Set up the project](#set-up-the-project)
- [Make a change and open a pull request](#make-a-change-and-open-a-pull-request)
- [How pull requests are named and merged](#how-pull-requests-are-named-and-merged)
- [Code style](#code-style)

## Ways to help

- **Report a bug** with the [bug form](../../issues/new/choose). Remove personal details first.
- **Suggest a feature** with the [feature form](../../issues/new/choose).
- **Ask a question** in [Discussions](../../discussions).
- **Fix something.** Issues labelled [`good first issue`](../../issues?q=is%3Aopen+label%3A%22good+first+issue%22) are
  small and well explained. Comment on one to say you're taking it, so two people don't build the same thing.

For anything bigger than a small fix, open an issue first and agree on the approach. It saves you from building
something that can't be merged.

## The rules that keep JobMagnet trustworthy

These come before any feature. A pull request that breaks one will be asked to change.

1. **Never invent experience.** Every fact, number and skill in a CV, letter or email comes from the person's own
   profile. Model output is checked against it, and new features must keep that check.
2. **Personal data stays on the computer.** Nothing is uploaded except the text sent to the writer the person chose,
   and the searches and pages they asked for. No analytics, no telemetry, no accounts.
3. **Nothing is sent on someone's behalf.** Cold mail creates Gmail drafts and never sends. Applications are never
   submitted automatically.
4. **Respect the sites we read.** New job sources use public APIs, schema.org data or pages that allow it in
   `robots.txt`. Don't add scrapers for sites whose terms forbid automated access.
5. **No personal data in the repository.** Never commit a real CV, profile, application or screenshot of one. Use a
   made-up person. `data/`, `output/`, `profiles/` and `interview/` are in `.gitignore` for this reason.

## Set up the project

You need **Python 3.12**, **Git** and **Google Chrome** (the app prints PDFs with it). To use the AI features you also
need the Claude or Gemini CLI: see [Choose your AI writer](README.md#-choose-your-ai-writer) in the README. The tests
don't need either.

1. **Fork** the repository: press **Fork** at the top right of the GitHub page. This makes your own copy.
2. **Clone your fork** and add the original as `upstream`, so you can pull in new changes later:

   ```bash
   git clone https://github.com/<your-username>/jobmagnet.git
   cd jobmagnet
   git remote add upstream https://github.com/pixar634/jobmagnet.git
   ```

3. **Create a virtual environment and install** the app plus the test tools:

   ```bash
   python -m venv .venv
   # Windows:          .venv\Scripts\activate
   # macOS and Linux:  source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```

4. **Run it** with auto-reload, then open http://127.0.0.1:8765:

   ```bash
   python -m uvicorn app.main:app --reload --port 8765
   ```

   On Windows, `start.bat` does steps 3 and 4 for you.

5. **Run the checks.** CI runs the same two commands on every pull request:

   ```bash
   ruff check .
   pytest
   ```

JobMagnet is developed on Windows. macOS and Linux should work. If something doesn't, that's a bug worth reporting.

## Make a change and open a pull request

1. **Start from an up-to-date `main`:**

   ```bash
   git checkout main
   git pull upstream main
   ```

2. **Make a branch** named after the change: `fix/letter-versions`, `feature/dark-theme`, `docs/setup-on-mac`.

   ```bash
   git checkout -b fix/letter-versions
   ```

3. **Make the change.** Keep it to one thing. Add or update a test in `tests/` when you change behaviour.
4. **Commit and push** to your fork. Your own commit messages can be anything readable; the PR title is what counts.

   ```bash
   git add <the files you changed>
   git commit -m "Keep every letter version"
   git push -u origin fix/letter-versions
   ```

5. **Open the pull request.** GitHub shows a **Compare & pull request** button on your fork. Fill in the template:
   what changed and why, how you tested it, and screenshots for anything visible.
6. **Review.** CI runs automatically, and a maintainer reviews. Push more commits to the same branch to answer
   comments; the PR updates itself. Once it's approved and green, it gets merged.

## How pull requests are named and merged

Every pull request is **squash-merged**: all its commits become one commit on `main`, and **the PR title becomes that
commit's message**. So the title follows one format, and a check enforces it:

```
[TYPE]: Subject
```

- `TYPE` is one of the types below, in capitals, inside square brackets.
- `Subject` is short and imperative (*Add*, *Fix*, *Stop*, *Keep*), starting with a capital letter.
- GitHub adds the PR number when it merges, so `main` reads like `[BUG]: Stop losing saved letters (#42)`.

| Type | For |
|---|---|
| `FEATURE` | Something new a user can do |
| `BUG` | A fix. Say what was broken: *Stop losing saved letters*, not *Update letters.py* |
| `DESIGN` | How something looks or reads, with no change in behaviour |
| `REFACTOR` | Restructuring with no change in behaviour |
| `PERF` | Faster or lighter |
| `SECURITY` | A security fix or hardening |
| `DATA` | Knowledge files: `knowledge/*.yaml`, `knowledge/playbook.md` |
| `TEST` | Tests only |
| `DOCS` | README, this guide and other docs |
| `INFRA` | CI and GitHub settings |
| `CHORE` | Dependencies, tooling, housekeeping |
| `REVERT` | Undoing an earlier change. Name it in the description |

Good: `[FEATURE]: Export the pipeline as CSV`, `[BUG]: Show Lead roles in Discover`.
Not accepted: `fixed stuff`, `[bug] letters`, `FEATURE: add csv`.

Commit messages on `main` never contain emojis or tool-generated footers (such as "Generated with ..." or
`Co-Authored-By` bot lines).

## Code style

- **Match the code around you**: its naming, its comment style and its level of detail. Comments say *why*, not what.
- **Python 3.12**, standard library first. Discuss a new dependency in an issue before adding it.
- **The front end has no build step**: plain JavaScript in `app/static/`, CSS built on the tokens in
  `app/static/css/tokens.css`. Don't hard-code colours; use the tokens.
- **UI copy is plain and specific.** Say what happened and what to do next.
- **Keep prompts honest.** A change to a writing brief must keep the rule that nothing is invented.

## License

By contributing, you agree that your contributions are licensed under the project's [MIT License](LICENSE).

## Code of Conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). Be kind, and assume good intent.
