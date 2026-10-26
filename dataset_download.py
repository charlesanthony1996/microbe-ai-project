"""
Download the bacterial datasets (bact_phase and bact_fluor) from the
Omnipose OSF project (https://osf.io/xmury) using the osfclient library.

Usage:
    python download_omnipose.py                    -> LISTS bacteria files only
    python download_omnipose.py --download         -> downloads bacteria files
    python download_omnipose.py --all              -> LISTS everything except apps
    python download_omnipose.py --all --download   -> downloads everything except apps

The script:
    - by default keeps only bacterial data; with --all keeps everything except
      the GUI app installers in the apps folder
    - keeps the original folder structure under OUTPUT_DIR
    - skips files that were already downloaded completely
    - writes each file to a temporary ".part" file first, so an interrupted
      download never looks like a finished file
    - retries each file a few times if the connection drops
"""

import os
import sys
import time

from dotenv import load_dotenv
from osfclient import OSF


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

PROJECT_ID = "xmury"
STORAGE_NAME = "osfstorage"
OUTPUT_DIR = "omnipose_data"

# Default mode: a file is downloaded if its path contains one of these words
WANTED_KEYWORDS = ["bact_phase", "bact_fluor"]

# "--all" mode: everything is downloaded EXCEPT paths containing these words
# (the apps folder holds GUI installers, not data)
SKIP_KEYWORDS = ["apps/"]

MAX_RETRIES = 3

# OSF answers "429 Too Many Requests" when we send too many requests.
# Wait this many seconds after the first 429 (doubled on each further attempt).
RATE_LIMIT_WAIT_SECONDS = 60
MAX_RATE_LIMIT_ATTEMPTS = 5

# Short pause between downloads so we do not hit the limit again
PAUSE_BETWEEN_FILES_SECONDS = 1


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def path_matches_any(remote_path, keywords):
    lower_path = remote_path.lower()

    for keyword in keywords:
        if keyword in lower_path:
            return True

    return False


def path_is_wanted(remote_path, download_everything):
    if download_everything:
        is_skipped = path_matches_any(remote_path, SKIP_KEYWORDS)
        is_wanted = not is_skipped
        return is_wanted

    is_wanted = path_matches_any(remote_path, WANTED_KEYWORDS)
    return is_wanted


def size_in_megabytes(remote_file):
    size_in_bytes = getattr(remote_file, "size", None)

    if size_in_bytes is None:
        return None

    megabytes = size_in_bytes / (1024 * 1024)
    return megabytes


def describe_size(remote_file):
    megabytes = size_in_megabytes(remote_file)

    if megabytes is None:
        return "unknown size"

    text = f"{megabytes:.1f} MB"
    return text


def make_local_path(remote_file):
    remote_path = remote_file.path

    # OSF paths start with "/", which would make os.path.join ignore OUTPUT_DIR
    relative_path = remote_path.lstrip("/")

    local_path = os.path.join(OUTPUT_DIR, relative_path)
    return local_path


def is_rate_limit_error(error):
    error_text = str(error)
    is_rate_limited = "429" in error_text
    return is_rate_limited


def wait_for_rate_limit(attempt):
    # 60 s, 120 s, 240 s, ... for attempts 1, 2, 3, ...
    multiplier = 2 ** (attempt - 1)
    wait_seconds = RATE_LIMIT_WAIT_SECONDS * multiplier

    print(f"    OSF rate limit reached (429). Waiting {wait_seconds} seconds...")
    time.sleep(wait_seconds)


def create_osf_client():
    # A personal access token gives a much higher rate limit.
    # Put it in a file called ".env" next to this script, containing one line:
    #     OSF_TOKEN=your_token_here
    # load_dotenv() reads that file and makes OSF_TOKEN available below.
    load_dotenv()

    token = os.environ.get("OSF_TOKEN")

    if token:
        print("Using OSF_TOKEN from the environment (logged-in rate limit).")
        osf = OSF(token=token)
    else:
        print("No OSF_TOKEN set: using the anonymous (low) rate limit.")
        osf = OSF()

    return osf


def connect_to_storage(osf):
    for attempt in range(1, MAX_RATE_LIMIT_ATTEMPTS + 1):
        try:
            project = osf.project(PROJECT_ID)
            storage = project.storage(STORAGE_NAME)
            return storage

        except RuntimeError as error:
            if not is_rate_limit_error(error):
                raise
            wait_for_rate_limit(attempt)

    print("Still rate limited after several attempts. Try again later.")
    sys.exit(1)


