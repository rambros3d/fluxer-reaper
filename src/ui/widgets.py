import psutil
import time
from textual.widgets import Static
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
            
            delta_time = curr_time - self._prev_time
            if delta_time <= 0:
                delta_time = 1.0
                
            sent_speed = (curr_net_io.bytes_sent - self._prev_net_io.bytes_sent) / delta_time
            recv_speed = (curr_net_io.bytes_recv - self._prev_net_io.bytes_recv) / delta_time
            
            # Simple combined speed display or separately
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
