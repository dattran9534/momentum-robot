# Momentum watchlist robot

Every weeknight evening (Sun–Thu, Pacific time), this robot finds Kev's newest "TOP 3 STOCKS … TRADING DAY" reel on YouTube Shorts or TikTok. It reads the tickers from the video and transcribes what he says. The results go in `data/latest.json`, along with a snapshot of the frame where each ticker appears. Your Claude scheduled task reads that file.

## Setup (about 5 minutes, all in a web browser)

1. Sign in at github.com (or create a free account).
2. Create a new repository named **momentum-robot**. Set it to **Public** so Claude can read the results, and check "Add a README file".
3. In the repo, click **Add file → Upload files**. Drag in everything from this zip: `robot.py`, the `data` folder and the `.github` folder. Then click **Commit changes**.
   - The `.github` folder is hidden on Mac and Windows. If you can't drag it, click **Add file → Create new file** instead, name it `.github/workflows/watchlist.yml`, and paste in the contents of that file.
4. Open the **Actions** tab. If GitHub asks, click "I understand my workflows, go ahead and enable them".
5. Click **Momentum watchlist robot → Run workflow** to test it. After 2–4 minutes, open `data/latest.json` to see the result.

After that it runs by itself at about 5:45 PM and 8:45 PM Pacific (an hour later during daylight saving time).

## Results file

- `status: ok` means 3 tickers were found.
- `status: partial` means fewer than 3 were found.
- `status: pending` means no matching reel was found yet.
- `status: failed` means something went wrong; `reason` says what.

`transcript` and `plan` hold what Kev said about each ticker: entries, levels and targets.

## Notes

- YouTube and TikTok sometimes block automated downloads from GitHub's servers. On those nights the status shows `failed`, and the Claude run marks the watchlist pending.
- GitHub can start scheduled runs 10–30 minutes late.
- GitHub pauses scheduled workflows after 60 days with no repository activity. The robot commits a file each night, which counts as activity.
