"""User-facing audio choices, translated only at the downloader boundary."""
FORMATS=['auto','mp3','flac','original']
QUALITIES=[('Lossless where available','lossless'),('Very high lossy','very_high'),('High lossy','high'),('Normal lossy','normal')]

def fill_formats(combo,value='auto'):
    combo.clear();combo.addItems(FORMATS);combo.setCurrentText(value if value in FORMATS else 'original' if value=='copy' else 'auto')
    combo.setToolTip('Auto: FLAC where available, otherwise MP3 320 kbps. FLAC: genuine lossless source required. Original: keep the selected source codec.')

def fill_qualities(combo,value='lossless'):
    combo.clear()
    for label,key in QUALITIES:combo.addItem(label,key)
    combo.setCurrentIndex(max(0,combo.findData(value)))

def value(combo):return combo.currentData() or combo.currentText()
