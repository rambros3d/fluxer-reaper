import pytest
import shutil
import yaml
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from src.core.backup_database import BackupDatabase
from src.core.configuration import AppConfig

LOG_FILE = (Path(__file__).parent.parent / ".reaper_tests.log").resolve()

def _log(msg: str):
    """Write a message to the shared test log file."""
    with open(LOG_FILE, "a") as f:
        f.write(f"{msg}\n")

@pytest.fixture
def log():
    """Fixture to provide the logging function to tests."""
    return _log

def pytest_configure(config):
    """Register global warning filters and marks."""
    # Register marks if needed
    config.addinivalue_line("markers", "asyncio: mark test as asyncio")
    
    # Silence benign async mock and ResourceWarnings globally
    config.addinivalue_line("filterwarnings", "ignore::RuntimeWarning")
    config.addinivalue_line("filterwarnings", "ignore::ResourceWarning")

def pytest_sessionstart(session):
    """Clear the log file at the beginning of the test session."""
    with open(LOG_FILE, "w") as f:
        f.write("--- Reaper Test Session Started ---\n")

def pytest_report_header(config):
    """Print data source status to the console header."""
    test_data_dir = (Path(__file__).parent.parent / "ReaperFiles-AutoTest").resolve()
    if test_data_dir.exists():
        return f"[DATA_SOURCE] Automated Test Directory: FOUND ({test_data_dir.name})"
    else:
        return "[DATA_SOURCE] Automated Test Directory: NOT FOUND (Falling back to MOCKS)"

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Hook to catch test results and log them to .reaper_tests.log."""
    outcome = yield
    rep = outcome.get_result()
    
    # We only log on the 'call' phase (the actual test run)
    if rep.when == "call":
        status = rep.outcome.upper()
        _log(f"RESULT: {item.nodeid} -> {status}")
        if rep.failed:
            _log(f"--- FAILURE DETAILS for {item.name} ---\n{rep.longreprtext}\n---------------------")
    elif not rep.passed:
        # Catch setup/teardown failures
        _log(f"ERROR in {item.nodeid} [{rep.when}]: {rep.outcome.upper()}")
        if rep.failed:
            _log(f"--- ERROR DETAILS ---\n{rep.longreprtext}\n---------------------")

def pytest_warning_recorded(warning_message, when, nodeid, location):
    """Hook to catch and log warnings to .reaper_tests.log."""
    msg = f"WARNING: {warning_message.message}"
    if nodeid:
        msg = f"WARNING in {nodeid} [{when}]: {warning_message.message}"
    _log(msg)

@pytest.fixture
def test_data_dir():
    return (Path(__file__).parent.parent / "ReaperFiles-AutoTest").resolve()

@pytest.fixture
def reaper_config(test_data_dir):
    config_path = test_data_dir / "reaper_config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
        return data
    # Fallback mock config
    return {
        "source_bot_token": "mock_source_token",
        "source_server_id": "123456789012345678",
        "tool_mode": "backup_transfer",
        "target_platform": "fluxer",
        "fluxer_bot_token": "mock_fluxer_token",
        "fluxer_server_id": "987654321098765432",
        "stoat_bot_token": "mock_stoat_token",
        "stoat_server_id": "MOCK_STOAT_COMMUNITY",
        "anonymize_users": False,
        "log_level": "DEBUG"
    }

@pytest.fixture
def temp_db(test_data_dir, tmp_path, reaper_config):
    # If the real test data exists, use it
    if test_data_dir.exists():
        target_id = reaper_config.get("fluxer_server_id") if reaper_config.get("target_platform") == "fluxer" else reaper_config.get("stoat_server_id")
        db_files = list(test_data_dir.glob(f"*-{target_id}.db"))
        if not db_files:
            db_files = list(test_data_dir.glob("*.db"))
            
        if db_files:
            original_db = db_files[0]
            temp_db_path = tmp_path / "test_migration.db"
            print(f"[DATA_SOURCE] USE_SAMPLE_DB: {original_db.name}")
            _log(f"[DATA_SOURCE] USE_SAMPLE_DB: {original_db.name}")
            shutil.copy(original_db, temp_db_path)
            return temp_db_path

    # Fallback: Create an empty mock database with the required schema
    temp_db_path = tmp_path / "mock_migration.db"
    print("[DATA_SOURCE] USE_MOCK_DB: Fallback to empty schema")
    _log("[DATA_SOURCE] USE_MOCK_DB: Fallback to empty schema")
    db = BackupDatabase(temp_db_path)
    # The BackupDatabase constructor already initializes the schema
    return temp_db_path

@pytest.fixture
def backup_db(temp_db):
    db = BackupDatabase(temp_db)
    yield db
    # No explicit close needed for now as it's a persistent connection

@pytest.fixture
def backup_reader(test_data_dir, reaper_config, tmp_path):
    sid = reaper_config.get("source_server_id")
    backup_path = test_data_dir / f"DISCORD_BACKUP-{sid}"
    
    if not test_data_dir.exists() or not backup_path.exists():
        # Fallback: create mock backup structure
        mock_path = tmp_path / f"DISCORD_BACKUP-{sid}"
        print(f"[DATA_SOURCE] USE_MOCK_BACKUP: {mock_path.name}")
        _log(f"[DATA_SOURCE] USE_MOCK_BACKUP: {mock_path.name}")
        mock_path.mkdir(parents=True, exist_ok=True)
        db_path = mock_path / "backup.db"
        db = BackupDatabase(db_path)
        # Populate with minimal mock data for BackupReader to work
        db._conn.execute("INSERT OR IGNORE INTO guild_profile (id, name) VALUES (?, ?)", (int(sid), "Mock Guild"))
        db._conn.execute("INSERT OR IGNORE INTO channels (id, name, type) VALUES (?, ?, ?)", (123, "mock-channel", 0))
        db._conn.commit()
        from src.core.backup_reader import BackupReader
        return BackupReader(mock_path)
        
    print(f"[DATA_SOURCE] USE_SAMPLE_BACKUP: {backup_path.name}")
    _log(f"[DATA_SOURCE] USE_SAMPLE_BACKUP: {backup_path.name}")
    from src.core.backup_reader import BackupReader
    return BackupReader(backup_path)

@pytest.fixture
def mock_discord_reader():
    reader = MagicMock()
    reader.guild = MagicMock()
    reader.fetch_message_history = AsyncMock()
    reader.download_attachment = AsyncMock(return_value=b"fake_data")
    reader.download_sticker = AsyncMock(return_value=b"fake_sticker_data")
    return reader

@pytest.fixture
def mock_fluxer_writer():
    writer = MagicMock()
    writer.send_message = AsyncMock(return_value="fluxer_msg_123")
    writer.send_marker = AsyncMock()
    return writer

@pytest.fixture
def mock_stoat_writer():
    writer = MagicMock()
    writer.send_message = AsyncMock(return_value="stoat_msg_123")
    writer.send_marker = AsyncMock()
    return writer
