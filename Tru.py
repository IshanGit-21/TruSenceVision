import cv2
import os
import numpy as np

# -----------------------------
# CONFIGURATION
# -----------------------------
DATASET_PATH = "Dataset"   # Folder containing subfolders for each person
FACE_SIZE = (200, 200)     # Standard face size for training

# Load face detection model
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

faces = []
labels = []
label_map = {}
current_id = 0

# -----------------------------
# LOAD DATASET
# -----------------------------
for person_name in os.listdir(DATASET_PATH):
    person_folder = os.path.join(DATASET_PATH, person_name)

    if not os.path.isdir(person_folder):
        continue

    label_map[current_id] = person_name

    for image_name in os.listdir(person_folder):
        img_path = os.path.join(person_folder, image_name)

        img = cv2.imread(img_path)
        if img is None:
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        detected_faces = face_cascade.detectMultiScale(gray, 1.3, 5)

        for (x, y, w, h) in detected_faces:
            face = gray[y:y+h, x:x+w]
            face = cv2.resize(face, FACE_SIZE)

            faces.append(face)
            labels.append(current_id)

    current_id += 1

labels = np.array(labels)

print("Training model...")

# -----------------------------
# TRAIN MODEL
# -----------------------------
recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.train(faces, labels)

print("Training complete. Starting camera...")

# -----------------------------
# START CAMERA
# -----------------------------
cap = cv2.VideoCapture(0)

while True:

    ret, frame = cap.read()
    if not ret:
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    detected_faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in detected_faces:

        face_roi = gray[y:y+h, x:x+w]
        face_roi = cv2.resize(face_roi, FACE_SIZE)

        label, confidence = recognizer.predict(face_roi)

        name = "Unknown"

        if confidence < 80:
            name = label_map[label]

        cv2.rectangle(frame, (x, y), (x+w, y+h), (0,255,0), 2)

        cv2.putText(
            frame,
            f"{name} ({int(confidence)})",
            (x, y-10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0,255,0),
            2
        )

    cv2.imshow("TruSenseVision", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()