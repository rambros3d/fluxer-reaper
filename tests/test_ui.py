import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from textual.app import App
from textual.widgets import Button, Label, OptionList
from textual.widgets.option_list import Option
from src.ui.main_app import ReaperApp, ConfigSelectionScreen, ConfigScreen, ConfigRow
from src.ui.mode_screen import ModeScreen
from src.ui.widgets import ClipboardInput
from src.core.configuration import AppConfig

import os


def _find_config_button(screen, cfg_display_name: str) -> Button | None:
    """Find the 'Open' button for a config by its display name."""
    for row in screen.query(ConfigRow):
        if row.display_name == cfg_display_name:
            return row.query_one(".btn_open", Button)
    return None


def _click_first_config(pilot, screen) -> Button | None:
    """Find and return the first config's open button."""
    rows = list(screen.query(ConfigRow))
    if not rows:
        return None
    btn = rows[0].query_one(".btn_open", Button)
    return btn



@pytest.fixture
def mock_configs(tmp_path, log):
    reaper_dir = tmp_path / "ReaperFiles-TestConfig"
    reaper_dir.mkdir()
    (reaper_dir / "reaper_config.yaml").write_text("source_bot_token: 'fake'\nsource_server_id: '123'\ntool_mode: 'backup_only'")
    
    autotest_dir = tmp_path / "ReaperFiles-AutoTest"
    autotest_dir.mkdir()
    (autotest_dir / "reaper_config.yaml").write_text("source_bot_token: 'fake'\nsource_server_id: '123'\ntool_mode: 'backup_transfer'")
    
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    log(f"CWD changed to: {tmp_path}")
    yield tmp_path
    os.chdir(old_cwd)

async def wait_for_screen(app, screen_class, timeout=5.0):
    import time
    start = time.time()
    while time.time() - start < timeout:
        if isinstance(app.screen, screen_class):
            return True
        await asyncio.sleep(0.1)
    return False

@pytest.mark.asyncio
async def test_ui_minimal_launch(mock_configs, log):
    """Verify app launch and screen transition to ModeScreen."""
    log("Running test_ui_minimal_launch")
    try:
        with patch("src.ui.main_app.ConfigSelectionScreen.check_updates", AsyncMock()):
            app = ReaperApp()
            async with app.run_test() as pilot:
                await wait_for_screen(app, ConfigSelectionScreen)
                # Click the open button of the first config
                btn = _click_first_config(pilot, app.screen)
                assert btn is not None, "No config rows found"
                await pilot.click(btn)
                await wait_for_screen(app, ModeScreen)
                assert isinstance(app.screen, ModeScreen)
                log("test_ui_minimal_launch PASSED")
    except Exception as e:
        log(f"test_ui_minimal_launch FAILED: {e}")
        raise

@pytest.mark.asyncio
async def test_ui_config_wizard_save(mock_configs, log):
    """Verify configuration editing and saving."""
    log("Running test_ui_config_wizard_save")
    try:
        with patch("src.ui.main_app.ConfigSelectionScreen.check_updates", AsyncMock()):
            app = ReaperApp()
            async with app.run_test() as pilot:
                await wait_for_screen(app, ConfigSelectionScreen)
                btn = _click_first_config(pilot, app.screen)
                assert btn is not None, "No config rows found"
                await pilot.click(btn)
                await wait_for_screen(app, ModeScreen)
                await pilot.click("#btn_config")
                await wait_for_screen(app, ConfigScreen)
                
                screen = app.screen
                inp = screen.query_one("#inp_source_token", ClipboardInput)
                inp.value = "new_fake_token"
                
                with patch("src.ui.main_app.save_config") as mock_save:
                    await pilot.click("#btn_save")
                    await pilot.pause(0.2)
                    assert mock_save.called
                    log("test_ui_config_wizard_save PASSED")
    except Exception as e:
        log(f"test_ui_config_wizard_save FAILED: {e}")
        raise

@pytest.mark.asyncio
async def test_ui_operation_trigger(mock_configs, log):
    """Verify that an operation can be triggered."""
    log("Running test_ui_operation_trigger")
    from src.ui.shuttle_ops import OperationPane
    from src.ui.modals import ChannelPickerScreen, ProgressScreen
    try:
        with patch("src.ui.main_app.ConfigSelectionScreen.check_updates", AsyncMock()):
            with patch.object(OperationPane, "run_validate", AsyncMock()):
                app = ReaperApp()
                async with app.run_test() as pilot:
                    await wait_for_screen(app, ConfigSelectionScreen)
                    btn = _click_first_config(pilot, app.screen)
                    assert btn is not None, "No config rows found"
                    await pilot.click(btn)
                    await wait_for_screen(app, ModeScreen)
                    
                    pane = app.screen.query_one(OperationPane)
                    pane.tokens_valid = True
                    pane.src_channels = [{"id": 1, "name": "t"}]
                    pane.src_cat_map = {None: "D"}
                    pane.tgt_channels = [{"id": 2, "name": "t"}]
                    pane.tgt_cat_map = {None: "D"}
                    pane.all_tgt_channels = pane.tgt_channels
                    
                    btn = pane.query_one("#op_backup_msgs", Button)
                    btn.disabled = False
                    await pilot.pause(0.2)
                    btn.focus()
                    await pilot.press("enter")
                    
                    await wait_for_screen(app, (ChannelPickerScreen, ProgressScreen))
                    assert isinstance(app.screen, (ChannelPickerScreen, ProgressScreen))
                    log("test_ui_operation_trigger PASSED")
    except Exception as e:
        log(f"test_ui_operation_trigger FAILED: {e}")
        raise

@pytest.mark.asyncio
async def test_ui_autotest_button(mock_configs, log):
    """Verify visibility and trigger of the AUTO TEST button."""
    log("Running test_ui_autotest_button")
    from src.ui.shuttle_ops import OperationPane
    try:
        with patch("src.ui.main_app.ConfigSelectionScreen.check_updates", AsyncMock()):
            with patch.object(OperationPane, "run_validate", AsyncMock()):
                app = ReaperApp()
                async with app.run_test() as pilot:
                    await wait_for_screen(app, ConfigSelectionScreen)
                    # Find the AutoTest config button
                    btn = _find_config_button(app.screen, "AutoTest")
                    assert btn is not None, "AutoTest config button not found"
                    await pilot.click(btn)
                    assert await wait_for_screen(app, ModeScreen), "Timed out waiting for ModeScreen"
                    
                    # Verify AUTO TEST button is present
                    pane = app.screen.query_one(OperationPane)
                    autotest_btn = pane.query_one("#op_autotest", Button)
                    assert autotest_btn.display is True
                    assert "AUTO TEST" in str(autotest_btn.label)
                    
                    # Mock the sequence and trigger it
                    with patch.object(OperationPane, "run_autotest_sequence", AsyncMock()) as mock_seq:
                        autotest_btn.disabled = False
                        await pilot.pause(0.1)
                        autotest_btn.focus()
                        await pilot.press("enter")
                        await pilot.pause(0.1)
                        assert mock_seq.called
                        log("test_ui_autotest_button PASSED")
    except Exception as e:
        log(f"test_ui_autotest_button FAILED: {e}")
        raise
