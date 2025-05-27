# Updated app.py with camera-based registration and attendance matching
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify
import pandas as pd
import qrcode
import uuid
import os
import json
import base64
import face_recognition
from datetime import datetime
from pytz import timezone
from io import BytesIO
import math

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PHOTO_FOLDER'] = 'static/photos'
app.config['DATABASE_FILE'] = 'students.json'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['PHOTO_FOLDER'], exist_ok=True)

sessions = {}  # session_id -> session details
attendance = {}  # session_id -> {ip: [rolls]}
BASE_URL = 'https://attendance-system-project.onrender.com/'

# ------------------- Utility -------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def load_students():
    if os.path.exists(app.config['DATABASE_FILE']):
        with open(app.config['DATABASE_FILE']) as f:
            return json.load(f)
    return {}

def save_students(data):
    with open(app.config['DATABASE_FILE'], 'w') as f:
        json.dump(data, f, indent=4)

# ------------------- Routes -------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        roll = request.form['roll']
        photo_data = request.form['photo']

        photo_data = photo_data.split(',')[1]
        img_data = base64.b64decode(photo_data)
        filename = f"{roll}.jpg"
        filepath = os.path.join(app.config['PHOTO_FOLDER'], filename)

        with open(filepath, 'wb') as f:
            f.write(img_data)

        students = load_students()
        students[roll] = {'name': name, 'photo_path': filepath, 'attendance': []}
        save_students(students)

        return jsonify({'status': 'success'})
    return render_template('register.html')

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['file']
    radius = request.form.get('radius')
    latitude = request.form.get('latitude')
    longitude = request.form.get('longitude')

    if not latitude or not longitude or not radius:
        return 'Location and radius are required.'

    try:
        latitude = float(latitude)
        longitude = float(longitude)
        radius = float(radius)
    except ValueError:
        return 'Invalid location or radius values.'

    if file:
        df = pd.read_csv(file)
        session_id = str(uuid.uuid4())
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], session_id + '.csv')
        df.to_csv(filepath, index=False)

        sessions[session_id] = {
            'filename': filepath,
            'latitude': latitude,
            'longitude': longitude,
            'radius': radius
        }

        url = BASE_URL + 'scan/' + session_id
        qr = qrcode.make(url)
        qr_path = os.path.join('static', 'qr', session_id + '.png')
        os.makedirs(os.path.dirname(qr_path), exist_ok=True)
        qr.save(qr_path)

        return render_template('qr_display.html', qr_path='qr/' + session_id + '.png', session_id=session_id)
    return 'File not uploaded.'

@app.route('/scan/<session_id>')
def scan(session_id):
    session = sessions.get(session_id)
    if not session:
        return 'Invalid session ID.'

    df = pd.read_csv(session['filename'])
    students = df.to_dict(orient='records')
    roll_col = df.columns[0]
    name_col = df.columns[1]

    return render_template('student_list.html', students=students, session_id=session_id, roll_col=roll_col, name_col=name_col)

@app.route('/mark', methods=['POST'])
def mark_attendance():
    session_id = request.form['session_id']
    roll = request.form['roll_number']
    lat = float(request.form['latitude'])
    lon = float(request.form['longitude'])
    photo_data = request.form['photo']
    user_ip = request.remote_addr

    session = sessions.get(session_id)
    if not session:
        return 'Invalid session.'

    dist = haversine(lat, lon, session['latitude'], session['longitude'])
    if dist > session['radius']:
        return f'Outside allowed area (Distance: {dist:.2f} m). Attendance not marked.'

    # Face verification
    students = load_students()
    if roll not in students:
        return 'Student not registered with photo.'

    stored_path = students[roll]['photo_path']
    photo_data = photo_data.split(',')[1]
    img_data = base64.b64decode(photo_data)
    temp_path = os.path.join(app.config['PHOTO_FOLDER'], f'temp_{roll}.jpg')
    with open(temp_path, 'wb') as f:
        f.write(img_data)

    try:
        ref_enc = face_recognition.face_encodings(face_recognition.load_image_file(stored_path))[0]
        new_enc = face_recognition.face_encodings(face_recognition.load_image_file(temp_path))[0]
        match = face_recognition.compare_faces([ref_enc], new_enc)[0]
    except:
        match = False

    if not match:
        return 'Face does not match.'

    if user_ip in attendance.get(session_id, {}) and roll in attendance[session_id][user_ip]:
        return 'Attendance already marked.'

    df = pd.read_csv(session['filename'])
    today = datetime.now(timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
    if today not in df.columns:
        df[today] = 0
    df.loc[df[df.columns[0]] == roll, today] = 1

    # Update percentage
    df['Attendance %'] = df.iloc[:, 2:].mean(axis=1) * 100
    df.to_csv(session['filename'], index=False)

    attendance.setdefault(session_id, {}).setdefault(user_ip, []).append(roll)

    return 'Attendance marked successfully!'

@app.route('/download/<session_id>')
def download(session_id):
    session = sessions.get(session_id)
    if not session:
        return 'Invalid session ID.'

    df = pd.read_csv(session['filename'])
    output = BytesIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return send_file(output, mimetype='text/csv', as_attachment=True, download_name=f'attendance_{session_id}.csv')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
