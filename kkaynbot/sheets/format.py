def fr(sid, r1, c1, r2, c2, bold=False, bg=None, fg=None, sz=None, al=None):
    fmt = {}; tf = {}
    if bold: tf["bold"] = True
    if fg:   tf["foregroundColor"] = fg
    if sz:   tf["fontSize"] = sz
    if tf:   fmt["textFormat"] = tf
    if bg:   fmt["backgroundColor"] = bg
    if al:   fmt["horizontalAlignment"] = al
    fmt["verticalAlignment"] = "MIDDLE"
    return {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r1-1, "endRowIndex": r2,
            "startColumnIndex": c1-1, "endColumnIndex": c2},
            "cell": {"userEnteredFormat": fmt}, "fields": "userEnteredFormat"}}

def mg(sid, r1, c1, r2, c2):
    return {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": r1-1, "endRowIndex": r2,
            "startColumnIndex": c1-1, "endColumnIndex": c2}, "mergeType": "MERGE_ALL"}}

def cw(sid, c, px):
    return {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
            "startIndex": c-1, "endIndex": c}, "properties": {"pixelSize": px}, "fields": "pixelSize"}}

def rh(sid, r, px):
    return {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS",
            "startIndex": r-1, "endIndex": r}, "properties": {"pixelSize": px}, "fields": "pixelSize"}}

def col_letter(n):
    return chr(64+n) if n <= 26 else chr(64+n//26) + chr(64+n%26)
