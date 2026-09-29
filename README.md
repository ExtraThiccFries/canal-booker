# Canal Booker

Canal Booker books Canal Building rooms on the Carleton booking portal (booking.carleton.ca) for a small group. Each person runs it on their own computer under their own Carleton account. It books each person's slot the moment it opens, so the group can cover a longer block than one person's 3 hour daily limit.

Passwords never leave each person's computer. They are kept in Windows Credential Manager or the Mac Keychain.

## For friends: getting started

1. Go to the **Releases** page of this GitHub repo and download the file for your computer.
   * Windows: `CanalBooker-Windows.zip`. Unzip it and double click `CanalBooker.exe`. If Windows shows "Windows protected your PC", click **More info** and then **Run anyway**.
   * Mac: `CanalBooker-Mac.zip`. Unzip it, move `CanalBooker` into Applications, then right click it and choose **Open**. You only need to right click the first time.
2. A page opens in your browser. Fill in:
   * **Your Carleton sign-in.** Use your MyCarletonOne username (the part before @carleton.ca) and password. Click **Test sign-in** to check it.
   * **Rooms.** Add rooms in order of preference. You can also copy them from the team plan.
   * **Time slots.** Add the day and time you were assigned, or click **Use my slots from it** in the Team plan box.
3. Click **Save changes**.
4. Open **Settings** and tick **Start Canal Booker when this computer starts**.

That's it. You can close the page. The booker keeps running in the background. Open the app again anytime to see what it booked.

You will need Google Chrome or Microsoft Edge installed. Every Windows computer already has Edge.

### Keep in mind

* Your computer needs to be on and awake when slots open (12:00 am). If it was asleep, the app catches up when it wakes, but someone else may have taken the room by then. Setting your computer to not sleep while plugged in helps.
* The library style check-in rules do not apply here, but please cancel any booking you will not use through the portal. Unused rooms block other students, and the portal can see who booked them.

## For the organizer

### One time setup

1. Create a private GitHub repo and upload everything in this folder.
2. Edit `schedule.json` on GitHub. Put in the real room names, each person's MyCarletonOne username, and their slots. Each person gets at most one slot per day, 3 hours or less.
3. Open `app_defaults.json` and set `team_plan_url` to the link of `schedule.json` in your repo, for example `https://github.com/yourname/canal-booker/blob/main/schedule.json`. Everyone's app will then find the plan on its own. If the repo is private, this link only works for people who are signed in to GitHub in that browser, so a public repo is easier. The file contains no passwords.
4. To publish downloads, go to **Releases**, click **Draft a new release**, create a tag like `v1.0`, and publish. GitHub builds the Windows and Mac downloads for you in a few minutes and attaches them to the release.

### Changing the schedule later

Edit `schedule.json` on GitHub. Each person then clicks **Use my slots from it** in their app. The Team plan box always shows the current plan.

### Confirming the booking steps (do this first)

The steps the booker follows live in `portal_recipe.json`. The sign-in steps match the public portal page. The booking steps are a first guess, because the booking form is only visible after signing in. Until they are confirmed, the app shows a yellow notice.

To confirm them:

1. In the app, click **Dry run**. It goes through every step except the final booking button and saves screenshots. Open the screenshot link in Activity to see where it stopped.
2. If a step fails, record a real booking. On a computer with Python installed, double click `record_booking_windows.bat` or `record_booking_mac.command`. A browser opens. Sign in, book a room the normal way, then close the browser. This saves `recording.py`.
3. Update the steps in `portal_recipe.json` to match the recording (or send the recording to Claude to do it). Raise `"version"` by 1 and set `"verified": true` once a dry run gets all the way through.
4. Commit it to the repo. Every app downloads the newer recipe on its next run, so nobody needs to reinstall.

### When slots open

Study rooms can be booked up to one week ahead. A whole day opens at exactly midnight one week before, so at 12:00 am on Sep 30 all of Oct 7 opens. The app runs at 12:00 am by default, goes for the newly opened day first, then fills any earlier gaps. If a room is not free yet it retries every 30 seconds for 20 minutes.

You can add more run times in **Settings** (for example a morning run to pick up cancellations). Everyone's default is set in `app_defaults.json` as `"book_times": ["00:00"]`. If the computer was asleep at a run time, the run happens as soon as it wakes.

Each time slot can have **backup times**. The booker tries your first room at the main time, then at each backup time, then moves to the next room. The portal allows one 3 hour booking per person per day, so once a day is booked it is skipped.

## Running from source

Needs Python 3.10 or newer. Double click `start_windows.bat` or `start_mac.command`, or run:

```
pip install -r requirements.txt
python run.py
```

The page opens at http://127.0.0.1:5057. Personal data is stored in `%APPDATA%\CanalBooker` on Windows and `~/Library/Application Support/CanalBooker` on Mac.
