import sys
import math

from PyQt5.QtCore import (
    Qt,
    QTimer,
    QPointF,
    QRectF,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QPainter,
    QPixmap,
    QPen,
    QPainterPath,
    QTransform,
    QImage,
    QColor,
)
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFileDialog,
    QLineEdit,
    QScrollArea,
    QMessageBox,
    QFrame,
)


class ImageCanvas(QWidget):
    """
    Widget that:
    - shows the loaded image
    - lets you draw a curved path with the mouse
    - animates a drone + red rectangle along that path
    - emits the current observed area as a QPixmap (camera view)
    """

    cropUpdated = pyqtSignal(QPixmap)  # emitted when the observing area changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None           # QPixmap of the big (satellite) image
        self.image_qimage = None    # QImage version for pixel access
        self.path_points = []       # list of QPointF
        self.drawing = False        # is the mouse drawing a path?
        self.drone_index = 0        # current index on the path
        self.playing = False

        # rectangle size in pixels (footprint)
        self.rect_width_px = 80.0
        self.rect_height_px = 80.0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer)

    # ---------- public API ----------

    def setImage(self, pixmap: QPixmap):
        self.image = pixmap
        self.image_qimage = pixmap.toImage() if not pixmap.isNull() else None
        self.path_points.clear()
        self.drawing = False
        self.playing = False
        self.drone_index = 0
        if self.image is not None:
            self.setFixedSize(self.image.size())
        self.update()

    def hasImage(self):
        return self.image is not None

    def hasPath(self):
        return len(self.path_points) > 1

    def setRectangleSizePixels(self, w_px: float, h_px: float):
        self.rect_width_px = max(4.0, float(w_px))
        self.rect_height_px = max(4.0, float(h_px))

    def startAnimation(self, interval_ms: int):
        """
        Start moving the drone along the path.
        interval_ms is derived from camera frequency.
        """
        if not self.hasImage() or not self.hasPath():
            return

        self.drone_index = 0
        self.playing = True
        self._emit_crop()    # show first observing area immediately
        self.update()
        self.timer.start(max(1, int(interval_ms)))

    def stopAnimation(self):
        self.timer.stop()
        self.playing = False
        self.update()

    # ---------- mouse events (draw path) ----------

    def mousePressEvent(self, event):
        if not self.hasImage():
            return

        if event.button() == Qt.LeftButton:
            pos = event.pos()
            if self._point_in_image(pos):
                # Start new path
                self.path_points = [QPointF(pos)]
                self.drawing = True
                self.drone_index = 0
                self.playing = False
                self.timer.stop()
                self.update()

    def mouseMoveEvent(self, event):
        if not self.drawing or not self.hasImage():
            return
        pos = event.pos()
        if self._point_in_image(pos):
            self.path_points.append(QPointF(pos))
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drawing = False

    # ---------- painting ----------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if self.image:
            painter.drawPixmap(0, 0, self.image)

        # Draw the path
        if len(self.path_points) > 1:
            pen = QPen(Qt.yellow, 2)
            painter.setPen(pen)
            path = QPainterPath(self.path_points[0])
            for p in self.path_points[1:]:
                path.lineTo(p)
            painter.drawPath(path)

        # Draw drone + red rectangle
        if self.hasPath():
            idx = max(0, min(self.drone_index, len(self.path_points) - 1))
            drone_pos = self.path_points[idx]
            angle_deg = self._heading_angle_deg(idx)

            self._draw_drone_and_rect(painter, drone_pos, angle_deg)

    # ---------- helpers ----------

    def _point_in_image(self, p):
        if not self.image:
            return False
        return 0 <= p.x() < self.image.width() and 0 <= p.y() < self.image.height()

    def _on_timer(self):
        if not self.hasPath():
            self.stopAnimation()
            return

        if self.drone_index < len(self.path_points) - 1:
            self.drone_index += 1
            self._emit_crop()
            self.update()
        else:
            # Reached the end
            self.stopAnimation()

    def _emit_crop(self):
        """
        Create a QPixmap that contains EXACTLY what is inside
        the red rectangle (same size & orientation, like a camera view).
        """
        if not self.hasImage() or not self.hasPath() or self.image_qimage is None:
            return

        idx = max(0, min(self.drone_index, len(self.path_points) - 1))
        center = self.path_points[idx]
        angle_deg = self._heading_angle_deg(idx)
        angle_rad = math.radians(angle_deg)

        cos_t = math.cos(angle_rad)
        sin_t = math.sin(angle_rad)

        w = int(self.rect_width_px)
        h = int(self.rect_height_px)
        if w < 2 or h < 2:
            return

        result = QImage(w, h, QImage.Format_ARGB32)
        result.fill(QColor(0, 0, 0))  # black outside image bounds

        img_w = self.image_qimage.width()
        img_h = self.image_qimage.height()

        # For each pixel in the camera image, sample the big image
        for y in range(h):
            ly = y - h / 2.0
            for x in range(w):
                lx = x - w / 2.0

                # local -> world (image) coordinates
                gx = center.x() + cos_t * lx - sin_t * ly
                gy = center.y() + sin_t * lx + cos_t * ly

                ix = int(round(gx))
                iy = int(round(gy))

                if 0 <= ix < img_w and 0 <= iy < img_h:
                    result.setPixel(x, y, self.image_qimage.pixel(ix, iy))
                # else remains black

        pix = QPixmap.fromImage(result)
        self.cropUpdated.emit(pix)

    def _heading_angle_deg(self, idx: int) -> float:
        """Angle in degrees of the path at index idx."""
        if len(self.path_points) < 2:
            return 0.0

        if idx < len(self.path_points) - 1:
            p1 = self.path_points[idx]
            p2 = self.path_points[idx + 1]
        else:
            p1 = self.path_points[idx - 1]
            p2 = self.path_points[idx]

        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        if dx == 0 and dy == 0:
            return 0.0

        angle_rad = math.atan2(dy, dx)
        return math.degrees(angle_rad)

    def _draw_drone_and_rect(self, painter: QPainter, center: QPointF, angle_deg: float):
        painter.save()

        # Move and rotate coordinate system to drone center
        transform = QTransform()
        transform.translate(center.x(), center.y())
        transform.rotate(angle_deg)
        painter.setTransform(transform, True)

        # Drone icon: small triangle
        drone_path = QPainterPath()
        drone_path.moveTo(QPointF(-10, -6))
        drone_path.lineTo(QPointF(-10, 6))
        drone_path.lineTo(QPointF(15, 0))
        drone_path.closeSubpath()

        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.green)
        painter.drawPath(drone_path)

        # Observing rectangle (camera footprint)
        rect = QRectF(
            -self.rect_width_px / 2,
            -self.rect_height_px / 2,
            self.rect_width_px,
            self.rect_height_px,
        )
        pen = QPen(Qt.red, 2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.transparent)
        painter.drawRect(rect)

        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Drone Path & Observing Area Viewer")
        self.resize(1200, 800)

        central = QWidget(self)
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)

        # Left side: controls + image
        left_layout = QVBoxLayout()
        main_layout.addLayout(left_layout, stretch=3)

        # Controls grid
        controls = QGridLayout()
        left_layout.addLayout(controls)

        # Load image button
        self.btn_load = QPushButton("1. Load Image")
        self.btn_load.clicked.connect(self.load_image)
        controls.addWidget(self.btn_load, 0, 0, 1, 2)

        # Altitude
        controls.addWidget(QLabel("Drone altitude (m):"), 1, 0)
        self.altitude_edit = QLineEdit("100")
        controls.addWidget(self.altitude_edit, 1, 1)

        # Rect width/length
        controls.addWidget(QLabel("Rect width (m):"), 2, 0)
        self.rect_w_edit = QLineEdit("20")
        controls.addWidget(self.rect_w_edit, 2, 1)

        controls.addWidget(QLabel("Rect length (m):"), 3, 0)
        self.rect_h_edit = QLineEdit("20")
        controls.addWidget(self.rect_h_edit, 3, 1)

        # Zoom (replaces old Scale)
        controls.addWidget(QLabel("Zoom factor:"), 4, 0)
        self.zoom_edit = QLineEdit("1.0")
        controls.addWidget(self.zoom_edit, 4, 1)

        # Camera frequency
        controls.addWidget(QLabel("Camera frequency (Hz):"), 5, 0)
        self.freq_edit = QLineEdit("2.0")
        controls.addWidget(self.freq_edit, 5, 1)

        # Play & Stop buttons
        self.btn_play = QPushButton("10. Play")
        self.btn_play.clicked.connect(self.start_play)
        controls.addWidget(self.btn_play, 6, 0)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.stop_play)
        controls.addWidget(self.btn_stop, 6, 1)

        hint_label = QLabel("Draw the flight path: hold LEFT mouse button over the image and move.")
        hint_label.setStyleSheet("color: gray;")
        left_layout.addWidget(hint_label)

        # Scroll area with image canvas
        self.canvas = ImageCanvas()
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setWidget(self.canvas)
        left_layout.addWidget(scroll, stretch=1)

        # Right side: observing area preview (bottom-right)
        right_layout = QVBoxLayout()
        main_layout.addLayout(right_layout, stretch=1)

        right_layout.addStretch(1)

        right_layout.addWidget(QLabel("Observed area (camera view):"))
        self.preview_label = QLabel()
        self.preview_label.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.preview_label.setMinimumSize(300, 300)
        self.preview_label.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self.preview_label)

        self.canvas.cropUpdated.connect(self.update_preview)

    # ---------- slots ----------

    def load_image(self):
        fname, _ = QFileDialog.getOpenFileName(
            self,
            "Select satellite image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All files (*.*)",
        )
        if not fname:
            return
        pixmap = QPixmap(fname)
        if pixmap.isNull():
            QMessageBox.warning(self, "Error", "Failed to load image.")
            return

        self.canvas.setImage(pixmap)

    def start_play(self):
        if not self.canvas.hasImage():
            QMessageBox.warning(self, "No image", "Please load an image first.")
            return
        if not self.canvas.hasPath():
            QMessageBox.warning(self, "No path", "Draw a curved flight path on the image with the mouse.")
            return

        try:
            altitude = float(self.altitude_edit.text().strip())
            rect_w_m = float(self.rect_w_edit.text().strip())
            rect_h_m = float(self.rect_h_edit.text().strip())
            zoom = float(self.zoom_edit.text().strip())
            freq = float(self.freq_edit.text().strip())
        except ValueError:
            QMessageBox.warning(self, "Input error", "Please enter valid numeric values.")
            return

        if freq <= 0:
            QMessageBox.warning(self, "Input error", "Camera frequency must be > 0.")
            return

        # altitude now acts like scale; zoom multiplies it
        # You can think: pixels_per_meter = altitude * zoom
        if altitude <= 0:
            altitude = 1.0
        if zoom <= 0:
            zoom = 1.0

        px_per_meter = altitude * zoom
        rect_w_px = rect_w_m * px_per_meter
        rect_h_px = rect_h_m * px_per_meter

        self.canvas.setRectangleSizePixels(rect_w_px, rect_h_px)

        interval_ms = int(1000.0 / freq)
        self.canvas.startAnimation(interval_ms)

    def stop_play(self):
        self.canvas.stopAnimation()

    def update_preview(self, pixmap: QPixmap):
        if pixmap.isNull():
            return
        # Scale to fit preview label (camera view)
        scaled = pixmap.scaled(
            self.preview_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