def collect_all_files(storage):
    for attempt in range(1, MAX_RATE_LIMIT_ATTEMPTS + 1):
        all_files = []

        try:
            # storage.files walks through all folders and subfolders
            for remote_file in storage.files:
                all_files.append(remote_file)
            return all_files

        except RuntimeError as error:
            if not is_rate_limit_error(error):
                raise
            wait_for_rate_limit(attempt)

    print("Still rate limited while listing files. Try again later.")
    sys.exit(1)


def download_one_file(remote_file, local_path):
    folder = os.path.dirname(local_path)
    os.makedirs(folder, exist_ok=True)

    temporary_path = local_path + ".part"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with open(temporary_path, "wb") as output_file:
                remote_file.write_to(output_file)

            # Only rename after the download finished without errors
            os.replace(temporary_path, local_path)
            return True

        except Exception as error:
            print(f"    attempt {attempt} of {MAX_RETRIES} failed: {error}")

            if is_rate_limit_error(error):
                wait_for_rate_limit(attempt)

    # All attempts failed: remove the half-written file
    if os.path.exists(temporary_path):
        os.remove(temporary_path)

    return False


# ---------------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------------

def main():
    should_download = "--download" in sys.argv
    download_everything = "--all" in sys.argv

    if download_everything:
        print(f"Mode: everything except {SKIP_KEYWORDS}")
    else:
        print(f"Mode: only files matching {WANTED_KEYWORDS}")

    print(f"Connecting to OSF project '{PROJECT_ID}'...")
    osf = create_osf_client()
    storage = connect_to_storage(osf)

    print("Listing all files (this can take a minute)...")
    all_files = collect_all_files(storage)
    print(f"Found {len(all_files)} files in total.")

    # Step 1: keep only the files selected by the current mode
    wanted_files = []
    for remote_file in all_files:
        is_wanted = path_is_wanted(remote_file.path, download_everything)
        if is_wanted:
            wanted_files.append(remote_file)

    print(f"{len(wanted_files)} files selected.")
    print()

    # If nothing matched, show every path so the keywords can be adjusted
    if len(wanted_files) == 0:
        print("No matching files. These are all the files in the project:")
        for remote_file in all_files:
            size_text = describe_size(remote_file)
            print(f"  {remote_file.path}  ({size_text})")
        print()
        print("Change WANTED_KEYWORDS at the top of the script and run again.")
        return

    # Step 2: show what would be downloaded
    total_megabytes = 0.0
    for remote_file in wanted_files:
        size_text = describe_size(remote_file)
        print(f"  {remote_file.path}  ({size_text})")

        megabytes = size_in_megabytes(remote_file)
        if megabytes is not None:
            total_megabytes = total_megabytes + megabytes

    print()
    print(f"Total size (known files): {total_megabytes:.1f} MB")
    print()

    if not should_download:
        print("This was only a listing. To download, run one of:")
        print("    python download_omnipose.py --download")
        print("    python download_omnipose.py --all --download")
        return

    # Step 3: download the files one by one
    downloaded_count = 0
    skipped_count = 0
    failed_paths = []

    for index, remote_file in enumerate(wanted_files, start=1):
        local_path = make_local_path(remote_file)
        size_text = describe_size(remote_file)
        print(f"[{index}/{len(wanted_files)}] {remote_file.path} ({size_text})")

        if os.path.exists(local_path):
            print("    already downloaded, skipping")
            skipped_count = skipped_count + 1
            continue

        success = download_one_file(remote_file, local_path)

        if success:
            print(f"    saved to {local_path}")
            downloaded_count = downloaded_count + 1
        else:
            print("    FAILED")
            failed_paths.append(remote_file.path)

        time.sleep(PAUSE_BETWEEN_FILES_SECONDS)

    # Step 4: summary
    print()
    print("Done.")
    print(f"  downloaded: {downloaded_count}")
    print(f"  skipped (already there): {skipped_count}")
    print(f"  failed: {len(failed_paths)}")

    if len(failed_paths) > 0:
        print()
        print("These files failed. Run the script again to retry only them:")
        for failed_path in failed_paths:
            print(f"  {failed_path}")


if __name__ == "__main__":
    main()