"""
Shared modals used by backup and shuttle operations.
"""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Label, Input, ProgressBar, RichLog, Rule, RadioButton
from textual.screen import ModalScreen


# ---------------------------------------------------------------------------
# ProgressModal – unified progress / log display
# ---------------------------------------------------------------------------

class ProgressModal(ModalScreen[None]):
    """Modal to display progress for any long-running operation."""

    def compose(self) -> ComposeResult:
        with Vertical(id="progress_dialog"):
            yield Label("Operation Status", id="progress_status")
            yield ProgressBar(total=None, show_eta=False, id="progress_bar")
            yield RichLog(id="progress_log", highlight=True, markup=True)
            yield Button("Close", id="btn_close_progress", disabled=True)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_close_progress":
            self.dismiss(None)

    def write(self, message: str):
        self.query_one("#progress_log", RichLog).write(message)

    # alias used by backup code
    write_to_log = write

    def set_status(self, status: str):
        self.query_one("#progress_status", Label).update(status)

    def set_progress(self, current: int, total: int):
        bar = self.query_one("#progress_bar", ProgressBar)
        bar.update(total=total, progress=current)

    def allow_close(self):
        btn = self.query_one("#btn_close_progress", Button)
        btn.disabled = False
        btn.variant = "success"
        self.query_one("#progress_bar", ProgressBar).update(total=100, progress=100)


# ---------------------------------------------------------------------------
# ConfirmModal – simple yes / no
# ---------------------------------------------------------------------------

class ConfirmModal(ModalScreen[bool]):
    """Simple Yes / No confirmation modal."""

    def __init__(self, message: str, danger: bool = False):
        super().__init__()
        self._message = message
        self._danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_dialog"):
            yield Label(self._message, id="confirm_msg")
            with Horizontal(id="confirm_buttons"):
                yield Button("Confirm", variant="error" if self._danger else "success", id="btn_yes")
                yield Button("Cancel", variant="primary", id="btn_no")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "btn_yes")


# ---------------------------------------------------------------------------
# SubMenuModal – generic labelled-button list
# ---------------------------------------------------------------------------

class SubMenuModal(ModalScreen[str]):
    """A generic sub-menu modal that presents a list of labelled buttons."""

    def __init__(self, title: str, options: list[tuple[str, str, str]]):
        """options: list of (button_id, label, variant)"""
        super().__init__()
        self._title = title
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="submenu_dialog"):
            yield Label(self._title, id="submenu_title")
            for btn_id, label, variant in self._options:
                yield Button(label, id=btn_id, variant=variant)
            yield Rule()
            yield Button("Cancel", id="btn_cancel_sub")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_cancel_sub":
            self.dismiss(None)
        else:
            self.dismiss(event.button.id)


# ---------------------------------------------------------------------------
# ChannelPickerModal – single-channel selection (shuttle)
# ---------------------------------------------------------------------------

class ChannelPickerModal(ModalScreen[int]):
    """Modal listing Discord channels for single-channel selection."""

    def __init__(self, channels: list, categories: dict, label: str = "Select Channel"):
        super().__init__()
        self._channels = channels
        self._categories = categories
        self._label = label

    def compose(self) -> ComposeResult:
        with Vertical(id="chanpick_dialog"):
            yield Label(self._label, id="chanpick_title")
            with VerticalScroll(id="chanpick_scroll"):
                cat_grouped: dict[int | None, list] = {}
                for c in self._channels:
                    cat_id = getattr(c, "category_id", None) if not isinstance(c, dict) else c.get("parent_id")
                    cat_grouped.setdefault(cat_id, []).append(c)

                for cat_id in sorted(cat_grouped, key=lambda k: self._categories.get(k, "") if k else ""):
                    if cat_id is not None and cat_id in self._categories:
                        yield Label(f"[cyan]{self._categories[cat_id]}[/cyan]", classes="category_header")
                    for c in cat_grouped[cat_id]:
                        if isinstance(c, dict):
                            name = c.get("name", "Unnamed")
                            cid = c.get("id")
                        else:
                            name = c.name
                            cid = c.id
                        yield RadioButton(name, value=False, id=f"chpk_{cid}")

            with Horizontal(id="chanpick_buttons"):
                yield Button("Select", variant="success", id="btn_pick_ok")
                yield Button("Cancel", id="btn_pick_cancel")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_pick_cancel":
            self.dismiss(None)
        elif event.button.id == "btn_pick_ok":
            for rb in self.query(RadioButton):
                if rb.value and rb.id and rb.id.startswith("chpk_"):
                    self.dismiss(int(rb.id.split("_", 1)[1]))
                    return
            # Nothing selected
            return


