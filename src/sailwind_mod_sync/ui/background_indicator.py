from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


class Spinner(QWidget):
    """Small rotating arc, drawn in the palette's highlight color."""

    STEP_DEGREES = 30
    INTERVAL_MS = 80

    def __init__(self, parent: QWidget | None = None, size: int = 14) -> None:
        super().__init__(parent)
        self._angle = 0
        self._size = size
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(size, size)

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def is_spinning(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _advance(self) -> None:
        self._angle = (self._angle + self.STEP_DEGREES) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.palette().color(QPalette.ColorRole.Highlight), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = QRectF(1.5, 1.5, self._size - 3, self._size - 3)
        # Qt angles are in 1/16ths of a degree, counter-clockwise; spin clockwise.
        painter.drawArc(rect, -self._angle * 16, 270 * 16)


class BackgroundIndicator(QWidget):
    """Status-bar spinner and label shown while quiet background work runs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spinner = Spinner(self)
        self.label = QLabel(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)
        layout.addWidget(self.spinner)
        layout.addWidget(self.label)
        self.hide()

    def set_tasks(self, tasks: list[str]) -> None:
        """Show the spinner with the running task names, or hide it when the list is empty."""
        if not tasks:
            self.spinner.stop()
            self.label.clear()
            self.setToolTip("")
            self.hide()
            return
        self.label.setText(tasks[0] if len(tasks) == 1 else f"{tasks[0]} (+{len(tasks) - 1} more)")
        self.setToolTip("Running in the background:\n" + "\n".join(tasks))
        self.spinner.start()
        self.show()
