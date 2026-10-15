# MotionClick Studio

A Windows-focused, four-in-one desktop GUI for audio-triggered mouse clicks, release-based randomized click timing, predictive red-ball following, and blue-square shuffle tracking. All automation runs locally on your computer. No accounts, uploads, telemetry, or network requests are built into the application.

## Included tools

| Tool | Default behavior | Start | Stop |
| --- | --- | --- | --- |
| Sound → Click | On system-output audio onset, click **1.5 seconds later**; once per sound period | Arm | Stop or Esc |
| Timed Click | After a **manual left-button release**, click after a random **7.95–8.05 seconds** | Arm then manually click/release | Stop or Esc |
| Red Ball | Follow red ball every **0.2 seconds**; predict **0.04 seconds** ahead | Arm then manually click/release | Stop or Esc |
| Blue Squares | Capture original blue IDs, track through all-white movement at **30 FPS target**, mark uncertainty | Select board, Arm, manually click/release during the blue phase | F (save), Esc (stop/save), or Stop |

The GUI allows configuring all the settings shown above. Only one tool can run at a time. Switching tabs stops the previous tool. A synthetic mouse click is excluded from triggering the timed-click tool again.

## Windows installation (source)

1. Install Python 3.11–3.13 from [python.org](https://www.python.org/downloads/windows/). The GitHub Windows build uses 3.13.
2. Download/extract this repository.
3. **Quick start:** double-click `start_windows.bat` to install the dependencies and open the GUI. Or open PowerShell in the extracted folder:

   ```powershell
   py -m pip install -r requirements.txt
   py app.py
   ```

On Windows, system audio capture uses the default playback device through WASAPI loopback. This is **not** microphone recording. If the speaker or audio device is absent, the GUI displays a corresponding error.

## Windows executable download

Builds are produced automatically by GitHub Actions once the repository is published:

- Go to **Actions → Windows executable → most recent successful run → Artifacts** for the first unsigned Windows ZIP.
- The stable release download URL is `https://github.com/<owner>/<repo>/releases/latest/download/MotionClickStudio-Windows.zip` **after** a tagged `v*` release has been built successfully.
- Extract the ZIP fully, then run `MotionClickStudio.exe` inside the folder. The output is a PyInstaller **onedir**, not a single portable executable.
- Windows SmartScreen may warn because the executable is unsigned. Do not bypass a security warning without verifying the release/source.

### Publish this repository and website

1. Create a **public** GitHub repository named `motionclick-studio` with no starter files.
2. Upload all files and folders (including `.github/workflows/` and `docs/`) to the repo's `main` branch. If using Git:

   ```powershell
   git init
   git add .
   git commit -m "Initial MotionClick Studio release"
   git branch -M main
   git remote add origin https://github.com/<your-user>/motionclick-studio.git
   git push -u origin main
   ```
3. In **Settings → Pages → Build and deployment**, set **Source** to **GitHub Actions**.
4. In **Actions**, wait for the *Windows executable* and *Publish download website* workflows to succeed. You may need to allow workflows to run in the repo's Actions settings.
5. For a permanent latest-release link, create and push a version tag:

   ```powershell
   git tag v1.0.0
   git push origin v1.0.0
   ```

   Wait for the Windows executable workflow to publish the GitHub Release.
6. Your website will be at `https://<your-user>.github.io/motionclick-studio/`.

The website derives its GitHub download URLs from the Pages address; no hardcoded account name needs editing when published under `username.github.io/repository/`.

## Safety and limitations

- Global left mouse release and Esc are captured when the app is armed. Moving the cursor to the extreme upper-left triggers PyAutoGUI's failsafe for cursor-controlled tools. **Esc / Stop are the primary emergency controls.**
- The scheduled sound click uses a silence gate; it is not source-specific sound recognition and may be triggered by system notifications or music. Disable notification sounds while testing.
- All timing is **best effort**: Windows input-event timing, device latency and processing overhead can make actual release-to-release intervals differ from selected targets.
- Red-ball estimation assumes roughly smooth motion, and can select the wrong red shape when similar objects appear.
- Blue-square tracking relies on color/shape segmentation; tune thresholds in `square_tracking.py` to match your game. Identical objects that fully overlap and then diverge unpredictably **cannot always be reliably disambiguated** with screenshots alone. Results are flagged `possibly_ambiguous` where uncertainty is detected. Frame-rate depends on screen size and CPU speed.
- Blue-square output files are written to `~/Documents/MotionClick Studio/` with timestamped PNG and CSV filenames.
- Screen selection is performed using an OpenCV selection dialog: drag a rectangle, press Enter, and return to the GUI.
- The source is provided for inspection. No Windows binary can be tested on this Linux build environment; the Windows executable must be generated/tested by the GitHub workflow and a Windows machine before production claims.

## Code layout

- `app.py` — Windows GUI, scheduling, audio and red-ball automation, blue-square integration.
- `square_tracking.py` — detection, Kalman states, Hungarian assignment and ambiguity flags.
- `docs/index.html` — responsive download site for GitHub Pages.
- `.github/workflows/windows-build.yml` — Windows PyInstaller build and release.
- `.github/workflows/pages.yml` — GitHub Pages publishing.
- `tests/test_logic.py` — independent mathematical and synthetic-vision unit tests.

## Development

```powershell
py -m pip install -r requirements.txt pytest
py -m pytest -q
```

License: MIT, see `LICENSE`.