# ---------------------------------------------------------------------------
# ChannelSelectModal – multi-channel selection with sync/force (backup)
# ---------------------------------------------------------------------------

class ChannelSelectModal(ModalScreen[dict]):
    """Modal for selecting channels using a simple checkbox list."""

    def __init__(self, channels: list, categories: dict, backed_up_ids: set, any_found: bool):
        super().__init__()
        # Group channels by category
        self.channels_by_category = {}
        for c in channels:
            cat_id = getattr(c, 'category_id', None)
            if cat_id not in self.channels_by_category:
                self.channels_by_category[cat_id] = []
            self.channels_by_category[cat_id].append(c)

        self.categories = categories  # id -> name
        self.backed_up_ids = backed_up_ids
        self.any_found = any_found

    def compose(self) -> ComposeResult:
        with Vertical(id="channel_dialog"):
            yield Label("Select Channels to Backup", id="chan_title")

            with VerticalScroll(id="channel_list_scroll"):
                cat_ids = sorted(
                    [k for k in self.channels_by_category.keys() if k is not None],
                    key=lambda k: self.categories.get(k, ""),
                )

                # No category channels
                if None in self.channels_by_category:
                    for c in sorted(self.channels_by_category[None], key=lambda x: x.position if hasattr(x, 'position') else 0):
                        label = f"{c.name}"
                        color = "green" if c.id in self.backed_up_ids else "white"
                        yield RadioButton(f"[{color}]{label}[/]", value=False, id=f"chan_{c.id}")

                for cat_id in cat_ids:
                    cat_name = self.categories.get(cat_id, "Unknown Category")
                    yield Label(f"[cyan]{cat_name}[/cyan]", classes="category_header")
                    for c in sorted(self.channels_by_category[cat_id], key=lambda x: x.position if hasattr(x, 'position') else 0):
                        label = f"{c.name}"
                        color = "green" if c.id in self.backed_up_ids else "white"
                        yield RadioButton(f"[{color}]{label}[/]", value=False, id=f"chan_{c.id}")

            with Horizontal(id="select_all_buttons"):
                yield Button("Select All", id="btn_all")
                yield Button("Deselect All", id="btn_none")

            with Horizontal(id="confirm_buttons"):
                if self.any_found:
                    yield Label("Existing backups found:", classes="label_warning")
                    yield Button("Sync", variant="success", id="btn_sync")
                    yield Button("Force Overwrite", variant="error", id="btn_force")
                else:
                    yield Button("Backup", variant="success", id="btn_backup")
                yield Button("Cancel", id="btn_cancel_chan")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_all":
            for cb in self.query(RadioButton):
                cb.value = True
        elif event.button.id == "btn_none":
            for cb in self.query(RadioButton):
                cb.value = False
        elif event.button.id in ["btn_sync", "btn_force", "btn_backup"]:
            selected = []
            for cb in self.query(RadioButton):
                if cb.value and cb.id and cb.id.startswith("chan_"):
                    chan_id = int(cb.id.split("_")[1])
                    selected.append(chan_id)
            if not selected:
                return

            force = event.button.id == "btn_force"
            self.dismiss({"channels": selected, "force": force})
        elif event.button.id == "btn_cancel_chan":
            self.dismiss(None)
