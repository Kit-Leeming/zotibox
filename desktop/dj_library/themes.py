"""Theme tokens, continuous selection painting and an HSV/harmony editor."""
import copy
import math

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QBrush, QLinearGradient, QPalette, QIcon, QPixmap
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QSlider, QLineEdit, QLabel, QPushButton, QDialogButtonBox, QStyledItemDelegate, QStyleOptionViewItem, QStyle)

ROLES = {"background": "Background", "sidebar": "Sidebar", "surface": "Lists and panels", "alternate": "Alternate rows",
         "foreground": "Text", "muted": "Secondary text", "accent": "Accent", "selection": "Selected row", "selection_text": "Selected text", "border": "Borders"}
BASE = {"background": "#181a1d", "sidebar": "#111315", "surface": "#202328", "alternate": "#24282e",
        "foreground": "#e7e9ed", "muted": "#a2aab6", "accent": "#84c9a1", "selection": "#84c9a1", "selection_text": "#101712", "border": "#3a4048"}


def make_theme(overrides=None):
    colours = dict(BASE, **(overrides or {}))
    return {key: {"start": value, "end": value, "gradient": False, "angle": 0} for key, value in colours.items()}


PRESETS = {"Charcoal / Mint": make_theme(), "Graphite / Blue": make_theme({"accent": "#79b7ee", "selection": "#79b7ee", "selection_text": "#101820"}),
           "Dark / Rose": make_theme({"accent": "#dfa1b6", "selection": "#dfa1b6", "selection_text": "#28151e"}),
           "Light / Indigo": make_theme({"background": "#eceef4", "sidebar": "#e1e4ed", "surface": "#ffffff", "alternate": "#f2f4f9",
               "foreground": "#202431", "muted": "#525d70", "accent": "#5267d0", "selection": "#5267d0", "selection_text": "#ffffff", "border": "#b9c1d0"})}

for name, background, accent in [("Midnight / Violet", "#161625", "#b59afa"),
        ("Carbon / Orange", "#202020", "#efab76"), ("Forest / Gold", "#15231e", "#e8c878"),
        ("Paper / Teal", "#f2efe7", "#277f83")]:
    PRESETS[name] = make_theme({"background": background, "accent": accent})
for name, start, end, accent in [("Northern lights", "#12252c", "#2b1942", "#8cd9c5"),
        ("Dusk", "#25233d", "#442529", "#edb296"),
        ("Ocean", "#101c30", "#153c42", "#8dcfee"),
        ("Cloud", "#eff2fc", "#d9e9e5", "#5460b5")]:
    theme = make_theme({"background": start, "accent": accent})
    theme["background"].update(end=end, gradient=True, angle=45)
    PRESETS[name] = theme


def valid_theme(value):
    theme = make_theme()
    for key in ROLES:
        token = (value or {}).get(key, {})
        for colour in ["start", "end"]:
            if QColor(token.get(colour, "")).isValid():
                theme[key][colour] = QColor(token[colour]).name()
        theme[key]["gradient"] = bool(token.get("gradient", False)) and key not in {"foreground", "muted", "selection_text", "border"}
        theme[key]["angle"] = int(token.get("angle", 0)) % 360
    # Only two user colour roles; derive readable text and consistent surfaces.
    for key in ["sidebar", "surface", "alternate"]:
        theme[key] = copy.deepcopy(theme["background"])
        first,last=QColor(theme["background"]["start"]),QColor(theme["background"]["end"])
        if not theme["background"]["gradient"]:last=first
        colour=QColor.fromRgbF((first.redF()+last.redF())/2,(first.greenF()+last.greenF())/2,(first.blueF()+last.blueF())/2)
        theme[key].update(start=colour.name(),end=colour.name(),gradient=False)
    theme["selection"] = copy.deepcopy(theme["accent"])
    theme["accent"]["gradient"]=False
    theme["selection"]["gradient"]=False
    bg = QColor(theme["background"]["start"])
    light = luminance(bg) > .179
    for key, colour in {"foreground": "#202431" if light else "#e7e9ed",
            "muted": "#50596b" if light else "#aeb7c6",
            "border": "#aab4c3" if light else "#46505c",
            "selection_text": "#111722" if luminance(QColor(theme["accent"]["start"]))>.179 else "#ffffff"}.items():
        theme[key] = make_theme({key:colour})[key]
    theme["font_size"] = max(11,min(20,int((value or {}).get("font_size",13))))
    return theme


