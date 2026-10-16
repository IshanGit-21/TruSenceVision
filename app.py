"""
TruSenceVision — app.py
Full Flask backend: face recognition, live session management,
attendance tracking, SQLite persistence, MJPEG stream.

Run:
    /opt/anaconda3/bin/python app.py

Dataset layout:
    Dataset/
        Person Name/
            photo1.jpg
            photo2.jpg
"""

import cv2
import os
import csv
import time
import pickle
import sqlite3
import threading
import random
import io
import datetime

import numpy as np
import face_recognition
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from PIL import Image

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────
DATASET_PATH         = "Dataset"
ENCODINGS_FILE       = "encodings.pkl"
DB_FILE              = "trusence.db"
PORT                 = 5001          # 5000 blocked by macOS AirPlay
RECOGNITION_TOLERANCE = 0.48        # lower = stricter  (0.42–0.55)
VOTE_FRAMES          = 6            # frames sampled per attendance check
# PROCESS_EVERY removed — recognition no longer runs in live stream (fixes segfault on Apple Silicon)

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────────────────────
#  GLOBAL STATE
# ─────────────────────────────────────────────────────────────
known_encodings: list = []
known_names:     list = []
known_rolls:     list = []

camera_lock    = threading.Lock()
camera         = None

session_lock   = threading.Lock()
active_session = None
check_thread   = None

_frame_counter   = 0
_last_detections = []


