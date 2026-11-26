"""
Automated Live Browser Walkthrough & Video Recorder
==================================================
Demonstrates 3D Holographic Cyber-Medical DPO Command Center (ui.py)
using Playwright in headed mode (headless=False) and records video.

Output: demo_recording/secret_wards_live_demo.mp4
"""

import os
import sys
import time
import glob
import signal
import subprocess
import urllib.request
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDING_DIR = os.path.join(BASE_DIR, "demo_recording")
PORT = 8501
URL = f"http://localhost:{PORT}"


def free_port(port=8501):
    try:
        out = subprocess.check_output(["lsof", "-t", f"-i:{port}"]).decode().strip()
        if out:
            pids = out.split()
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except Exception:
                    pass
            time.sleep(1)
    except Exception:
        pass


def wait_for_server(url, timeout=30):
    start = time.time()
    print(f"Waiting for Streamlit server at {url}...")
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=1) as resp:
                if resp.status == 200:
                    print("Streamlit server is ready!")
                    return True
        except Exception:
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    if resp.status in (200, 304):
                        print("Streamlit server is ready!")
                        return True
            except Exception:
                pass
        time.sleep(0.8)
    raise RuntimeError(f"Server did not start within {timeout}s")


def smooth_scroll(page, target_y, steps=25, delay=0.03):
    current_y = page.evaluate("window.scrollY")
    delta = (target_y - current_y) / max(steps, 1)
    for _ in range(steps):
        current_y += delta
        page.evaluate(f"window.scrollTo(0, {current_y})")
        time.sleep(delay)


