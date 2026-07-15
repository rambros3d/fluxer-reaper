import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from textual.app import App
from src.ui.main_app import ReaperApp, ConfigSelectionScreen, ConfigScreen
from src.ui.mode_screen import ModeScreen
from src.core.configuration import AppConfig

import os
from textual.widgets import ListItem, ListView, Input, Button, Label



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
        # Reverting to AsyncMock to avoid WorkerError. 
        # RuntimeWarnings are now handled globally in conftest.py.
        with patch("src.ui.main_app.ConfigSelectionScreen.check_updates", AsyncMock()):
            app = ReaperApp()
            async with app.run_test() as pilot:
                await wait_for_screen(app, ConfigSelectionScreen)
                await pilot.click(ListItem)
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
                await pilot.click(ListItem)
                await wait_for_screen(app, ModeScreen)
                await pilot.click("#btn_config")
                await wait_for_screen(app, ConfigScreen)
                
                screen = app.screen
                inp = screen.query_one("#inp_source_token", Input)
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
            # run_validate is decorated with @work in shuttle_ops.py, so it MUST be a coroutine (AsyncMock)
            with patch.object(OperationPane, "run_validate", AsyncMock()):
                app = ReaperApp()
                async with app.run_test() as pilot:
                    await wait_for_screen(app, ConfigSelectionScreen)
                    await pilot.click(ListItem)
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
                    # Find the AutoTest item in the list
                    lv = app.screen.query_one(ListView)
                    autotest_index = -1
                    for idx, item in enumerate(lv.children):
                        if item.name == "AutoTest":
                            autotest_index = idx
                            break
                    
                    assert autotest_index != -1, "AutoTest profile not found in ListView"
                    target_item = lv.children[autotest_index]
                    await pilot.click(target_item)
                    assert await wait_for_screen(app, ModeScreen), "Timed out waiting for ModeScreen"
                    
                    # Verify button is present
                    pane = app.screen.query_one(OperationPane)
                    btn = pane.query_one("#op_autotest", Button)
                    assert btn.display is True
                    assert "AUTO TEST" in str(btn.label)
                    
                    # Mock the sequence and trigger it
                    with patch.object(OperationPane, "run_autotest_sequence", AsyncMock()) as mock_seq:
                        btn.disabled = False
                        await pilot.pause(0.1)
                        btn.focus()
                        await pilot.press("enter")
                        await pilot.pause(0.1)
                        assert mock_seq.called
                        log("test_ui_autotest_button PASSED")
    except Exception as e:
        log(f"test_ui_autotest_button FAILED: {e}")
        raise
