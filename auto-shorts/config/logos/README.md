# config/logos — known FOREIGN watermarks

`clipping/watermark_check.py` compares 12 frames of every new clipping source with each image in this
folder (ARCHITECTURE.md §6.3.1). A match with score >= 0.8 marks the source `watermark_suspect`
(severity "warning"): such a source is **not masked**, it is dropped or replaced with a clean master from
the rights holder. A foreign watermark costs ~90% of reach and signals reused content to YouTube.

What to put here:

- logos of platforms and channels whose watermark must never reach our uploads (TikTok, Likee,
  VK Clips, other creators' channel bugs, TV channel logos);
- PNG with transparency is best: the transparent area is ignored when matching (masked match);
  cropping to the logo itself is done automatically;
- one file per logo variant (white / black / animated frames saved as separate PNGs);
- file names in Latin: `tiktok_white.png`, `channel_xyz.png`. Files starting with `_` or `.` are skipped.

Do NOT put our own logos here — they would flag our own sources.

Optional sidecar `<name>.json` next to the image:

```json
{"name": "tiktok", "height_range": [0.03, 0.2]}
```

`height_range` — expected logo height as a fraction of the frame height (default 0.03–0.20).

Without any logos the check still runs a logo-agnostic "static corner overlay" heuristic that can only
produce a hint, never a hard warning.
