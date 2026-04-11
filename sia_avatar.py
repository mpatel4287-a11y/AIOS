#!/usr/bin/env python3
import sys, math, time
from PyQt5.QtWidgets import QApplication, QWidget, QMenu
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QRectF, QPointF
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QFont

class StdinReader(QThread):
    expr_signal = pyqtSignal(str)
    def run(self):
        for line in sys.stdin:
            line = line.strip()
            if line.startswith("EXPR:"):
                self.expr_signal.emit(line[5:])

class SiaAvatar(QWidget):
    EXPRESSIONS = {
        "idle":      {"eyes":"normal",  "mouth":"smile",  "color":"#9B59B6"},
        "thinking":  {"eyes":"squint",  "mouth":"hmm",    "color":"#3498DB"},
        "speaking":  {"eyes":"happy",   "mouth":"talk",   "color":"#E74C3C"},
        "happy":     {"eyes":"happy",   "mouth":"big",    "color":"#2ECC71"},
        "listening": {"eyes":"wide",    "mouth":"smile",  "color":"#F39C12"},
        "working":   {"eyes":"focus",   "mouth":"line",   "color":"#1ABC9C"},
        "sleeping":  {"eyes":"closed",  "mouth":"smile",  "color":"#7F8C8D"},
        "excited":   {"eyes":"stars",   "mouth":"big",    "color":"#E91E63"},
    }

    def __init__(self):
        super().__init__()
        self.expr = "idle"
        self.blink_open = True
        self.mouth_open = False
        self.t = 0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # Position window on screen
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen.width()-135, screen.height()-190, 120, 150)

        # Background processing wrapper
        self.reader = StdinReader()
        self.reader.expr_signal.connect(self.set_expr)
        self.reader.start()

        # Timers for animation
        self.anim_timer = QTimer()
        self.anim_timer.timeout.connect(self.animate_pulse)
        self.anim_timer.start(70)

        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self.animate_blink)
        self.blink_timer.start(3200)

        self.mouth_timer = QTimer()
        self.mouth_timer.timeout.connect(self.animate_mouth)
        self.mouth_timer.start(140)

    def set_expr(self, name):
        if name in self.EXPRESSIONS:
            self.expr = name
            self.update()

    def animate_pulse(self):
        self.t += 1
        if self.expr in ("speaking", "listening", "excited"):
            self.update()

    def animate_blink(self):
        self.blink_open = False
        self.update()
        QTimer.singleShot(120, self.restore_blink)

    def restore_blink(self):
        self.blink_open = True
        self.update()

    def animate_mouth(self):
        if self.expr == "speaking":
            self.mouth_open = not self.mouth_open
            self.update()
        else:
            self.mouth_open = False
            if self.expr != "speaking":
                self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self.drag_pos)
            event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Hide", self.hide)
        menu.addAction("Show", self.show)
        menu.addSeparator()
        menu.addAction("Quit", QApplication.instance().quit)
        menu.exec_(event.globalPos())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        ex  = self.EXPRESSIONS[self.expr]
        col = QColor(ex["color"])
        cx, cy = 60, 65

        # Outer glow rings
        for r, w in [(48, 1), (44, 1.5), (40, 2)]:
            painter.setPen(QPen(col, w))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(cx-r, cy-r, r*2, r*2))

        # Head background
        painter.setBrush(QBrush(QColor("#0d0020")))
        painter.setPen(QPen(col, 2))
        painter.drawEllipse(QRectF(cx-36, cy-36, 36*2, 36*2))

        # Pulse ring when speaking
        if self.expr == "speaking":
            r2 = int(44 + 5*math.sin(self.t*0.4))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(col, 1))
            painter.drawEllipse(QRectF(cx-r2, cy-r2, r2*2, r2*2))

        eyes = ex["eyes"]
        blink = not self.blink_open

        # Eyes
        painter.setPen(Qt.NoPen)
        if blink or eyes == "closed":
            painter.setPen(QPen(col, 2))
            painter.drawLine(cx-18, cy-8, cx-8, cy-8)
            painter.drawLine(cx+8, cy-8, cx+18, cy-8)
        elif eyes == "normal":
            painter.setBrush(col)
            painter.drawEllipse(QRectF(cx-20, cy-15, 12, 12))
            painter.drawEllipse(QRectF(cx+8, cy-15, 12, 12))
            painter.setBrush(QColor("white"))
            painter.drawEllipse(QRectF(cx-17, cy-12, 5, 5))
            painter.drawEllipse(QRectF(cx+12, cy-12, 5, 5))
        elif eyes == "happy":
            painter.setPen(QPen(col, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(QRectF(cx-22, cy-16, 16, 12), 0 * 16, 180 * 16)
            painter.drawArc(QRectF(cx+6, cy-16, 16, 12), 0 * 16, 180 * 16)
        elif eyes == "wide":
            painter.setBrush(col)
            painter.drawEllipse(QRectF(cx-22, cy-17, 16, 16))
            painter.drawEllipse(QRectF(cx+6, cy-17, 16, 16))
            painter.setBrush(QColor("white"))
            painter.drawEllipse(QRectF(cx-19, cy-14, 8, 8))
            painter.drawEllipse(QRectF(cx+11, cy-14, 8, 8))
        elif eyes == "squint":
            painter.setPen(QPen(col, 3))
            painter.drawLine(cx-20, cy-10, cx-8, cy-6)
            painter.drawLine(cx+8, cy-6, cx+20, cy-10)
        elif eyes == "focus":
            painter.setBrush(col)
            painter.drawEllipse(QRectF(cx-19, cy-14, 10, 10))
            painter.drawEllipse(QRectF(cx+9, cy-14, 10, 10))
            painter.setPen(QPen(col, 2))
            painter.drawLine(cx-20, cy-17, cx-8, cy-15)
            painter.drawLine(cx+8, cy-15, cx+20, cy-17)
        elif eyes == "stars":
            painter.setBrush(col)
            for ox, oy in [(cx-14, cy-9), (cx+14, cy-9)]:
                poly = QPolygonF()
                for i in range(10):
                    ang = math.pi/2 + i*math.pi/5
                    r2  = 7 if i%2==0 else 3
                    poly.append(QPointF(ox+r2*math.cos(ang), oy-r2*math.sin(ang)))
                painter.drawPolygon(poly)

        # Mouth
        mouth = ex["mouth"]
        painter.setPen(Qt.NoPen)
        if mouth == "smile":
            painter.setPen(QPen(col, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(QRectF(cx-14, cy+5, 28, 15), 200 * 16, 140 * 16)
        elif mouth == "big":
            painter.setBrush(col)
            painter.drawChord(QRectF(cx-18, cy+3, 36, 19), 200 * 16, 140 * 16)
            painter.setPen(QPen(QColor("#0d0020"), 3))
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(QRectF(cx-15, cy+9, 30, 11), 200 * 16, 140 * 16)
        elif mouth == "talk":
            h = 12 if self.mouth_open else 5
            painter.setBrush(col)
            painter.drawEllipse(QRectF(cx-10, cy+8, 20, h))
        elif mouth == "hmm":
            painter.setPen(QPen(col, 2))
            painter.drawLine(cx-10, cy+14, cx+10, cy+14)
        elif mouth == "line":
            painter.setPen(QPen(col, 2))
            painter.drawLine(cx-12, cy+12, cx+12, cy+12)

        # Name
        font = QFont("Helvetica", 9, QFont.Bold)
        painter.setFont(font)
        painter.setPen(col)
        painter.drawText(QRectF(0, 120, 120, 25), Qt.AlignCenter, "S I A")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    avatar = SiaAvatar()
    avatar.show()
    sys.exit(app.exec_())