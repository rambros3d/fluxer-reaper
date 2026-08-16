import psutil
import time
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll, Container, Center
from textual.widgets import Button, Label, Input, Static
import logging

logger = logging.getLogger(__name__)

class RamDisplay(Static):
    """Widget to display current RAM usage and Network speed."""

    def on_mount(self) -> None:
        self._prev_net_io = psutil.net_io_counters()
        self._prev_time = time.time()
        self.update_stats()
        self.set_interval(1.0, self.update_stats)

    def _format_speed(self, bytes_per_sec: float) -> str:
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.1f} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.1f} KB/s"
        else:
            return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"

    def update_stats(self) -> None:
        """Fetch and display RAM and Network speed."""
        try:
            # 1. RAM Usage
            process = psutil.Process()
            rss = process.memory_info().rss
            if rss > 1024 * 1024 * 1024:
                ram_usage = f"{rss / (1024 * 1024 * 1024):.2f} GB"
            else:
                ram_usage = f"{rss / (1024 * 1024):.2f} MB"

            # 2. Network Speed
            curr_net_io = psutil.net_io_counters()
            curr_time = time.time()
            
            # Check if this is the first call or too soon
            if not hasattr(self, "_prev_net_io") or self._prev_net_io is None:
                self._prev_net_io = curr_net_io
                self._prev_time = curr_time
                self.update(f" RAM: [yellow]{ram_usage}[/yellow] | Net: [cyan]0.0 B/s[/cyan]")
                return

            delta_time = curr_time - self._prev_time
            if delta_time < 0.5: # Don't calculate for very small intervals to avoid spikes
                return
                
            sent_speed = (curr_net_io.bytes_sent - self._prev_net_io.bytes_sent) / delta_time
            recv_speed = (curr_net_io.bytes_recv - self._prev_net_io.bytes_recv) / delta_time
            
            # Combined speed display
            speed_str = self._format_speed(sent_speed + recv_speed)
            
            self._prev_net_io = curr_net_io
            self._prev_time = curr_time

            self.update(f" RAM: [yellow]{ram_usage}[/yellow] | Net: [cyan]{speed_str}[/cyan]")
        except Exception as e:
            logger.error(f"Error updating stats: {e}")
            self.update(" Stats: [bold red]N/A[/bold red]")

class Footnote(Static):
    """Widget to display branding text at the bottom right."""
    def on_mount(self) -> None:
        self.update("made by [bold]RamBros[/bold]")


# ---------------------------------------------------------------------------
# ClipboardInput – Input field with Copy / Paste / Clear buttons
# ---------------------------------------------------------------------------

class ClipboardInput(Horizontal):
    """An Input field with tiny icon buttons on the right side.

    No outer border.  The Input itself uses the default Textual styling.
    Buttons are compact and translucent, becoming visible on hover.

    Example::

        yield ClipboardInput(placeholder="Paste token here", password=True, id="inp_token")
    """

    DEFAULT_CSS = """
    ClipboardInput {
        height: auto;
        align: center middle;
        padding: 0;
    }
    ClipboardInput Input {
        width: 1fr;
    }
    ClipboardInput .preview {
        width: auto;
        color: $text 50%;
        padding: 0 1;
        text-style: bold;
        content-align: center middle;
    }
    ClipboardInput Button {
        width: 4;
        min-width: 4;
        border: none;
        background: transparent;
        color: $text 50%;
        padding: 0 1;
        content-align: center middle;
    }
    ClipboardInput Button:hover {
        color: $text;
        background: $boost 40%;
    }
    """

    def __init__(
        self,
        value: str = "",
        placeholder: str = "",
        password: bool = False,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._default = value
        self._placeholder = placeholder
        self._pwd = password
        self._token_prefix = ""

    def compose(self) -> ComposeResult:
        wid = self.id or "clipinput"
        yield Input(
            value=self._default,
            placeholder=self._placeholder,
            password=self._pwd,
            id=f"{wid}_field",
        )
        yield Label("", id=f"{wid}_preview", classes="preview")
        yield Button("\U0001F4C4", id=f"{wid}_copy", tooltip="Copy")
        yield Button("\U0001f4cb", id=f"{wid}_paste", tooltip="Paste")
        yield Button("\u2716", id=f"{wid}_clear", tooltip="Clear")

    def on_mount(self) -> None:
        self._sync_preview()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._sync_preview()

    def _sync_preview(self) -> None:
        """Show first 4 chars of the token as a preview label."""
        try:
            preview = self.query_one(f"#{(self.id or 'clipinput')}_preview", Label)
        except Exception:
            return
        token = self._input.value
        if token and len(token) > 4:
            preview.update(f"{token[:4]}…")
            preview.display = True
        else:
            preview.display = False

    @property
    def value(self) -> str:
        return self._input.value

    @value.setter
    def value(self, val: str) -> None:
        self._input.value = val
        self._sync_preview()

    @property
    def _input(self) -> Input:
        return self.query_one(Input)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.endswith("_copy"):
            text = self._input.value
            if text:
                self.app.copy_to_clipboard(text)
                self.app.notify("Copied", severity="information", timeout=1.5)
        elif bid.endswith("_paste"):
            text = self._read_system_clipboard()
            if text:
                self._input.value = text
                self._input.focus()
            else:
                self.app.notify("Nothing to paste", severity="warning", timeout=1.5)
        elif bid.endswith("_clear"):
            self._input.value = ""
            self._input.focus()

    # -- cross-platform system clipboard -----------------------------------

    @staticmethod
    def _read_system_clipboard() -> str:
        """Read from the system clipboard via multiple backends.

        Tries (in order): tkinter → pyperclip → subprocess (xclip / wl-paste /
        powershell).  Returns empty string if all fail.
        """
        # 1. tkinter — built into Python, works on all platforms
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            return text or ""
        except Exception:
            pass

        # 2. pyperclip — common third-party lib
        try:
            import pyperclip
            return pyperclip.paste() or ""
        except Exception:
            pass

        # 3. subprocess — platform-specific fallbacks
        import subprocess
        import shutil
        try:
            if shutil.which("wl-paste"):
                return subprocess.check_output(["wl-paste"], text=True).strip()
            if shutil.which("xclip"):
                return subprocess.check_output(
                    ["xclip", "-selection", "clipboard", "-o"], text=True
                ).strip()
            if shutil.which("xsel"):
                return subprocess.check_output(
                    ["xsel", "--clipboard", "--output"], text=True
                ).strip()
            if shutil.which("powershell"):
                return subprocess.check_output(
                    ["powershell", "-command", "Get-Clipboard"], text=True
                ).strip()
        except Exception:
            pass

        return ""