def run_walkthrough():
    os.makedirs(RECORDING_DIR, exist_ok=True)
    free_port(PORT)

    # Clean previous raw recordings if any
    for old_file in glob.glob(os.path.join(RECORDING_DIR, "*")):
        if not old_file.endswith(".mp4"):
            try:
                os.remove(old_file)
            except Exception:
                pass

    # Start Streamlit server in background
    server_cmd = [
        sys.executable, "-m", "streamlit", "run", "ui.py",
        f"--server.port={PORT}",
        "--server.headless=true",
        "--browser.serverAddress=localhost",
        "--", "--output", "my_output",
        "--input", os.path.expanduser("~/Secret-Wards-ToolKit/dev/input")
    ]
    print(f"Starting Streamlit: {' '.join(server_cmd)}")
    server_proc = subprocess.Popen(
        server_cmd,
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid if hasattr(os, "setsid") else None
    )

    try:
        wait_for_server(URL, timeout=35)

        print("\n🚀 Launching Playwright Chromium in HEADED mode (1440x900)...")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                slow_mo=80,
                args=["--start-maximized"]
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=RECORDING_DIR,
                record_video_size={"width": 1440, "height": 900}
            )
            page = context.new_page()

            # Open UI
            print(f"Navigating to {URL}...")
            page.goto(URL, wait_until="networkidle")
            page.wait_for_selector('div[data-testid="stMetric"]', timeout=30000)
            time.sleep(2)

            # =================================================================
            # STEP 1: 3D Holographic Hero Shield & Top KPI Strip
            # =================================================================
            print("Step 1: Demonstrating 3D Holographic Hero Shield & Top KPI Strip...")
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(1)

            # Interact with the 3D Hero Shield: click and drag to orbit
            page.mouse.move(580, 160)
            page.mouse.down()
            for dx in range(0, 160, 20):
                page.mouse.move(580 + dx, 160 + int(dx * 0.2))
                time.sleep(0.05)
            page.mouse.up()
            time.sleep(2.0)

            # Hover over metrics
            metrics = page.locator('div[data-testid="stMetric"]').all()
            for m in metrics[:4]:
                m.hover()
                time.sleep(0.4)

            time.sleep(2.5)  # Pause for judges to read KPI values (0.0% leaks, 100% medical)

            # Smooth scroll down to tabs
            smooth_scroll(page, 440, steps=20)
            time.sleep(1.5)

            # =================================================================
            # STEP 2: Tab 1 — X-Ray Privacy Scanner & Multilingual Redaction Diff
            # =================================================================
            print("Step 2: Demonstrating Tab 1 (X-Ray Scanner & Multilingual Redaction Diff)...")
            tabs = page.locator('[role="tab"]').all()
            if tabs:
                tabs[0].click()
            time.sleep(1.5)

            # Interact with X-Ray Privacy Scanner slider
            slider = page.locator('div[data-testid="stSlider"]').first
            if slider.is_visible():
                slider.click()
                time.sleep(0.5)
                # Press Home to sweep slider to 0 (Raw Exposed View)
                page.keyboard.press("Home")
                time.sleep(2.0)
                # Press Right arrows to sweep slider back to 100% Protected View
                page.keyboard.press("End")
                time.sleep(2.0)

            # Select clinical discharge note in selectbox
            cb = page.locator('[role="tabpanel"] input[role="combobox"]').first
            if cb.is_visible():
                cb.focus()
                cb.click()
                time.sleep(0.5)
                page.keyboard.type("discharge")
                time.sleep(0.8)
                page.keyboard.press("ArrowDown")
                time.sleep(0.5)
                page.keyboard.press("Enter")
                time.sleep(2.0)

            # Smooth scroll down to observe side-by-side Raw vs Sanitized diff
            smooth_scroll(page, 750, steps=25)
            time.sleep(3.5)  # Pause to inspect [PSN-XXXXXX] cyan pill, [ADDRESS] amber pill, vitals intact

            # Scroll inside the diff box to show preserved vitals & diagnoses
            diff_boxes = page.locator('.diff-box').all()
            if len(diff_boxes) >= 2:
                diff_boxes[1].hover()
                page.mouse.wheel(0, 250)
                time.sleep(1.5)

            # Scroll back up and select another file (e.g. CSV billing claims)
            smooth_scroll(page, 480, steps=20)
            time.sleep(1.0)
            if cb.is_visible():
                cb.focus()
                cb.click()
                time.sleep(0.5)
                page.keyboard.press("ArrowDown")
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")
                time.sleep(2.0)
            smooth_scroll(page, 720, steps=20)
            time.sleep(2.5)

            # =================================================================
            # STEP 3: Tab 2 — 3D Cross-Hospital Identity Constellation
            # =================================================================
            print("Step 3: Demonstrating Tab 2 (3D Cross-Hospital Identity Constellation)...")
            smooth_scroll(page, 400, steps=15)
            time.sleep(0.5)

            tabs = page.locator('[role="tab"]').all()
            if len(tabs) >= 2:
                tabs[1].click()
            time.sleep(1.5)

            # Show identity metrics & patient cluster selector
            smooth_scroll(page, 520, steps=20)
            time.sleep(1.5)

            # Interact with the 3D Constellation canvas: orbit drag
            page.mouse.move(680, 560)
            page.mouse.down()
            for dx in range(0, 180, 25):
                page.mouse.move(680 + dx, 560 + int(dx * 0.15))
                time.sleep(0.04)
            page.mouse.up()
            time.sleep(3.0)  # Observe glowing energy beams and unlinked satellites outside shield

            # Select patient cluster dropdown in Tab 2
            cluster_cbs = page.locator('[role="tabpanel"] input[role="combobox"]').all()
            if cluster_cbs:
                cluster_cbs[0].focus()
                cluster_cbs[0].click()
                time.sleep(0.6)
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")
                time.sleep(1.5)

            # Scroll through the patient care trajectory records table
            smooth_scroll(page, 950, steps=20)
            time.sleep(3.0)

            # Scroll down to show Anti-Over-Merge Safety Rationale
            smooth_scroll(page, 1250, steps=25)
            time.sleep(3.5)  # Read Anti-Over-Merge ("patient": null) 1.00 precision guard

            # Expand the unlinked records expander
            expander = page.locator('text=View All')
            if expander.count() > 0:
                expander.first.click()
                time.sleep(1.0)
                smooth_scroll(page, 1450, steps=20)
                time.sleep(2.5)

            # =================================================================
            # STEP 4: Tab 3 — 3D Voter-List Attack Simulation & Quarantine Vault
            # =================================================================
            print("Step 4: Demonstrating Tab 3 (3D Cohort Cube & Quarantine Vault)...")
            smooth_scroll(page, 400, steps=25)
            time.sleep(0.5)

            tabs = page.locator('[role="tab"]').all()
            if len(tabs) >= 3:
                tabs[2].click()
            time.sleep(1.5)

            # Show 3D Cohort Cube
            smooth_scroll(page, 550, steps=20)
            time.sleep(1.5)

            # Interact with 3D Cohort Cube: orbit drag
            page.mouse.move(680, 580)
            page.mouse.down()
            for dx in range(0, 160, 20):
                page.mouse.move(680 + dx, 580 - int(dx * 0.2))
                time.sleep(0.04)
            page.mouse.up()
            time.sleep(3.0)  # Read k>=450 Invariant Cluster

            # Scroll down to Mathematical Stay Invariance proof
            smooth_scroll(page, 920, steps=20)
            time.sleep(3.5)  # Read (T_dis + Delta) - (T_adm + Delta) = Stay Duration raw

            # Interact with District filter
            t3_cbs = page.locator('[role="tabpanel"] input[role="combobox"]').all()
            if t3_cbs:
                t3_cbs[0].focus()
                t3_cbs[0].click()
                time.sleep(0.5)
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")
                time.sleep(1.5)

            # Scroll down to Section 2: Quarantine Vault
            smooth_scroll(page, 1450, steps=25)
            time.sleep(4.0)  # Read SHA-256 Quarantine Table with zero leaked filenames

            # Scroll back to the top of the app smoothly
            print("Walkthrough complete, returning to top banner...")
            smooth_scroll(page, 0, steps=30, delay=0.02)
            time.sleep(2.0)

            # Finalize context to save video
            print("Closing browser context to finalize recording...")
            page.close()
            context.close()
            browser.close()

    finally:
        # Terminate Streamlit background process cleanly
        print("Terminating background Streamlit server...")
        if server_proc.poll() is None:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(server_proc.pid), signal.SIGTERM)
                else:
                    server_proc.terminate()
            except Exception:
                server_proc.kill()
        time.sleep(1)

    # Post-process recorded video
    recorded_videos = glob.glob(os.path.join(RECORDING_DIR, "*.webm"))
    if not recorded_videos:
        print("⚠️ No .webm video found in demo_recording/")
        return

    latest_webm = max(recorded_videos, key=os.path.getctime)
    final_mp4 = os.path.join(RECORDING_DIR, "secret_wards_live_demo.mp4")

    # Check for ffmpeg
    ffmpeg_bin = "/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else "ffmpeg"
    try:
        subprocess.run([ffmpeg_bin, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        has_ffmpeg = True
    except Exception:
        has_ffmpeg = False

    if has_ffmpeg:
        print(f"\n🎬 Converting {os.path.basename(latest_webm)} to {final_mp4} using ffmpeg...")
        conv_cmd = [
            ffmpeg_bin, "-y",
            "-i", latest_webm,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-crf", "22",
            final_mp4
        ]
        res = subprocess.run(conv_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0 and os.path.exists(final_mp4):
            size_mb = os.path.getsize(final_mp4) / (1024 * 1024)
            print(f"✅ Live demo video successfully saved to: {final_mp4} ({size_mb:.2f} MB)")
            try:
                os.remove(latest_webm)
            except Exception:
                pass
            return
        else:
            print(f"⚠️ ffmpeg conversion returned non-zero code: {res.stderr.decode()[:200]}")

    final_webm = os.path.join(RECORDING_DIR, "secret_wards_live_demo.webm")
    os.rename(latest_webm, final_webm)
    size_mb = os.path.getsize(final_webm) / (1024 * 1024)
    print(f"✅ Live demo video successfully saved to: {final_webm} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    run_walkthrough()
