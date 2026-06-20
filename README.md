# Watchboard

Checks websites on a schedule and tells you when something you're waiting for
becomes available — e.g. a swimming lesson spot opening up. Runs for free on
GitHub. No server, no app to maintain.

You already have one watch set up as an example: **Morley Swimming, Stage 1,
ages 5-14**, checking the Active Leeds booking site.

## Setup (about 10 minutes, one time only)

### 1. Create a free GitHub account
Go to [github.com/join](https://github.com/join) if you don't already have one.

### 2. Create a new repository
- Go to [github.com/new](https://github.com/new)
- Name it `website-watcher` (or anything you like)
- Set it to **Private** if you'd rather your watches weren't public, or
  **Public** is fine too — there's nothing sensitive in here.
- Click **Create repository**

### 3. Upload these files
- On your new repository's page, click **uploading an existing file**
- Drag the entire contents of this folder in (all files and folders, keeping
  their structure: `.github`, `docs`, `config.json`, `requirements.txt`, `scraper.py`)
- Click **Commit changes**

### 4. Turn on the dashboard (GitHub Pages)
- Go to your repo's **Settings** tab → **Pages** (left sidebar)
- Under "Build and deployment", set **Source** to "Deploy from a branch"
- Set **Branch** to `main` and folder to `/docs`, then **Save**
- Wait about a minute, then refresh — GitHub will show you a link like
  `https://yourusername.github.io/website-watcher/`. That's your dashboard.
  Bookmark it.

### 5. Create an access token (lets the dashboard add/edit watches)
- Go to [github.com/settings/tokens?type=beta](https://github.com/settings/tokens?type=beta)
- Click **Generate new token**
- Give it a name like "watchboard"
- Under **Repository access**, choose "Only select repositories" and pick your
  `website-watcher` repo
- Under **Permissions**, find **Contents** and set it to **Read and write**.
  Also set **Actions** to **Read and write** (this lets the "Check now" button work).
- Click **Generate token**, then **copy it** (you won't see it again)

### 6. Connect the dashboard
- Open your dashboard link from step 4
- Click **Settings**
- Enter your GitHub username, repository name, and paste in the token
- Click **Save settings**

### 7. Run your first check
- Click **Check now** on the dashboard
- Wait a minute or two, then refresh the page — you'll see the status of the
  Morley swimming watch

That's it. From now on it checks automatically on the schedule you set
(default: daily at 7am UTC), and you just open the dashboard link whenever
you want to see results.

## Adding more watches

Click **+ Add a watch** on the dashboard and fill in:
- **Name** — whatever you'll recognize it by
- **Website to check** — the page where availability would show up
- **Must all appear together** — words that pin down the exact thing you
  want (e.g. a branch name + a class name + an age group)
- **Words that mean "available" / "full"** — optional, sensible defaults are
  already filled in (it looks for things like "book now" vs "full" / "waiting list")

## A note on accuracy

This works by reading the visible text of a page and looking for your
keywords near words like "available" or "full" — it doesn't understand
booking systems individually. For most sites this works well out of the box.
For some (especially ones that require you to click through several filters
before showing anything), it may need a bit of trial and error: open the
"What was found on the page" details on a watch's card to see exactly what
text it matched, and adjust your keywords accordingly. Come back and tell me
what you see and I can help tune it further.

## Changing the schedule
In **Settings**, pick Daily / Twice a day / Weekly from "Check frequency" and
save — this rewrites the schedule for you, no file editing needed.
