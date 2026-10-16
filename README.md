# TruSenceVision — Setup & Run

## Project Structure

```
TrusenceVision-Project/
├── app.py              ← Flask backend  (NEW)
├── requirements.txt    ← Python deps    (NEW)
├── index.html          ← Frontend
├── style.css           ← Frontend CSS
├── app.js              ← Frontend JS
├── Dataset/            ← Face image dataset (already exists)
│   └── Ishan Prasad/
│       ├── Photo on 3-12-26 at 00.06.jpg
│       └── ...
├── encodings.pkl       ← Auto-generated on first run
└── trusence.db         ← Auto-generated SQLite database
```

---

## 1 — Install dependencies

`face_recognition` needs **cmake** and **dlib** which require a C++ compiler.

### macOS (your machine)
```bash
# Install cmake and dlib build tools via Homebrew
brew install cmake

# Install Python packages
pip install -r requirements.txt
```

If `face_recognition` fails to install, try:
```bash
pip install dlib
pip install face_recognition
```

---

## 2 — Run the backend

```bash
cd TrusenceVision-Project
python app.py
```

First run will:
1. Create `trusence.db` (SQLite)
2. Scan `Dataset/` and encode all faces → saved to `encodings.pkl`
3. Start Flask on `http://localhost:5000`

You'll see output like:
```
=======================================================
  TruSenceVision Backend
=======================================================
[DB]  Database initialised.
[ENC] Loading face encodings …
[ENC] Ishan Prasad: 5 encodings
[ENC] Total: 5 face vectors saved.
[SRV] Starting Flask on http://localhost:5000
```

---

## 3 — Open the frontend

Open `index.html` in Chrome. The sidebar status dot will turn **green** once the backend responds.

---

## 4 — How recognition works

| Parameter | Value | What it does |
|-----------|-------|-------------|
| `RECOGNITION_TOLERANCE` | 0.48 | Face distance threshold — lower = stricter matching |
| `VOTE_FRAMES` | 6 | Frames sampled per check — majority vote counts |
| `PROCESS_EVERY` | 3 | Process every 3rd frame in live stream (speed) |

**Accuracy tips:**
- More photos per person = better accuracy (5–10 is ideal)
- Ensure good lighting in Dataset photos
- Lower `RECOGNITION_TOLERANCE` to 0.42 if getting false matches
- Raise to 0.52 if missing known students

---

## 5 — Adding students via frontend

Use **Register Student** page → fill details → capture 5 webcam photos → click Register.

Photos are saved to `Dataset/<Name>/` and immediately encoded.

---

## 6 — Rebuild encodings manually

If you add photos directly to Dataset folders, delete `encodings.pkl` and restart `app.py`. It will re-encode everything.

Or hit this endpoint:
```
GET http://localhost:5000/api/ping
```
(encodings auto-load on startup every time)