def luminance(colour):
    channels=[colour.redF(),colour.greenF(),colour.blueF()]
    linear=[c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in channels]
    return sum(c*w for c,w in zip(linear,[.2126,.7152,.0722]))


def theme_sample(theme):
    theme=valid_theme(theme)
    image=QPixmap(150,65)
    painter=QPainter(image)
    painter.fillRect(image.rect(),selection_brush(theme["background"],QRectF(image.rect())))
    painter.fillRect(12,15,22,35,QColor(theme["accent"]["start"]))
    painter.setPen(QPen(QColor(theme["foreground"]["start"]),3))
    for y,width in [(20,84),(32,62),(44,72)]:
        painter.drawLine(47,y,47+width,y)
    painter.end()
    return QIcon(image)


def css(token):
    if not token["gradient"]:
        return token["start"]
    angle = math.radians(token["angle"])
    dx, dy = math.cos(angle) / 2, math.sin(angle) / 2
    return f"qlineargradient(x1:{.5-dx},y1:{.5-dy},x2:{.5+dx},y2:{.5+dy},stop:0 {token['start']},stop:1 {token['end']})"


def selection_brush(token, rect):
    if not token["gradient"]:
        return QBrush(QColor(token["start"]))
    angle = math.radians(token["angle"])
    centre = rect.center()
    vector = QPointF(math.cos(angle) * rect.width() / 2, math.sin(angle) * rect.height() / 2)
    gradient = QLinearGradient(centre - vector, centre + vector)
    gradient.setColorAt(0, QColor(token["start"]))
    gradient.setColorAt(1, QColor(token["end"]))
    return QBrush(gradient)


class RowDelegate(QStyledItemDelegate):
    def __init__(self, view, get_theme):
        super().__init__(view)
        self.view, self.get_theme = view, get_theme

    def paint(self, painter, option, index):
        if not option.state & QStyle.StateFlag.State_Selected:
            option.state &= ~QStyle.StateFlag.State_HasFocus
            return super().paint(painter, option, index)
        theme = self.get_theme()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # One shared gradient coordinate system, without per-cell focus/radius/margins.
        row_rect = QRectF(0, opt.rect.y(), self.view.viewport().width(), opt.rect.height())
        painter.save()
        painter.fillRect(opt.rect, selection_brush(theme["selection"], row_rect))
        painter.setFont(opt.font)
        painter.setPen(QColor(theme["selection_text"]["start"]))
        style = self.view.style()
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, self.view)
        text_rect.adjust(7, 0, -7, 0)
        text = opt.fontMetrics.elidedText(opt.text, Qt.TextElideMode.ElideRight, max(0, text_rect.width()))
        painter.drawText(text_rect, opt.displayAlignment | Qt.AlignmentFlag.AlignVCenter, text)
        if not opt.icon.isNull():
            icon_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, opt, self.view)
            opt.icon.paint(painter, icon_rect)
        painter.restore()


