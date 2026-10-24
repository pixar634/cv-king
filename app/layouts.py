"""The selectable designs. All share one HTML structure (so every layout reads identically to an
ATS: name, contact, summary, skills, then each role as title, company, dates, bullets) and differ
only in CSS and header style."""

LAYOUTS = {
    "flowcv": {
        "label": "FlowCV serif",
        "blurb": "Your current design: serif, dates in a left gutter. Calm and readable; fine everywhere.",
        "header": "grid", "font_pt": 10.6, "min_pt": 10.0,
    },
    "engineer": {
        "label": "Engineer",
        "blurb": "The Jake's-Resume structure most product-company engineers use: centred name, "
                 "title and dates on one line, dense and scannable.",
        "header": "centered", "font_pt": 10.4, "min_pt": 9.8,
    },
    "modern": {
        "label": "Modern sans",
        "blurb": "Clean sans with one muted accent. Reads as product/startup without looking designed-for-show.",
        "header": "inline", "font_pt": 9.8, "min_pt": 9.2,
    },
    "classic": {
        "label": "Classic",
        "blurb": "Single column in Calibri, dates right. The safest format for MNCs, services firms and Workday portals.",
        "header": "grid", "font_pt": 10.4, "min_pt": 9.8,
    },
}

# Page geometry per density. One page tightens margins and spacing before it cuts content.
DENSITY = {
    "normal": {"margin": "15mm 19mm 14mm 19mm", "sec_gap": 12, "entry_gap": 7},
    "compact": {"margin": "11mm 15mm 10mm 15mm", "sec_gap": 8, "entry_gap": 4.5},
}
