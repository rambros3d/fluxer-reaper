import logging
import asyncio
import aiohttp
import sys
import os
import stat
from typing import Dict, Any, Optional
from pathlib import Path
from src.core.utils import get_app_version

logger = logging.getLogger(__name__)

REPO_OWNER = "rambros3d"
REPO_NAME = "disco-reaper"
API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"

def get_current_version() -> str:
    """Returns the current version string, e.g., '1.0.0'. Strips 'Reaper-' and 'v'."""
    raw = get_app_version()
    # E.g., raw is 'Reaper-v1.0.0' or 'Reaper-1.0.0' or 'Reaper-Unknown'
    if raw.startswith("Reaper-"):
        raw = raw[7:]
    if raw.startswith("v"):
        raw = raw[1:]
    return raw

def parse_version(version_str: str) -> tuple:
    """Parses a version string into a tuple of ints for comparison. e.g., '1.0.0' -> (1,0,0)"""
    try:
        # Strip 'v' and any prerelease tags like '-beta' for simple int comparison
        clean = version_str.lstrip('v').split('-')[0]
        return tuple(map(int, clean.split('.')))
    except Exception:
        return (0, 0, 0)

async def check_for_updates() -> Optional[Dict[str, Any]]:
    """
    Fetches the latest release from GitHub.
    Returns a dict with update info if a newer version is available, else None.
    Dict structure: {"version": "v1.2.0", "url": "...", "body": "...", "asset_url": "...", "asset_name": "..."}
    """
    # Only offer updates if we are running from a frozen PyInstaller bundle
    # We don't want to overwrite the dev environment python script
    if not getattr(sys, 'frozen', False):
        logger.info("Auto-updater: Running from source, updates disabled.")
        return None

    current_ver_str = get_current_version()
    if "Unknown" in current_ver_str or "git" in current_ver_str:
        logger.info(f"Auto-updater: Current version '{current_ver_str}' is unstable/dev. Checking disabled.")
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, headers={"Accept": "application/vnd.github.v3+json"}) as resp:
                if resp.status == 200:
                    releases = await resp.json()
                    if not isinstance(releases, list) or not releases:
                        return None
                    data = releases[0]
                    latest_tag = data.get("tag_name", "")
                    
                    latest_ver = parse_version(latest_tag)
                    current_ver = parse_version(current_ver_str)
                    
                    if latest_ver > current_ver:
                        logger.info(f"Auto-updater: New version found: {latest_tag} (Current: {current_ver_str})")
                        
                        # Find the correct asset for this OS
                        expected_asset_name = ""
                        if sys.platform == "win32":
                            expected_asset_name = "disco-reaper-windows.zip"
                        elif sys.platform == "darwin":
                            expected_asset_name = "disco-reaper-macos.zip"
                        else:
                            expected_asset_name = "disco-reaper-linux.zip"
                            
                        asset_url = None
                        for asset in data.get("assets", []):
                            if asset.get("name") == expected_asset_name:
                                asset_url = asset.get("browser_download_url")
                                break
                                
                        if not asset_url:
                            logger.error(f"Auto-updater: Could not find asset '{expected_asset_name}' in release.")
                            return None
                            
                        return {
                            "version": latest_tag,
                            "url": data.get("html_url"),
                            "body": data.get("body", "No release notes provided."),
                            "asset_url": asset_url,
                            "asset_name": expected_asset_name,
                            "prerelease": data.get("prerelease", False)
                        }
                    else:
                        logger.info(f"Auto-updater: Up to date (Current: {current_ver_str}, Latest: {latest_tag})")
                else:
                    logger.warning(f"Auto-updater: Failed to fetch latest release. Status: {resp.status}")
    except Exception as e:
        logger.error(f"Auto-updater: Error checking for updates: {e}")
        
    return None

async def download_and_extract_update(asset_url: str, progress_callback=None) -> Optional[Path]:
    """
    Downloads the zip asset from GitHub and extracts the executable to a temporary path.
    Returns the path to the downloaded executable.
    """
    import tempfile
    import zipfile
    
    try:
        temp_dir = Path(tempfile.mkdtemp(prefix="discoreaper_update_"))
        zip_path = temp_dir / "update.zip"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(asset_url) as resp:
                if resp.status != 200:
                    logger.error(f"Auto-updater: Download failed with status {resp.status}")
                    return None
                    
                total_size = int(resp.headers.get('content-length', 0))
                downloaded_size = 0
                
                with open(zip_path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if progress_callback and total_size > 0:
                            progress_callback(downloaded_size, total_size)
                            
        # Extract the zip
        # The zip typically contains a 'REAPER' folder with 'DiscoReaper' or 'DiscoReaper.exe' inside
        executable_name = "DiscoReaper.exe" if sys.platform == "win32" else "DiscoReaper"
        found_exe_path = None
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # We don't want to blindly extract everything everywhere. Just find the executable.
            for file_info in zip_ref.infolist():
                if file_info.filename.endswith(executable_name):
                    # We found the executable. Extract it directly to the temp dir.
                    # We read and write it so we flatten any directory structure inside the zip
                    with zip_ref.open(file_info) as source, open(temp_dir / executable_name, "wb") as target:
                        target.write(source.read())
                    found_exe_path = temp_dir / executable_name
                    break
                    
        if not found_exe_path or not found_exe_path.exists():
            logger.error("Auto-updater: Could not find the executable inside the downloaded zip.")
            return None
            
        return found_exe_path
        
    except Exception as e:
        logger.error(f"Auto-updater: Error during download/extract: {e}")
        return None

def apply_update_and_restart(new_exe_path: Path):
    """
    Replaces the current executable with the new one and restarts.
    Handles Windows file-locking quirks and cross-device filesystem moves.
    """
    import shutil
    import subprocess
    
    try:
        current_exe = Path(sys.executable) if getattr(sys, 'frozen', False) else Path(sys.argv[0])
        current_exe = current_exe.resolve()
        
        logger.info(f"Applying update: replacing {current_exe} with {new_exe_path}")
        
        # Make the new binary executable (mainly for Linux/macOS)
        if sys.platform != "win32":
            st = os.stat(new_exe_path)
            os.chmod(new_exe_path, st.st_mode | stat.S_IEXEC)
            
        if sys.platform == "win32":
            # On Windows, we can't overwrite a running .exe, but we CAN rename it.
            old_exe = current_exe.parent / (current_exe.name + ".old")
            if old_exe.exists():
                try:
                    old_exe.unlink()
                except Exception:
                    pass
            os.rename(current_exe, old_exe)
            shutil.move(str(new_exe_path), str(current_exe))
            
            logger.info("Update applied. Restarting process...")
            subprocess.Popen([str(current_exe)] + sys.argv[1:])
            sys.exit(0)
            
        else:
            # On Linux/macOS, replace it. 
            # We must unlink (delete) the existing file first to avoid "Text file busy"
            # if we are crossing filesystems (shutil.move falls back to copy & overwrite).
            if current_exe.exists():
                try:
                    logger.info(f"Unlinking {current_exe} to avoid 'Text file busy'")
                    current_exe.unlink()
                except Exception as e:
                    logger.warning(f"Could not unlink current exe: {e}. Attempting move anyway.")
            
            shutil.move(str(new_exe_path), str(current_exe))
            st = os.stat(current_exe)
            os.chmod(current_exe, st.st_mode | stat.S_IEXEC)

            logger.info("Update applied. Restarting via execv...")
            # Restart
            os.execv(str(current_exe), [str(current_exe)] + sys.argv[1:])
            
    except Exception as e:
        logger.error(f"Auto-updater: Failed to apply update: {e}")
        raise
