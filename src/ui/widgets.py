from textual.widgets import Static
import logging

logger = logging.getLogger(__name__)

class RamDisplay(Static):
    """Widget to display current RAM usage."""

    def on_mount(self) -> None:
        self.update_ram()
        self.set_interval(1.0, self.update_ram)

    def update_ram(self) -> None:
        """Fetch and display RAM usage (RSS)."""
        try:
            # RSS is in KB in /proc/self/status on Linux
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                        break
                else:
                    rss_kb = 0
            
            if rss_kb > 1024 * 1024:
                usage = f"{rss_kb / (1024 * 1024):.2f} GB"
            else:
                usage = f"{rss_kb / 1024:.2f} MB"
            
            self.update(f" RAM: [yellow]{usage}[/yellow]")
        except Exception:
            self.update(" RAM: [bold red]N/A[/bold red]")

class Footnote(Static):
    """Widget to display branding text at the bottom right."""
    def on_mount(self) -> None:
        self.update("made by [bold]RamBros[/bold]")