def stylesheet(theme):
    t = {key: css(theme[key]) for key in ROLES}
    return f"""
        QWidget {{ color:{t['foreground']}; font-family:'Segoe UI'; font-size:{theme.get('font_size',13)}px; }}
        QMainWindow {{background:{t['surface']};}}
        QDialog {{background:{t['background']};}}
        #content, #settings_body, #sidebar, #navigation {{ background:transparent; border:none; }}
        #sidebar {{ border-right:1px solid {t['border']}; }}
        QLabel {{ background:transparent; }}
        QLabel#brand {{ font-size:25px; font-weight:650; }}
        QLabel#heading {{ font-size:25px; font-weight:600; }}
        QLabel#metric {{ font-size:30px; font-weight:600; }}
        QLabel[muted=true] {{ color:{t['muted']}; }}
        #navigation::item {{ padding:13px 10px; margin:3px 0; border-radius:5px; }}
        #navigation::item:selected {{ background:{t['surface']}; color:{theme['accent']['start']}; }}
        #navigation::item:hover {{ background:{t['surface']}; }}
        #card {{ background:{t['surface']}; border:1px solid {t['border']}; border-radius:6px; }}
        QPushButton {{ font-family:'Segoe UI'; background:{t['surface']}; border:1px solid {t['border']}; border-radius:5px; padding:8px 12px; }}
        QPushButton:hover {{ border-color:{theme['accent']['start']}; }}
        QPushButton:disabled {{ color:{t['muted']}; }}
        #windowcontrol, #windowclose {{background:transparent;border:0;border-radius:0;padding:0;font-size:20px;}}
        #windowcontrol:hover {{background:{t['surface']};}}
        #windowclose:hover {{background:#b83a42;color:white;}}
        QPushButton[primary=true] {{ background:{t['accent']}; color:{t['selection_text']}; border-color:{theme['accent']['start']}; font-weight:600; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background:{t['surface']}; border:1px solid {t['border']}; border-radius:5px; padding:9px; }}
        QLineEdit:focus, QComboBox:focus {{ border-color:{theme['accent']['start']}; }}
        QComboBox::drop-down {{ border:0; width:26px; }}
        QComboBox QAbstractItemView {{ background:{t['surface']}; color:{t['foreground']}; border:1px solid {t['border']}; outline:0; selection-background-color:{theme['selection']['start']}; selection-color:{t['selection_text']}; padding:4px; }}
        QComboBox QAbstractItemView::item {{ min-height:28px; padding:5px; }}
        QTableWidget, QTreeWidget, QListWidget {{ background:{t['surface']}; alternate-background-color:{t['alternate']}; border:1px solid {t['border']}; border-radius:0; outline:0; selection-background-color:{theme['selection']['start']}; selection-color:{t['selection_text']}; }}
        QTreeWidget::item, QTableWidget::item {{ border:0; border-radius:0; margin:0; padding:0; }}
        QHeaderView::section {{ background:{t['sidebar']}; color:{t['muted']}; padding:10px 8px; border:0; border-bottom:1px solid {t['border']}; }}
        QMenu {{ background:{t['surface']}; border:1px solid {t['border']}; padding:5px; }}
        QMenu::item {{ padding:8px 22px; }}
        QMenu::item:selected {{ background:{theme['selection']['start']}; color:{t['selection_text']}; }}
        QSplitter::handle {{ background:{t['border']}; width:2px; height:2px; }}
        QCheckBox {{ spacing:8px; }}
        QProgressBar {{ border:0; background:{t['surface']}; border-radius:3px; }}
        QProgressBar::chunk {{ background:{t['accent']}; }}
        QScrollArea {{ background:transparent; border:0; }}
        QScrollBar:vertical {{ background:{t['surface']}; width:10px; }}
        QScrollBar::handle:vertical {{ background:{t['border']}; border-radius:4px; min-height:24px; }}
        QSlider::groove:horizontal {{ background:{t['border']}; height:6px; }}
        QSlider::handle:horizontal {{ background:{theme['accent']['start']}; width:14px; margin:-5px 0; border-radius:7px; }}
        QTabWidget::pane {{border:0;}}
        QTabBar::tab {{background:{t['sidebar']};color:{t['muted']};padding:10px 18px;border-bottom:2px solid transparent;}}
        QTabBar::tab:selected {{background:{t['surface']};color:{t['foreground']};border-bottom-color:{theme['accent']['start']};}}
    """


def harmony(colour, mode):
    h, s, v, _ = colour.getHsvF()
    h = max(0, h)
    offsets = {"Complementary": [0, .5], "Triad": [0, 1/3, 2/3], "Analogous": [-1/12, 0, 1/12],
               "Split complementary": [0, 5/12, 7/12], "Tetrad": [0, .25, .5, .75], "Single colour": [0]}
    if mode == "Monochromatic":
        return [QColor.fromHsvF(h, s, value) for value in [.25, .45, .65, .85, 1]]
    return [QColor.fromHsvF((h + offset) % 1, s, v) for offset in offsets.get(mode, [0])]


