# CV Tailor

A local app that rewrites your CV for each job description, renders it in your design, checks it
the way an ATS reads it, writes the cover letter, finds fresh jobs, and tracks every application.

**Start it:** double-click `start.bat`. It opens http://127.0.0.1:8765. Keep its window open while you
work; closing it stops the app. Everything runs on this laptop: your CV, the JDs and the tracker never
leave it, except the brief sent to the AI you choose.

## The three screens

**Tailor.** One box: paste a job link (press Enter) or the full description (Ctrl+Enter). `/` focuses it.
- A link is read for you: company, role, location, job ID, posted date and the description, straight from
  the ATS's API where the site uses Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee or
  Personio (about a second), otherwise from the page. LinkedIn and Naukri links are refused; paste the text.
- **Tailor** writes the CV (1 to 3 minutes). Market and career track are picked from the job's location and
  title unless you change them on the card.
- Then **What changed** shows the headline, summary and every bullet against your original; *Changes* above
  the preview highlights them on the CV (hover a line to see the original).
- Switch **design** (FlowCV serif, Engineer, Modern sans, Classic) or **length** (1 or 2 pages) above the
  preview: it re-renders in seconds from the same content, with no AI run. *Edit* lets you change words by hand.
- **Gaps**: requirements your profile doesn't show. Tick only the true ones; they're added and it re-tailors.
- **Cover letter**: a first draft with every CV, plus five voices to try (Plainspoken, Storyteller, Formal,
  Warm, Proof points). Drafts stay in this browser; *Keep* saves one next to the PDF.
- **Download** saves `Company_Position_Soumava_Paul.pdf`. **Mark applied** moves it to Applied.

**Discover.** *Scan sources* reads every source in parallel (under a minute for career pages), shows only
postings inside the window (default 14 days), and ranks them against your profile. Lead-level and above
only (Senior titles can be switched on), and jobs that need Java, .NET or C# are skipped (edit the list in
Settings). Tick the jobs you like and press **Tailor N jobs**: they're written in parallel, 2 at a time.

**Pipeline.** Tailoring (live progress for each job), Tailored, Applied and Interviews, with closed ones folded
away. Change a status from its card; click a card to open it. *Export to Excel* writes the whole list.

**Career.** Your history as a map (built from your profile), and five or six routes for the next five years,
researched on the web when you press *Map my routes* (2 to 5 minutes): Forward Deployed / Applied AI, Engineering
Manager, Product, Staff IC, Solutions, early-stage. Click any step for what employers screen for, what you already
have, what to build, certifications, projects to take on, the pay range with its source (no source, no number),
and jobs for it from your scans or a live web search. Set a **goal** (e.g. VP level by a year, with a target pay):
routes that reach it point at it. Log your **salary** by year: code draws your growth, where your pace leads, and the
yearly growth your goal needs. The **growth log** takes what you've done, are doing, learned or want; the next map
reads all of it, and skills you mark for CVs join your profile.

**Claude / Gemini** (top right) picks the writer for everything: CVs, letters, link reading, career maps. Web
searches always run on Claude. Gemini needs a one-time `gemini` sign-in in a terminal.

**Settings** (the gear): defaults for every new CV, how many jobs tailor at once, your availability line,
regions and areas, what Discover hides (Senior titles, years asked, skills such as Java/.NET), company offices,
job sources (careers pages, job-board searches, web searches), People to watch, and confirmed skills.

## What it will never do

- **Invent anything.** Every CV and letter is built only from `data/base_cv.json`. Code checks every
  number and every skill in the output against it and flags anything new in red. Titles, companies and
  dates are printed by code, not written by the AI.
- **Overstate your title.** Headlines always say Lead (your current title) and never claim Manager, Staff,
  Architect or Founder. The career track changes what the CV leads with, not what it claims.
- **Mention a weakness** in a cover letter. Gaps are for your interview prep.

## The numbers

- **Keyword match** is computed by code (same JD and CV, same score), from `knowledge/skills.yaml`.
  *Before* is your original FlowCV PDF; *after* is the tailored PDF read back as an ATS would;
  *ceiling* is the most your profile can truthfully reach. A low ceiling means the job asks for
  things your profile doesn't show; tailoring can't fix that, only true additions can.
- **Total experience** (7+ years) is computed from your employment dates, internship excluded.

## Writers

Pick in the top bar. Both use the CLIs signed in on this laptop, so a run costs your subscription
usage, not an API bill.
- **Claude Opus** (default; best writing) or **Claude Sonnet** (faster).
- **Gemini**: run `gemini` once in a terminal and choose *Sign in with Google* with your subscription
  account, then it's available here.

## Files

| Path | What |
|---|---|
| `data/base_cv.json` | Your master profile: the only source of facts. Edit it to add true things. |
| `data/sources.json` | Your job sources and people list |
| `knowledge/playbook.md` | The writing brief: how ATS and recruiters read a CV, and the rules |
| `knowledge/markets.yaml` | India, UK, EU, UAE, Singapore, Australia/NZ, US/Canada, Remote |
| `knowledge/tracks.yaml` | IC Lead, Engineering Manager, Product Manager, Delivery Lead, Staff |
| `knowledge/voices.yaml` | The five cover-letter voices and the banned clichés |
| `output/<date>_<company>_<role>/` | Per application: the PDF, cover letter, JD, report, logo |
| `output/applications.json` | The tracker |