# ─────────────────────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            roll_no       TEXT UNIQUE NOT NULL,
            class_name    TEXT,
            semester      INTEGER DEFAULT 6,
            email         TEXT,
            face_encoded  INTEGER DEFAULT 0,
            registered_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            subject       TEXT NOT NULL,
            min_interval  INTEGER DEFAULT 2,
            max_interval  INTEGER DEFAULT 5,
            threshold     INTEGER DEFAULT 75,
            started_at    TEXT DEFAULT (datetime('now')),
            ended_at      TEXT,
            total_checks  INTEGER DEFAULT 0,
            duration_secs INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS attendance_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            roll_no    TEXT NOT NULL,
            status     TEXT NOT NULL,
            timestamp  INTEGER NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        );
    """)
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────
#  FACE ENCODINGS
# ─────────────────────────────────────────────────────────────
def load_encodings():
    """Load cached encodings, or rebuild from Dataset/ folder."""
    global known_encodings, known_names, known_rolls

    if os.path.exists(ENCODINGS_FILE):
        with open(ENCODINGS_FILE, "rb") as f:
            data = pickle.load(f)
        known_encodings = data.get("encodings", [])
        known_names     = data.get("names",     [])
        known_rolls     = data.get("rolls",     ["unknown"] * len(known_names))
        print(f"[ENC] Loaded {len(known_encodings)} encodings from cache.")
        return

    rebuild_encodings()


def rebuild_encodings():
    """Scan Dataset/ folder, encode all faces, save cache + sync DB."""
    global known_encodings, known_names, known_rolls
    known_encodings, known_names, known_rolls = [], [], []

    if not os.path.exists(DATASET_PATH):
        print("[ENC] Dataset/ not found — skipping.")
        return

    conn = get_db()

    for person_name in sorted(os.listdir(DATASET_PATH)):
        person_dir = os.path.join(DATASET_PATH, person_name)
        if not os.path.isdir(person_dir) or person_name.startswith("."):
            continue

        roll_no = _ensure_student(conn, person_name)
        enc_list = []

        for fname in os.listdir(person_dir):
            if not fname.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
                continue
            fpath = os.path.join(person_dir, fname)
            try:
                img = face_recognition.load_image_file(fpath)
                h, w = img.shape[:2]
                if w > 800:
                    img = cv2.resize(img, (800, int(h * 800 / w)))
                encs = face_recognition.face_encodings(img)
                if encs:
                    enc_list.append(encs[0])
            except Exception as e:
                print(f"[ENC] Skip {fpath}: {e}")

        if enc_list:
            known_encodings.extend(enc_list)
            known_names.extend([person_name] * len(enc_list))
            known_rolls.extend([roll_no]     * len(enc_list))
            conn.execute("UPDATE students SET face_encoded=1 WHERE roll_no=?", (roll_no,))
            conn.commit()
            print(f"[ENC] {person_name}: {len(enc_list)} encodings")
        else:
            print(f"[ENC] {person_name}: no faces detected")

    conn.close()
    _save_encodings()
    print(f"[ENC] Total: {len(known_encodings)} vectors saved.")


def _save_encodings():
    with open(ENCODINGS_FILE, "wb") as f:
        pickle.dump({"encodings": known_encodings,
                     "names":     known_names,
                     "rolls":     known_rolls}, f)


def _ensure_student(conn, person_name):
    """Return existing roll_no or create a placeholder DB record."""
    row = conn.execute("SELECT roll_no FROM students WHERE name=?", (person_name,)).fetchone()
    if row:
        return row["roll_no"]
    roll_no = person_name.lower().replace(" ", "_")[:20]
    conn.execute(
        "INSERT OR IGNORE INTO students (name, roll_no, class_name, face_encoded) VALUES (?,?,?,0)",
        (person_name, roll_no, "Unknown")
    )
    conn.commit()
    return roll_no


def encode_new_student(pil_images, roll_no, name):
    """Encode PIL images for a newly registered student, update cache."""
    new_encs = []
    for pil_img in pil_images:
        try:
            img = np.array(pil_img.convert("RGB"))
            encs = face_recognition.face_encodings(img)
            if encs:
                new_encs.append(encs[0])
        except Exception as e:
            print(f"[ENC] Encode error: {e}")

    if new_encs:
        known_encodings.extend(new_encs)
        known_names.extend([name]     * len(new_encs))
        known_rolls.extend([roll_no]  * len(new_encs))
        _save_encodings()

    return len(new_encs)


# ─────────────────────────────────────────────────────────────
#  RECOGNITION
# ─────────────────────────────────────────────────────────────
def recognise_frame(frame_bgr):
    """
    Recognise faces in a BGR frame.
    Returns list of {name, roll_no, confidence, box(x1,y1,x2,y2)}.
    """
    if not known_encodings:
        return []

    small = cv2.resize(frame_bgr, (0, 0), fx=0.4, fy=0.4)
    rgb   = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

    locs = face_recognition.face_locations(rgb, model="hog")
    encs = face_recognition.face_encodings(rgb, locs)

    results = []
    for enc, loc in zip(encs, locs):
        dists    = face_recognition.face_distance(known_encodings, enc)
        best_idx = int(np.argmin(dists))
        best_d   = float(dists[best_idx])
        conf     = round(max(0.0, 1.0 - best_d) * 100, 1)

        if best_d <= RECOGNITION_TOLERANCE:
            name    = known_names[best_idx]
            roll_no = known_rolls[best_idx]
        else:
            name, roll_no = "Unknown", None

        top, right, bottom, left = loc
        s = 1 / 0.4
        results.append({
            "name":       name,
            "roll_no":    roll_no,
            "confidence": conf,
            "box":        (int(left*s), int(top*s), int(right*s), int(bottom*s)),
        })

    return results


# ─────────────────────────────────────────────────────────────
#  CAMERA
# ─────────────────────────────────────────────────────────────
def open_camera():
    global camera
    with camera_lock:
        if camera is None or not camera.isOpened():
            camera = cv2.VideoCapture(0)
            camera.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            camera.set(cv2.CAP_PROP_FPS, 30)
    return camera is not None and camera.isOpened()


def close_camera():
    global camera
    with camera_lock:
        if camera:
            camera.release()
            camera = None


def _annotate(frame, detections):
    for d in detections:
        x1, y1, x2, y2 = d["box"]
        color = (34, 139, 34) if d["name"] != "Unknown" else (0, 0, 200)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"{d['name']}  {d['confidence']}%"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(frame, (x1, y1 - th - 8), (x1 + tw + 6, y1), color, -1)
        cv2.putText(frame, label, (x1 + 3, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return frame


def generate_frames():
    """MJPEG generator — ~25 fps, annotations from periodic _run_check only.
    Face recognition is NOT called here to prevent segfault on Apple Silicon.
    Recognition runs exclusively inside _run_check() during session checks."""
    while True:
        with camera_lock:
            if camera is None or not camera.isOpened():
                time.sleep(0.05)
                continue
            ret, frame = camera.read()

        if not ret:
            time.sleep(0.05)
            continue

        # Use last known detections from _run_check — never call recognise_frame here
        annotated = _annotate(frame.copy(), _last_detections)
        _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
               + buf.tobytes() + b"\r\n")
        time.sleep(1 / 25)


# ─────────────────────────────────────────────────────────────
#  SESSION WORKER
# ─────────────────────────────────────────────────────────────
def _run_check(session_id, rolls):
    """Sample VOTE_FRAMES frames, majority-vote per student, write DB events."""
    votes = {}
    for _ in range(VOTE_FRAMES):
        with camera_lock:
            if camera is None or not camera.isOpened():
                break
            ret, frame = camera.read()
        if not ret:
            continue
        for d in recognise_frame(frame):
            if d["roll_no"]:
                votes.setdefault(d["roll_no"], 0)
                votes[d["roll_no"]] += 1
        time.sleep(0.15)

    present = {r for r, cnt in votes.items() if cnt >= max(2, VOTE_FRAMES // 2)}
    now     = int(time.time())
    results = []

    conn = get_db()
    for roll in rolls:
        status = "present" if roll in present else "absent"
        conn.execute(
            "INSERT INTO attendance_events (session_id, roll_no, status, timestamp) VALUES (?,?,?,?)",
            (session_id, roll, status, now)
        )
        row = conn.execute("SELECT name FROM students WHERE roll_no=?", (roll,)).fetchone()
        results.append({
            "roll_no":   roll,
            "name":      row["name"] if row else roll,
            "status":    status,
            "timestamp": now,
        })
    conn.commit()
    conn.close()
    return results


def session_worker(session_id, min_iv, max_iv):
    global active_session
    conn  = get_db()
    rolls = [r["roll_no"] for r in
             conn.execute("SELECT roll_no FROM students WHERE face_encoded=1").fetchall()]
    conn.close()

    while True:
        with session_lock:
            if active_session is None or active_session["id"] != session_id:
                return
            active_session["check_running"] = False

        wait = random.randint(min_iv * 60, max_iv * 60)
        for remaining in range(wait, 0, -1):
            with session_lock:
                if active_session is None or active_session["id"] != session_id:
                    return
                active_session["next_check_in"] = remaining
            time.sleep(1)

        with session_lock:
            if active_session is None or active_session["id"] != session_id:
                return
            active_session["check_running"] = True

        results = _run_check(session_id, rolls)

        with session_lock:
            if active_session is None:
                return
            active_session["checks_done"]       += 1
            active_session["check_running"]      = False
            active_session["latest_detections"]  = results

            for r in results:
                roll = r["roll_no"]
                st   = active_session["student_status"].setdefault(
                           roll, {"checks": 0, "present": 0, "pct": 0.0})
                st["checks"] += 1
                if r["status"] == "present":
                    st["present"] += 1
                st["pct"] = round(100 * st["present"] / st["checks"], 1)

            conn2 = get_db()
            conn2.execute("UPDATE sessions SET total_checks=? WHERE id=?",
                          (active_session["checks_done"], session_id))
            conn2.commit()
            conn2.close()


# ─────────────────────────────────────────────────────────────
#  API ROUTES
# ─────────────────────────────────────────────────────────────

@app.route("/api/ping")
def ping():
    return jsonify({"success": True})


# ── Dashboard ────────────────────────────────────────────────
@app.route("/api/dashboard")
def dashboard():
    conn        = get_db()
    total_stu   = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    total_sess  = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    today       = datetime.date.today().isoformat()
    today_pres  = conn.execute(
        """SELECT COUNT(DISTINCT ae.roll_no) FROM attendance_events ae
           JOIN sessions s ON s.id=ae.session_id
           WHERE ae.status='present' AND s.started_at LIKE ?""",
        (today + "%",)
    ).fetchone()[0]
    recent = conn.execute(
        """SELECT s.id, s.subject, s.started_at,
             COUNT(CASE WHEN ae.status='present' THEN 1 END) AS present_count,
             COUNT(CASE WHEN ae.status='absent'  THEN 1 END) AS absent_count
           FROM sessions s
           LEFT JOIN attendance_events ae ON ae.session_id=s.id
           GROUP BY s.id ORDER BY s.started_at DESC LIMIT 5"""
    ).fetchall()
    conn.close()

    with session_lock:
        sess_active = active_session is not None

    return jsonify({
        "success":         True,
        "total_students":  total_stu,
        "total_sessions":  total_sess,
        "today_present":   today_pres,
        "session_active":  sess_active,
        "recent_sessions": [dict(r) for r in recent],
    })


# ── Students ─────────────────────────────────────────────────
@app.route("/api/students")
def list_students():
    conn = get_db()
    rows = conn.execute("SELECT * FROM students ORDER BY registered_at DESC").fetchall()
    conn.close()
    return jsonify({"success": True, "students": [dict(r) for r in rows]})


@app.route("/api/students/register", methods=["POST"])
def register_student():
    name       = request.form.get("name",       "").strip()
    roll_no    = request.form.get("roll_no",    "").strip()
    class_name = request.form.get("class_name", "").strip()
    semester   = request.form.get("semester",   6)
    email      = request.form.get("email",      "").strip()
    images     = request.files.getlist("images")

    if not name or not roll_no:
        return jsonify({"success": False, "message": "Name and roll number required."})
    if not images:
        return jsonify({"success": False, "message": "At least one image required."})

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO students (name, roll_no, class_name, semester, email, face_encoded)"
            " VALUES (?,?,?,?,?,0)",
            (name, roll_no, class_name, int(semester), email)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "message": f"Roll '{roll_no}' already exists."})

    # Save images to Dataset/<name>/
    person_dir = os.path.join(DATASET_PATH, name)
    os.makedirs(person_dir, exist_ok=True)

    pil_images = []
    for i, img_file in enumerate(images):
        try:
            pil = Image.open(img_file).convert("RGB")
            pil.save(os.path.join(person_dir, f"{roll_no}_{i+1}.jpg"))
            pil_images.append(pil)
        except Exception as e:
            print(f"[REG] Image save error: {e}")

    count = encode_new_student(pil_images, roll_no, name)
    conn.execute("UPDATE students SET face_encoded=? WHERE roll_no=?",
                 (1 if count else 0, roll_no))
    conn.commit()
    conn.close()

    return jsonify({"success": True,
                    "message": f"'{name}' registered with {count} face encodings."})


@app.route("/api/students/<roll_no>", methods=["DELETE"])
def delete_student(roll_no):
    conn = get_db()
    row  = conn.execute("SELECT name FROM students WHERE roll_no=?", (roll_no,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"success": False, "message": "Student not found."})

    name = row["name"]
    conn.execute("DELETE FROM students WHERE roll_no=?", (roll_no,))
    conn.commit()
    conn.close()

    # Remove from in-memory cache
    keep = [i for i, r in enumerate(known_rolls) if r != roll_no]
    known_encodings[:] = [known_encodings[i] for i in keep]
    known_names[:]     = [known_names[i]     for i in keep]
    known_rolls[:]     = [known_rolls[i]     for i in keep]
    _save_encodings()

    # Remove dataset folder
    import shutil
    pdir = os.path.join(DATASET_PATH, name)
    if os.path.isdir(pdir):
        shutil.rmtree(pdir)

    return jsonify({"success": True, "message": f"'{name}' deleted."})


# ── Sessions ─────────────────────────────────────────────────
@app.route("/api/sessions/start", methods=["POST"])
def start_session():
    global active_session, check_thread

    with session_lock:
        if active_session is not None:
            return jsonify({"success": False, "message": "Session already active."})

    data         = request.get_json() or {}
    subject      = data.get("subject",      "Class").strip()
    min_interval = int(data.get("min_interval", 2))
    max_interval = int(data.get("max_interval", 5))
    threshold    = int(data.get("threshold",    75))

    if not open_camera():
        return jsonify({"success": False, "message": "Could not open camera."})

    conn = get_db()
    conn.execute(
        "INSERT INTO sessions (subject, min_interval, max_interval, threshold) VALUES (?,?,?,?)",
        (subject, min_interval, max_interval, threshold)
    )
    conn.commit()
    session_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    with session_lock:
        active_session = {
            "id":                session_id,
            "subject":           subject,
            "started_at":        time.time(),
            "threshold":         threshold,
            "checks_done":       0,
            "check_running":     False,
            "next_check_in":     min_interval * 60,
            "latest_detections": [],
            "student_status":    {},
        }

    check_thread = threading.Thread(
        target=session_worker,
        args=(session_id, min_interval, max_interval),
        daemon=True,
    )
    check_thread.start()

    return jsonify({"success": True, "session_id": session_id, "subject": subject})


@app.route("/api/sessions/<int:session_id>/status")
def session_status(session_id):
    with session_lock:
        if active_session is None or active_session["id"] != session_id:
            return jsonify({"success": False, "message": "Session not active."})

        present_now = sum(
            1 for st in active_session["student_status"].values()
            if st["pct"] >= active_session["threshold"]
        )
        return jsonify({
            "success":           True,
            "checks_done":       active_session["checks_done"],
            "check_running":     active_session["check_running"],
            "next_check_in":     active_session["next_check_in"],
            "currently_present": present_now,
            "latest_detections": active_session["latest_detections"],
            "student_status":    {
                roll: {"pct": s["pct"], "checks": s["checks"]}
                for roll, s in active_session["student_status"].items()
            },
        })


@app.route("/api/sessions/<int:session_id>/stop", methods=["POST"])
def stop_session(session_id):
    global active_session

    with session_lock:
        if active_session is None or active_session["id"] != session_id:
            return jsonify({"success": False, "message": "Session not active."})
        sess_data      = dict(active_session)
        active_session = None

    close_camera()

    duration     = int(time.time() - sess_data["started_at"])
    total_checks = sess_data["checks_done"]
    threshold    = sess_data["threshold"]

    conn = get_db()
    rows = conn.execute(
        """SELECT roll_no,
             SUM(CASE WHEN status='present' THEN 1 ELSE 0 END) AS p,
             COUNT(*) AS t
           FROM attendance_events WHERE session_id=? GROUP BY roll_no""",
        (session_id,)
    ).fetchall()

    attendance = []
    for r in rows:
        pct    = round(100 * r["p"] / r["t"], 1) if r["t"] else 0
        status = "present" if pct >= threshold else "absent"
        stu    = conn.execute("SELECT name FROM students WHERE roll_no=?",
                              (r["roll_no"],)).fetchone()
        attendance.append({
            "roll_no":        r["roll_no"],
            "name":           stu["name"] if stu else r["roll_no"],
            "status":         status,
            "detection_pct":  pct,
            "checks_present": r["p"],
            "total_checks":   r["t"],
        })

    conn.execute(
        "UPDATE sessions SET ended_at=datetime('now'), duration_secs=?, total_checks=? WHERE id=?",
        (duration, total_checks, session_id)
    )
    conn.commit()
    conn.close()

    return jsonify({"success": True, "subject": sess_data["subject"],
                    "attendance": attendance})


@app.route("/api/sessions")
def list_sessions():
    conn = get_db()
    rows = conn.execute(
        """SELECT s.id, s.subject, s.started_at, s.ended_at, s.total_checks,
             COUNT(CASE WHEN ae.status='present' THEN 1 END) AS present_count,
             COUNT(CASE WHEN ae.status='absent'  THEN 1 END) AS absent_count
           FROM sessions s
           LEFT JOIN attendance_events ae ON ae.session_id=s.id
           GROUP BY s.id ORDER BY s.started_at DESC"""
    ).fetchall()
    conn.close()
    return jsonify({"success": True, "sessions": [dict(r) for r in rows]})


@app.route("/api/sessions/<int:session_id>/attendance")
def session_attendance(session_id):
    conn = get_db()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not sess:
        conn.close()
        return jsonify({"success": False, "message": "Session not found."})

    sess_dict = dict(sess)
    threshold = sess_dict.get("threshold", 75)
    rows = conn.execute(
        """SELECT ae.roll_no,
             SUM(CASE WHEN ae.status='present' THEN 1 ELSE 0 END) AS checks_present,
             COUNT(*) AS total_checks
           FROM attendance_events ae WHERE ae.session_id=? GROUP BY ae.roll_no""",
        (session_id,)
    ).fetchall()

    attendance = []
    for r in rows:
        pct    = round(100 * r["checks_present"] / r["total_checks"], 1) if r["total_checks"] else 0
        status = "present" if pct >= threshold else "absent"
        stu    = conn.execute("SELECT name FROM students WHERE roll_no=?",
                              (r["roll_no"],)).fetchone()
        attendance.append({
            "roll_no":        r["roll_no"],
            "name":           stu["name"] if stu else r["roll_no"],
            "status":         status,
            "detection_pct":  pct,
            "checks_present": r["checks_present"],
            "total_checks":   r["total_checks"],
        })

    conn.close()
    return jsonify({"success": True, "session": sess_dict, "attendance": attendance})


# ── Export CSV ───────────────────────────────────────────────
@app.route("/api/attendance/export")
def export_csv():
    sid  = request.args.get("session_id", type=int)
    conn = get_db()

    if sid:
        sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        rows = conn.execute(
            """SELECT s.name, s.roll_no, s.class_name,
                 SUM(CASE WHEN ae.status='present' THEN 1 ELSE 0 END) AS p,
                 COUNT(ae.id) AS t
               FROM attendance_events ae
               JOIN students s ON s.roll_no=ae.roll_no
               WHERE ae.session_id=? GROUP BY ae.roll_no""", (sid,)
        ).fetchall()
        fname = f"attendance_session_{sid}.csv"
        threshold = dict(sess).get("threshold", 75) if sess else 75
    else:
        rows = conn.execute(
            """SELECT s.name, s.roll_no, s.class_name,
                 SUM(CASE WHEN ae.status='present' THEN 1 ELSE 0 END) AS p,
                 COUNT(ae.id) AS t
               FROM attendance_events ae
               JOIN students s ON s.roll_no=ae.roll_no
               GROUP BY ae.roll_no"""
        ).fetchall()
        fname     = "attendance_all.csv"
        threshold = 75
    conn.close()

    buf    = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Name", "Roll No", "Class", "Status",
                     "Detection %", "Checks Present", "Total Checks"])
    for r in rows:
        pct    = round(100 * r["p"] / r["t"], 1) if r["t"] else 0
        status = "Present" if pct >= threshold else "Absent"
        writer.writerow([r["name"], r["roll_no"], r["class_name"],
                         status, f"{pct}%", r["p"], r["t"]])
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


# ── MJPEG stream ─────────────────────────────────────────────
@app.route("/video_feed")
def video_feed():
    return Response(generate_frames(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


# ─────────────────────────────────────────────────────────────
#  SERVE FRONTEND — open http://localhost:5001 in browser
# ─────────────────────────────────────────────────────────────
from flask import send_from_directory

@app.route("/")
def serve_index():
    return send_from_directory(".", "index.html")

@app.route("/css/<path:filename>")
def serve_css(filename):
    return send_from_directory("css", filename)

@app.route("/js/<path:filename>")
def serve_js(filename):
    return send_from_directory("js", filename)


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  TruSenceVision Backend")
    print("=" * 55)
    os.makedirs(DATASET_PATH, exist_ok=True)
    init_db()
    print("[DB]  Database ready.")
    print("[ENC] Loading face encodings …")
    load_encodings()
    print(f"[ENC] Ready — {len(known_encodings)} face vectors loaded.")
    print(f"[SRV] Opening http://localhost:{PORT} ...")
    print("=" * 55)
    # Auto-open browser after 1.5 seconds
    import threading, webbrowser
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