class ColourWheel(QWidget):
    changed = Signal(QColor)
    def __init__(self):
        super().__init__()
        self.setFixedSize(244, 244)
        self.hue, self.saturation, self.value = .38, .45, .8
        self.cache = None

    def colour(self):
        return QColor.fromHsvF(self.hue, self.saturation, self.value)

    def set_colour(self, colour):
        h, self.saturation, self.value, _ = colour.getHsvF()
        self.hue = max(0, h)
        self.update()

    def paintEvent(self, event):
        if self.cache is None:
            size = self.width()
            image = QImage(size, size, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            centre, radius = size / 2, size / 2 - 5
            for y in range(size):
                for x in range(size):
                    dx, dy = x - centre, centre - y
                    distance = math.hypot(dx, dy)
                    if distance <= radius:
                        image.setPixelColor(x, y, QColor.fromHsvF((math.atan2(dy, dx) / (2*math.pi)) % 1, distance / radius, 1.0))
            self.cache = image
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawImage(0, 0, self.cache)
        radius = self.width()/2 - 5
        angle = self.hue * 2 * math.pi
        point = QPointF(self.width()/2 + math.cos(angle)*radius*self.saturation, self.height()/2 - math.sin(angle)*radius*self.saturation)
        painter.setPen(QPen(Qt.GlobalColor.black, 4))
        painter.drawEllipse(point, 6, 6)
        painter.setPen(QPen(Qt.GlobalColor.white, 2))
        painter.drawEllipse(point, 6, 6)

    def mousePressEvent(self, event):
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            dx, dy = event.position().x() - self.width()/2, self.height()/2 - event.position().y()
            self.hue = (math.atan2(dy, dx) / (2*math.pi)) % 1
            self.saturation = min(1, math.hypot(dx, dy)/(self.width()/2-5))
            self.update()
            self.changed.emit(self.colour())


class ThemeDialog(QDialog):
    preview = Signal(dict)
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ZotiBox — Theme editor")
        self.theme = valid_theme(copy.deepcopy(theme))
        self.setMinimumWidth(700)
        layout = QVBoxLayout(self)
        self.preset = QComboBox()
        self.preset.addItems(["Custom"] + list(PRESETS))
        for index,theme in enumerate(PRESETS.values(),1):
            self.preset.setItemIcon(index,theme_sample(theme))
        layout.addWidget(self.preset)
        body = QHBoxLayout()
        self.wheel = ColourWheel()
        self.harmony_anchor=QColor(self.theme["background"]["start"])
        left = QVBoxLayout()
        left.addWidget(self.wheel)
        left.addWidget(QLabel("Brightness"))
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, 100)
        left.addWidget(self.brightness)
        body.addLayout(left)
        right = QFormLayout()
        self.role = QComboBox()
        for key, label in {"background":"Background", "accent":"Accent"}.items():
            self.role.addItem(label, key)
        self.endpoint = QComboBox()
        self.endpoint.addItems(["Start / single colour", "Gradient end"])
        self.mode = QComboBox()
        self.mode.addItems(["Single colour", "Linear gradient"])
        self.angle = QComboBox()
        for value in [0,45,90,135,180,225,270,315]:
            self.angle.addItem(f"{value}°", value)
        self.hex = QLineEdit()
        self.harmony = QComboBox()
        self.harmony.addItems(["Single colour", "Complementary", "Triad", "Analogous", "Split complementary", "Tetrad", "Monochromatic"])
        for title, control in [("Theme colour", self.role), ("Fill", self.mode), ("Editing", self.endpoint), ("Gradient angle", self.angle), ("HEX colour", self.hex), ("Colour harmony", self.harmony)]:
            right.addRow(title, control)
        body.addLayout(right, 1)
        layout.addLayout(body)
        self.font_size=QSlider(Qt.Orientation.Horizontal)
        self.font_size.setRange(11,20)
        self.font_size.setValue(self.theme["font_size"])
        layout.addWidget(QLabel("Text size"))
        layout.addWidget(self.font_size)
        self.font_size.valueChanged.connect(self.change_font)
        self.swatches = QHBoxLayout()
        layout.addLayout(self.swatches)
        self.preview_strip = QLabel("Theme preview")
        self.preview_strip.setMinimumHeight(55)
        self.preview_strip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview_strip)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.role.currentIndexChanged.connect(self.load_role)
        self.role.currentIndexChanged.connect(self.anchor_role)
        self.endpoint.currentIndexChanged.connect(self.load_role)
        self.wheel.changed.connect(self.set_colour)
        self.hex.editingFinished.connect(self.hex_changed)
        self.brightness.valueChanged.connect(self.value_changed)
        self.mode.currentIndexChanged.connect(self.change_fill)
        self.angle.currentIndexChanged.connect(self.change_fill)
        self.harmony.currentIndexChanged.connect(self.update_swatches)
        self.anchor_button=QPushButton("Use current colour as harmony base")
        self.anchor_button.clicked.connect(self.anchor_harmony)
        layout.insertWidget(layout.count()-2,self.anchor_button)
        self.endpoint_swatches=QLabel()
        self.endpoint_swatches.setMinimumHeight(40)
        self.endpoint_swatches.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.insertWidget(layout.count()-2,self.endpoint_swatches)
        self.preset.currentIndexChanged.connect(self.load_preset)
        self.load_role()

    def anchor_harmony(self):
        self.harmony_anchor=QColor(self.wheel.colour())
        self.update_swatches()

    def anchor_role(self):
        self.harmony_anchor=QColor(self.theme[self.role.currentData()]["start"])
        self.update_swatches()

    def change_font(self,value):
        self.theme["font_size"]=value
        self.emit_preview()

    def emit_preview(self):
        self.theme=valid_theme(self.theme)
        self.preview.emit(copy.deepcopy(self.theme))

    def load_role(self):
        token = self.theme[self.role.currentData()]
        for widget in [self.mode, self.angle, self.brightness]:
            widget.blockSignals(True)
        can_gradient = self.role.currentData()=="background"
        if not can_gradient or not token["gradient"]:
            self.endpoint.blockSignals(True);self.endpoint.setCurrentIndex(0);self.endpoint.blockSignals(False)
        self.mode.setEnabled(can_gradient)
        self.mode.setCurrentIndex(int(token["gradient"]))
        self.endpoint.setEnabled(can_gradient and token["gradient"])
        self.angle.setEnabled(can_gradient and token["gradient"])
        self.angle.setCurrentIndex(self.angle.findData(token["angle"]))
        colour = QColor(token["end" if self.endpoint.currentIndex() else "start"])
        self.wheel.set_colour(colour)
        self.brightness.setValue(round(colour.valueF()*100))
        self.hex.setText(colour.name())
        for widget in [self.mode, self.angle, self.brightness]:
            widget.blockSignals(False)
        self.update_swatches()

    def set_colour(self, colour, from_harmony=False):
        if not from_harmony and self.endpoint.currentIndex()==0:self.harmony_anchor=QColor(colour)
        key = "end" if self.endpoint.currentIndex() else "start"
        previous = self.theme[self.role.currentData()][key]
        if self.role.currentData() == "accent" and self.theme["selection"][key] == previous:
            self.theme["selection"][key] = colour.name()
        self.theme[self.role.currentData()][key] = colour.name()
        self.wheel.set_colour(colour)
        self.hex.setText(colour.name())
        self.brightness.blockSignals(True)
        self.brightness.setValue(round(colour.valueF()*100))
        self.brightness.blockSignals(False)
        self.update_swatches()
        self.emit_preview()

    def hex_changed(self):
        colour = QColor(self.hex.text())
        if colour.isValid():
            self.set_colour(colour)
        else:
            self.hex.setText(self.wheel.colour().name())

    def value_changed(self, value):
        self.wheel.value = value / 100
        self.set_colour(self.wheel.colour())

    def change_fill(self):
        token = self.theme[self.role.currentData()]
        old_gradient = token["gradient"]
        token["gradient"] = self.mode.currentIndex() == 1
        token["angle"] = self.angle.currentData() or 0
        if self.role.currentData() == "accent" and self.theme["selection"]["start"] == token["start"] and self.theme["selection"]["gradient"] == old_gradient:
            self.theme["selection"] = copy.deepcopy(token)
        self.load_role()
        self.emit_preview()

    def update_swatches(self):
        while self.swatches.count():
            item = self.swatches.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        base=self.wheel.colour() if self.harmony.currentText()=="Single colour" else self.harmony_anchor
        self.anchor_button.setText(f"Harmony base: {self.harmony_anchor.name()} · Use current colour")
        for colour in harmony(base, self.harmony.currentText()):
            swatch = QPushButton(colour.name())
            text = "#111111" if colour.lightnessF() > .55 else "#ffffff"
            swatch.setStyleSheet(f"background:{colour.name()};color:{text};padding:12px;border:1px solid #777777;")
            swatch.clicked.connect(lambda checked=False, colour=colour: self.set_colour(colour,True))
            self.swatches.addWidget(swatch)
        token = self.theme[self.role.currentData()]
        self.endpoint_swatches.setVisible(token["gradient"])
        self.endpoint_swatches.setText(f"Start: {token['start']}       End: {token['end']}       Editing: {'end' if self.endpoint.currentIndex() else 'start'}")
        self.endpoint_swatches.setStyleSheet(f"background:{css(token)};color:{self.theme['foreground']['start']};padding:8px;")
        self.preview_strip.setStyleSheet(f"background:{css(token)};color:{self.theme['foreground']['start']};border:1px solid #777777;")

    def load_preset(self):
        if self.preset.currentText() in PRESETS:
            self.theme = valid_theme(copy.deepcopy(PRESETS[self.preset.currentText()]))
            self.harmony_anchor=QColor(self.theme[self.role.currentData()]["start"])
            self.load_role()
            self.emit_preview()
