# Job Radar

Watches employer career sites directly and alerts you when a matching role goes live. It runs free on GitHub Actions, needs no servers or Python packages, and publishes a dashboard to GitHub Pages.

It currently watches about 50 career sites and 2 community job lists for entry-level cybersecurity and fraud roles in metro Atlanta or remote.

The community lists are [HackedRico/2027-cyber-jobs](https://github.com/HackedRico/2027-cyber-jobs) and [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions). They reach employers this tracker doesn't read directly, and act as a backup if a site blocks it. HackedRico's list updates about twice a day, so the employer sites are what catch a new role fastest.

## Set it up

1. Put this folder in a new **public** GitHub repository. Public repos get free Actions minutes and Pages. Your alert link stays private as a secret.

   ```bash
   git init -b main && git add . && git commit -m "Set up Job Radar"
   gh repo create job-radar --public --source . --push
   ```

2. In Discord, open a channel's **Edit Channel > Integrations > Webhooks**, create a webhook, and copy its URL.
3. In the repo, open **Settings > Secrets and variables > Actions** and add a secret named `DISCORD_WEBHOOK_URL` with that URL.
4. Open **Settings > Pages** and set **Source** to **GitHub Actions**.
5. Open **Actions > Job Radar > Run workflow** and run these modes in order:
   - `doctor` shows which sites can be read.
   - `test-alert` sends a test message.
   - `run` records what's open today, without alerting on it.

After that it checks every 30 minutes. GitHub can delay scheduled runs, especially when it's busy.

Your dashboard will be at `https://<your-username>.github.io/job-radar/`.

**Phone alerts without Discord:** install the ntfy app, subscribe to a long random topic name, and add it as a secret named `NTFY_TOPIC`. Anyone who knows the topic can read it, so make it hard to guess.

## How jobs are chosen

| Check | Result |
| --- | --- |
| Title isn't a target role, is senior, or is an internship | Hidden |
| Outside metro Atlanta and not remote | Hidden |
| Job description asks for 5+ years | Hidden |
| Title says II, or the description asks for 3–4 years or an active clearance | **Stretch** |
| Everything else | **Good fit** |

Experience comes from the lowest figure in the description, so "2 years with a degree, or 6 without" counts as 2. The tracker would rather show an extra job than hide one you could apply to.

Roles with an unclear location stay visible and are marked `check location`.

## Change what it watches

- **Roles, locations, and experience limits:** edit `profile.toml`.
- **Companies:** paste a Workday, Oracle, or Avature careers link into `companies.toml`. New companies are recorded silently on their first read, so they don't flood you.
- **How often it checks:** edit `cron` in `.github/workflows/radar.yml`.

Test a filter change without running a full check:

```bash
python -m radar check "Cybersecurity Analyst II" "Atlanta, GA"
```

Requires Python 3.11 or newer.

## When something breaks

Career sites can change without notice, or block automated requests. If a site fails 6 checks in a row, you get one warning, and another when it recovers. A failed, empty, or partial read never marks a job as closed. If an alert can't be sent, it retries on the next check.

## Run the tests

```bash
python -m unittest discover -s tests
```

## Good to know

- **Polite by design:** each site gets one request at a time with a pause in between, and job pages are only opened for new matches.
- **Coverage limits:** sites listed under `[[manual]]` in `companies.toml` use systems the tracker can't read yet, so the dashboard links to them instead. That includes Delta, whose site returns a short page with no job links when the tracker requests it.
- **Applied marks:** these are saved in your browser, so they don't sync between devices.
