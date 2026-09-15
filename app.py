import os
from functools import wraps
from datetime import datetime, timedelta
from flask import Flask, request, redirect, url_for, render_template_string, send_from_directory, jsonify, session
import psycopg2
from psycopg2 import extras
from werkzeug.utils import secure_filename
import requests

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "balsam_clinic_enterprise_2026")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# =====================================================================
# إعدادات قاعدة البيانات السحابية (PostgreSQL)
# =====================================================================
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/balsam_db")

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def initialize_database():
    """ إنشاء الجداول والبيانات الأساسية تلقائياً في PostgreSQL """
    print("جاري تجهيز قاعدة البيانات السحابية...")
    conn = get_db()
    cursor = conn.cursor()

    # 1. جدول المرضى
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Patients (
        Patient_ID SERIAL PRIMARY KEY,
        Full_Name VARCHAR(200) NOT NULL,
        Phone VARCHAR(50) NOT NULL,
        Address VARCHAR(300) NULL,
        Created_At TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. جدول العيادات / التخصصات
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Specialties (
        Specialty_ID SERIAL PRIMARY KEY,
        Name VARCHAR(200) NOT NULL UNIQUE
    );
    """)

    # 3. جدول طلبات المرضى
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Requests (
        Request_ID SERIAL PRIMARY KEY,
        Patient_ID INT NOT NULL REFERENCES Patients(Patient_ID),
        Specialty_ID INT NOT NULL REFERENCES Specialties(Specialty_ID),
        Request_Type VARCHAR(50) NOT NULL DEFAULT 'Normal',
        Disease_Type VARCHAR(300) NULL,
        Visit_Reason TEXT NULL,
        Status VARCHAR(50) NOT NULL DEFAULT 'Pending',
        Created_At TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 4. جدول المرفقات
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Attachments (
        Attachment_ID SERIAL PRIMARY KEY,
        Request_ID INT NOT NULL REFERENCES Requests(Request_ID) ON DELETE CASCADE,
        Attachment_Type VARCHAR(50) NOT NULL,
        File_Name VARCHAR(500) NOT NULL,
        Created_At TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 5. جدول المواعيد
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Appointments (
        Appointment_ID SERIAL PRIMARY KEY,
        Request_ID INT NOT NULL REFERENCES Requests(Request_ID),
        Patient_ID INT NOT NULL REFERENCES Patients(Patient_ID),
        Appointment_Date DATE NOT NULL,
        Patient_Status VARCHAR(50) NOT NULL,
        Created_At TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 6. جدول المستخدمين
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Users (
        User_ID SERIAL PRIMARY KEY,
        Username VARCHAR(100) NOT NULL UNIQUE,
        Password VARCHAR(255) NOT NULL,
        Full_Name VARCHAR(200) NOT NULL,
        Role VARCHAR(50) NOT NULL
    );
    """)

    # 7. إعدادات المركز
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS CenterSettings (
        Setting_ID SERIAL PRIMARY KEY,
        Center_Name VARCHAR(200) NOT NULL,
        Capacity_Ratio INT NOT NULL DEFAULT 100
    );
    """)

    # إضافة العيادات الافتراضية
    specialties = [
        "عيادة الباطنية والقلب",
        "عيادة العظام والمفاصل",
        "مركز غسيل الكلى والأمراض المزمنة",
        "عيادة الأطفال وحديثي الولادة",
        "عيادة الجراحة العامة"
    ]
    for sp in specialties:
        cursor.execute("""
            INSERT INTO Specialties (Name) 
            VALUES (%s) 
            ON CONFLICT (Name) DO NOTHING;
        """, (sp,))

    # إضافة إعداد المركز الافتراضي
    cursor.execute("SELECT 1 FROM CenterSettings LIMIT 1;")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO CenterSettings (Center_Name, Capacity_Ratio) VALUES (%s, %s);", ('عيادة بلسم الرقمية', 100))

    # إضافة المدير الافتراضي
    cursor.execute("""
        INSERT INTO Users (Username, Password, Full_Name, Role) 
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (Username) DO NOTHING;
    """, ('admin', 'admin123', 'مدير النظام', 'Admin'))

    conn.commit()
    cursor.close()
    conn.close()
    print("تم تجهيز قاعدة البيانات PostgreSQL بنجاح.")

# تهيئة الجداول تلقائياً عند بدء تشغيل التطبيق تحت سيرفر Gunicorn
_db_initialized = False

@app.before_request
def run_db_initialization():
    global _db_initialized
    if not _db_initialized:
        try:
            initialize_database()
            _db_initialized = True
        except Exception as e:
            print(f"خطأ أثناء تجهيز قاعدة البيانات: {e}")

# ----------------- إعدادات بوابة Textbee SMS ------txb_zLxMggon9vL3qTbKjbWz9nToJOwpp1D9----------
TEXTBEE_API_KEY = os.getenv("TEXTBEE_API_KEY", "txb_zLxMggon9vL3qTbKjbWz9nToJOwpp1D9")
TEXTBEE_DEVICE_ID = os.getenv("TEXTBEE_DEVICE_ID", "6a9dd76eccb6c72709195e0d")

def send_textbee_sms(phone_number, sms_text):
    url = f"https://api.textbee.dev/api/v1/gateway/devices/{TEXTBEE_DEVICE_ID}/send-sms"
    headers = {
        "x-api-key": TEXTBEE_API_KEY,
        "Content-Type": "application/json"
    }
    phone_number = str(phone_number).strip()
    if phone_number.startswith('+967'):
        phone_number = phone_number[4:]
    elif phone_number.startswith('00967'):
        phone_number = phone_number[5:]
    elif phone_number.startswith('0'):
        phone_number = phone_number[1:]

    payload = {
        "receivers": [phone_number],
        "smsBody": sms_text
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        print("تم إرسال رسالة SMS بنجاح.")
        return True
    except Exception as e:
        print(f"فشل إرسال الرسالة: {e}")
        return False

# ----------------- فحص الصلاحيات (RBAC) -----------------
def login_required(roles=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if roles and session.get('role') not in roles:
                return render_template_string("""
                <div style="background:#f8fafc;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:sans-serif;">
                    <h1 style="font-size:3rem;color:#e11d48;margin-bottom:10px;">403</h1>
                    <h2 style="color:#334155;">عذراً، لا تملك صلاحية الدخول لهذه الصفحة.</h2>
                    <a href="/admin" style="margin-top:20px;color:#0f766e;text-decoration:none;font-weight:bold;">العودة للرئيسية</a>
                </div>
                """), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ----------------- خوارزمية احتساب المواعيد -----------------
def calculate_appointment_date(patient_id, request_type, capacity_ratio):
    if request_type == 'Chronic':
        return datetime.now().date() + timedelta(days=2), "Chronic_Priority"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM Appointments WHERE Patient_ID = %s", (patient_id,))
    past_count = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    is_new = (past_count == 0)
    patient_status = "New" if is_new else "Returning"
    base_date = datetime.now().date()

    if capacity_ratio == 25:
        scheduled_date = base_date + timedelta(days=3 if is_new else 90)
    elif capacity_ratio == 50:
        scheduled_date = base_date + timedelta(days=5 if is_new else 30)
    elif capacity_ratio == 75:
        scheduled_date = base_date + timedelta(days=4 if is_new else 14)
    else:
        scheduled_date = base_date + timedelta(days=2 if is_new else 7)

    return scheduled_date, patient_status

# =====================================================================
# قوالب HTML
# =====================================================================
HTML_PATIENT = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"> <meta name="viewport" content="width=device-width, initial-scale=1.0"> 
<title>عيادة بلسم - بوابة المرضى</title> 
<script src="https://cdn.tailwindcss.com"></script> 
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"> 
<script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script> 
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800&display=swap" rel="stylesheet"> 
<style> body { font-family: 'Tajawal', sans-serif; background-color: #f1f5f9; color: #334155; } .stepper-active { background-color: #0f766e; color: white; border-color: #0f766e; } .stepper-inactive { background-color: #e2e8f0; color: #94a3b8; border-color: #e2e8f0; } .stepper-completed { background-color: #ccfbf1; color: #0f766e; border-color: #0f766e; } input, select, textarea { transition: border-color 0.2s; } input:focus, select:focus, textarea:focus { border-color: #0f766e; box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.1); } </style>
</head>
<body class="min-h-screen flex items-center justify-center p-4">
<div class="w-full max-w-2xl bg-white rounded-2xl shadow-xl border border-slate-200 overflow-hidden"> 
    <div class="bg-teal-700 px-8 py-6 flex items-center gap-4"> 
        <div class="w-14 h-14 bg-white rounded-xl flex items-center justify-center text-3xl text-teal-700 shadow-sm"> <i class="fa-solid fa-notes-medical"></i> </div> 
        <div> <h1 class="text-2xl font-bold text-white tracking-wide">عيادة بلسم الرقمية</h1> <p class="text-teal-100 text-sm mt-1 opacity-90">نظام حجز المواعيد وإدارة المرضى الإلكتروني</p> </div> 
    </div> 
    <div class="flex border-b border-slate-200 bg-slate-50"> 
        <button onclick="switchTab('booking')" id="tab-book" class="flex-1 py-4 text-sm font-bold text-teal-700 border-b-2 border-teal-700 bg-white transition"> <i class="fa-solid fa-calendar-check ml-2"></i>حجز موعد جديد </button> 
        <button onclick="switchTab('status')" id="tab-stat" class="flex-1 py-4 text-sm font-bold text-slate-500 hover:text-slate-700 transition"> <i class="fa-solid fa-clock-rotate-left ml-2"></i>الاستعلام عن طلب </button> 
    </div> 
    <div id="booking-container" class="p-6 sm:p-8"> 
        <div class="mb-10 relative"> 
            <div class="absolute top-1/2 left-0 right-0 h-1 bg-slate-200 -translate-y-1/2 z-0 rounded-full"></div> 
            <div id="progress-line" class="absolute top-1/2 right-0 h-1 bg-teal-600 -translate-y-1/2 z-0 rounded-full transition-all duration-300" style="width: 0%;"></div> 
            <div class="flex justify-between relative z-10"> 
                <div class="step-item flex flex-col items-center gap-2" data-step="1"> 
                    <div class="w-10 h-10 rounded-full stepper-active font-bold flex items-center justify-center text-sm border-2 transition-colors">1</div> 
                    <span class="text-xs font-bold text-teal-700">البيانات</span> 
                </div> 
                <div class="step-item flex flex-col items-center gap-2" data-step="2"> 
                    <div class="w-10 h-10 rounded-full stepper-inactive font-bold flex items-center justify-center text-sm border-2 transition-colors">2</div> 
                    <span class="text-xs font-bold text-slate-400">التشخيص</span> 
                </div> 
                <div class="step-item flex flex-col items-center gap-2" data-step="3"> 
                    <div class="w-10 h-10 rounded-full stepper-inactive font-bold flex items-center justify-center text-sm border-2 transition-colors">3</div> 
                    <span class="text-xs font-bold text-slate-400">الهوية</span> </div> 
                <div class="step-item flex flex-col items-center gap-2" data-step="4"> 
                    <div class="w-10 h-10 rounded-full stepper-inactive font-bold flex items-center justify-center text-sm border-2 transition-colors">4</div> 
                    <span class="text-xs font-bold text-slate-400">التقرير</span> 
                </div> 
            </div> 
        </div> 
        <form id="wizardForm" action="/submit-request" method="POST" enctype="multipart/form-data"> 
            <div class="mb-6 bg-slate-50 p-4 rounded-xl border border-slate-200"> 
                <label class="block text-sm font-bold text-slate-700 mb-3">حدد نوع المسار الطبي:</label> 
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3"> 
                    <label class="flex items-center p-3 rounded-lg border border-slate-300 bg-white cursor-pointer hover:border-teal-500 transition"> 
                        <input type="radio" name="request_type" value="Normal" checked class="w-4 h-4 text-teal-600 border-slate-300 focus:ring-teal-500"> 
                        <div class="mr-3"> <span class="block text-sm font-bold text-slate-800">كشف طبي اعتيادي</span> <span class="text-xs text-slate-500">للحالات والمرضى الجدد</span> </div> 
                    </label> 
                    <label class="flex items-center p-3 rounded-lg border border-slate-300 bg-white cursor-pointer hover:border-orange-500 transition"> 
                        <input type="radio" name="request_type" value="Chronic" class="w-4 h-4 text-orange-600 border-slate-300 focus:ring-orange-500"> 
                        <div class="mr-3"> <span class="block text-sm font-bold text-slate-800">حالة مزمنة (أولوية)</span> <span class="text-xs text-slate-500">غسيل كلى، أمراض مزمنة</span> </div> 
                    </label> 
                </div> 
            </div> 
            <div class="step-pane space-y-5" id="pane-1"> 
                <h3 class="text-lg font-bold text-slate-800 border-b border-slate-200 pb-2">البيانات الشخصية</h3> 
                <div> <label class="block text-sm font-bold text-slate-700 mb-1.5">الاسم الرباعي <span class="text-red-500">*</span></label> <input type="text" id="full_name" name="full_name" required placeholder="أدخل الاسم كما في الهوية الوطنية" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-2.5 text-sm outline-none"> </div> 
                <div> <label class="block text-sm font-bold text-slate-700 mb-1.5">رقم الهاتف <span class="text-red-500">*</span></label> <input type="tel" id="phone" name="phone" required placeholder="77XXXXXXX" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-2.5 text-sm outline-none text-left" dir="ltr"> <p class="text-[11px] text-slate-500 mt-1">سيتم إرسال رسالة نصية (SMS) بتفاصيل الموعد إلى هذا الرقم.</p> </div> 
            </div> 
            <div class="step-pane space-y-5 hidden" id="pane-2"> 
                <h3 class="text-lg font-bold text-slate-800 border-b border-slate-200 pb-2">التشخيص والعيادة المطلوبة</h3> 
                <div> <label class="block text-sm font-bold text-slate-700 mb-1.5">المنطقة / السكن <span class="text-red-500">*</span></label> <input type="text" id="address" name="address" required placeholder="المدينة - الحي" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-2.5 text-sm outline-none"> </div> 
                <div> <label class="block text-sm font-bold text-slate-700 mb-1.5">العيادة المتاحة <span class="text-red-500">*</span></label> 
                    <select name="specialty_id" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-2.5 text-sm outline-none"> 
                        {% for sp in specialties %} <option value="{{ sp[0] }}">{{ sp[1] }}</option> {% endfor %} 
                    </select> 
                </div> 
                <div> <label class="block text-sm font-bold text-slate-700 mb-1.5">التشخيص المبدئي <span class="text-red-500">*</span></label> <input type="text" id="disease_type" name="disease_type" required placeholder="مثال: التهاب مفاصل، فشل كلوي..." class="w-full bg-white border border-slate-300 rounded-lg px-4 py-2.5 text-sm outline-none"> </div> 
                <div> <label class="block text-sm font-bold text-slate-700 mb-1.5">سبب الزيارة والشكوى <span class="text-red-500">*</span></label> <textarea name="visit_reason" rows="2" required placeholder="يرجى وصف الحالة باختصار..." class="w-full bg-white border border-slate-300 rounded-lg px-4 py-2.5 text-sm outline-none"></textarea> </div> 
            </div> 
            <div class="step-pane space-y-5 hidden" id="pane-3"> 
                <h3 class="text-lg font-bold text-slate-800 border-b border-slate-200 pb-2">إثبات الهوية</h3> 
                <div class="bg-blue-50 text-blue-800 p-3 rounded-lg text-xs flex gap-2"> <i class="fa-solid fa-circle-info mt-0.5"></i> <p>يرجى إرفاق صورة واضحة للبطاقة الشخصية لمطابقة البيانات عند الحضور للعيادة.</p> </div> 
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-4"> 
                    <div class="border border-slate-300 rounded-xl p-4 text-center bg-slate-50"> 
                        <i class="fa-solid fa-id-card text-2xl text-slate-400 mb-2"></i> <label class="block text-sm font-bold text-slate-700 mb-2">صورة البطاقة (الوجه الأمامي)</label> 
                        <input type="file" name="id_front" accept="image/*" onchange="previewImg(this, 'preview-front')" class="w-full text-xs text-slate-600 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:bg-slate-200 file:font-bold cursor-pointer"> 
                        <img id="preview-front" class="mt-3 max-h-24 mx-auto rounded border border-slate-200 hidden shadow-sm"> 
                    </div> 
                    <div class="border border-slate-300 rounded-xl p-4 text-center bg-slate-50"> 
                        <i class="fa-solid fa-id-card text-2xl text-slate-400 mb-2"></i> <label class="block text-sm font-bold text-slate-700 mb-2">صورة البطاقة (الوجه الخلفي)</label> 
                        <input type="file" name="id_back" accept="image/*" onchange="previewImg(this, 'preview-back')" class="w-full text-xs text-slate-600 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:bg-slate-200 file:font-bold cursor-pointer"> 
                        <img id="preview-back" class="mt-3 max-h-24 mx-auto rounded border border-slate-200 hidden shadow-sm"> 
                    </div> 
                </div> 
            </div> 
            <div class="step-pane space-y-5 hidden" id="pane-4"> 
                <h3 class="text-lg font-bold text-slate-800 border-b border-slate-200 pb-2">التوثيق الطبي</h3> 
                <div class="border border-teal-200 bg-teal-50 rounded-xl p-6 text-center"> 
                    <div class="w-12 h-12 bg-white text-teal-700 rounded-full flex items-center justify-center mx-auto text-xl mb-3 shadow-sm"> <i class="fa-solid fa-file-medical"></i> </div> 
                    <h4 class="text-sm font-bold text-teal-900 mb-1">التقرير الطبي أو الروشتة المعتمدة</h4> 
                    <p class="text-xs text-teal-700 mb-4">هذه الخطوة إلزامية لإثبات جدية الحالة ومنع الحجوزات الوهمية.</p> 
                    <input type="file" name="medical_report" accept="image/*,.pdf" onchange="previewImg(this, 'preview-report')" class="w-full text-sm text-slate-600 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-teal-600 file:text-white file:font-bold cursor-pointer"> 
                    <img id="preview-report" class="mt-4 max-h-32 mx-auto rounded border border-slate-300 hidden shadow-sm"> 
                </div> 
            </div> 
            <div class="flex justify-between items-center mt-8 pt-5 border-t border-slate-200"> 
                <button type="button" id="btn-prev" onclick="changeStep(-1)" class="px-6 py-2.5 rounded-lg border border-slate-300 bg-white hover:bg-slate-50 text-slate-700 font-bold text-sm transition hidden"> <i class="fa-solid fa-arrow-right ml-2"></i> السابق </button> 
                <button type="button" id="btn-next" onclick="changeStep(1)" class="mr-auto px-8 py-2.5 rounded-lg bg-teal-700 hover:bg-teal-800 text-white font-bold text-sm transition shadow-md"> التالي <i class="fa-solid fa-arrow-left mr-2"></i> </button> 
                <button type="submit" id="btn-submit" class="mr-auto px-8 py-2.5 rounded-lg bg-teal-700 hover:bg-teal-800 text-white font-bold text-sm transition shadow-md hidden flex items-center gap-2"> <i class="fa-solid fa-paper-plane"></i> اعتماد وإرسال الطلب </button> 
            </div> 
        </form> 
    </div> 
    <div id="status-container" class="p-6 sm:10 hidden"> 
        <div class="text-center mb-6"> 
            <i class="fa-solid fa-shield-halved text-4xl text-teal-700 mb-3"></i> 
            <h3 class="text-lg font-bold text-slate-800">استعلام عن حالة الموعد</h3> 
            <p class="text-sm text-slate-500 mt-1">أدخل رقم الهاتف الذي تم التسجيل به للاطلاع على النتيجة</p> 
        </div> 
        <div class="flex gap-2 max-w-md mx-auto"> 
            <input type="tel" id="search_phone" placeholder="رقم الهاتف..." class="flex-1 bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm outline-none focus:border-teal-600 text-left" dir="ltr"> 
            <button onclick="checkStatus()" class="px-6 py-3 bg-teal-700 hover:bg-teal-800 text-white font-bold text-sm rounded-lg transition shadow-sm"> بحث </button> 
        </div> 
        <div id="status-result" class="mt-6 max-w-md mx-auto hidden p-5 bg-white border border-slate-200 rounded-xl shadow-sm"></div> 
    </div> 
    <div class="bg-slate-50 border-t border-slate-200 p-4 text-center flex justify-between items-center px-6"> 
        <span class="text-xs text-slate-400 font-medium">النظام الرقمي لعيادة بلسم © 2026</span> 
        <a href="/login" class="text-xs font-bold text-slate-500 hover:text-teal-700 transition flex items-center gap-1.5"> <i class="fa-solid fa-lock"></i> دخول الموظفين </a> 
    </div> 
</div> 
<script> 
let currentStep = 1; const totalSteps = 4; 
function updateStepper() { 
    for (let i = 1; i <= totalSteps; i++) { 
        const pane = document.getElementById('pane-' + i); 
        const ind = document.querySelector(`.step-item[data-step="${i}"] div`); 
        const txt = document.querySelector(`.step-item[data-step="${i}"] span`); 
        if (i === currentStep) { 
            pane.classList.remove('hidden'); 
            ind.className = 'w-10 h-10 rounded-full stepper-active font-bold flex items-center justify-center text-sm border-2 transition-colors'; 
            txt.className = 'text-xs font-bold text-teal-700'; 
        } else if (i < currentStep) { 
            pane.classList.add('hidden'); 
            ind.className = 'w-10 h-10 rounded-full stepper-completed font-bold flex items-center justify-center text-sm border-2 transition-colors'; 
            ind.innerHTML = '<i class="fa-solid fa-check"></i>'; 
            txt.className = 'text-xs font-bold text-teal-700'; 
        } else { 
            pane.classList.add('hidden'); 
            ind.className = 'w-10 h-10 rounded-full stepper-inactive font-bold flex items-center justify-center text-sm border-2 transition-colors'; 
            ind.innerHTML = i; 
            txt.className = 'text-xs font-bold text-slate-400'; 
        } 
    } 
    document.getElementById('progress-line').style.width = ((currentStep - 1) / (totalSteps - 1) * 100) + '%'; 
    document.getElementById('btn-prev').classList.toggle('hidden', currentStep === 1); 
    document.getElementById('btn-next').classList.toggle('hidden', currentStep === totalSteps); 
    document.getElementById('btn-submit').classList.toggle('hidden', currentStep !== totalSteps); 
} 
function changeStep(delta) { 
    if (delta === 1 && currentStep === 1) { 
        if (!document.getElementById('full_name').value.trim() || !document.getElementById('phone').value.trim()) { 
            Swal.fire({ icon: 'warning', title: 'تنبيه', text: 'يرجى إدخال الاسم ورقم الهاتف.', confirmButtonColor: '#0f766e' }); 
            return; 
        } 
    } 
    currentStep += delta; 
    updateStepper(); 
} 
function previewImg(input, targetId) { 
    if (input.files && input.files[0]) { 
        const reader = new FileReader(); 
        reader.onload = function(e) { 
            const img = document.getElementById(targetId); 
            img.src = e.target.result; 
            img.classList.remove('hidden'); 
        } 
        reader.readAsDataURL(input.files[0]); 
    } 
} 
function switchTab(tab) { 
    document.getElementById('booking-container').classList.toggle('hidden', tab !== 'booking'); 
    document.getElementById('status-container').classList.toggle('hidden', tab === 'booking'); 
    const btnBook = document.getElementById('tab-book'); 
    const btnStat = document.getElementById('tab-stat'); 
    if (tab === 'booking') { 
        btnBook.className = 'flex-1 py-4 text-sm font-bold text-teal-700 border-b-2 border-teal-700 bg-white transition'; 
        btnStat.className = 'flex-1 py-4 text-sm font-bold text-slate-500 hover:text-slate-700 transition border-b border-slate-200 bg-slate-50'; 
    } else { 
        btnStat.className = 'flex-1 py-4 text-sm font-bold text-teal-700 border-b-2 border-teal-700 bg-white transition'; 
        btnBook.className = 'flex-1 py-4 text-sm font-bold text-slate-500 hover:text-slate-700 transition border-b border-slate-200 bg-slate-50'; 
    } 
} 
async function checkStatus() { 
    const phone = document.getElementById('search_phone').value.trim(); 
    if(!phone) return; 
    const res = await fetch('/api/check-status?phone=' + encodeURIComponent(phone)); 
    const data = await res.json(); 
    const box = document.getElementById('status-result'); 
    box.classList.remove('hidden'); 
    if(data.found) { 
        let badgeClass = data.status === 'Approved' ? 'bg-green-100 text-green-800 border-green-200' : (data.status === 'Rejected' ? 'bg-red-100 text-red-800 border-red-200' : 'bg-orange-100 text-orange-800 border-orange-200'); 
        box.innerHTML = ` 
        <div class="space-y-3 text-right border-l-4 border-teal-600 pl-3"> 
            <div class="flex justify-between items-start"> 
                <h4 class="font-bold text-slate-800 text-base">${data.name}</h4> 
                <span class="text-[11px] px-2.5 py-1 rounded-full font-bold border ${badgeClass}">${data.status_arabic}</span> 
            </div> 
            <div class="text-sm text-slate-600">العيادة المطلوبة: <span class="font-bold">${data.specialty}</span></div> 
            ${data.appt_date ? `<div class="bg-slate-50 p-3 rounded-lg border border-slate-200 mt-2 text-sm text-slate-800"><i class="fa-regular fa-calendar-check text-teal-600 ml-1"></i> موعد الحضور المعتمد: <span class="font-bold text-teal-700">${data.appt_date}</span></div>` : '<div class="text-xs text-slate-500 mt-2"><i class="fa-solid fa-hourglass-half ml-1"></i> الطلب قيد الفحص والتدقيق من قبل الإدارة الطبية.</div>'} 
        </div> `; 
    } else { 
        box.innerHTML = `<div class="text-center text-sm text-red-600 font-bold py-2"><i class="fa-solid fa-circle-exclamation text-2xl mb-2 block"></i> لم يتم العثور على أي ملف طبي بهذا الرقم.</div>`; 
    } 
} 
</script> 
{% if msg %} 
<script> 
Swal.fire({ icon: 'success', title: 'اكتملت العملية', text: '{{ msg }}', confirmButtonColor: '#0f766e', confirmButtonText: 'حسناً' }); 
</script> 
{% endif %}
</body>
</html>
"""

HTML_LOGIN = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"> <meta name="viewport" content="width=device-width, initial-scale=1.0"> 
<title>بوابة النظام الإداري</title> 
<script src="https://cdn.tailwindcss.com"></script> 
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"> 
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800&display=swap" rel="stylesheet"> 
<style>body { font-family: 'Tajawal', sans-serif; background-color: #f1f5f9; color: #334155; }</style>
</head>
<body class="min-h-screen flex items-center justify-center p-4">
<div class="w-full max-w-sm bg-white border border-slate-200 rounded-2xl p-8 shadow-xl"> 
    <div class="text-center mb-8"> 
        <div class="w-16 h-16 bg-slate-100 rounded-2xl mx-auto flex items-center justify-center text-3xl text-slate-700 mb-4 border border-slate-200"> <i class="fa-solid fa-lock"></i> </div> 
        <h1 class="text-xl font-bold text-slate-800">تسجيل الدخول للنظام</h1> 
        <p class="text-sm text-slate-500 mt-1">خاص بالكادر الطبي والإداري</p> 
    </div> 
    {% if error %} 
    <div class="mb-5 p-3 bg-red-50 border border-red-200 rounded-lg text-sm font-bold text-red-600 text-center flex items-center justify-center gap-2"> 
        <i class="fa-solid fa-circle-exclamation"></i> <span>{{ error }}</span> 
    </div> 
    {% endif %} 
    <form method="POST" action="/login" class="space-y-4"> 
        <div> 
            <label class="block text-sm font-bold text-slate-700 mb-1.5">اسم المستخدم</label> 
            <div class="relative"> 
                <i class="fa-solid fa-user absolute right-3.5 top-3.5 text-slate-400"></i> 
                <input type="text" name="username" required placeholder="أدخل اسم المستخدم" class="w-full bg-slate-50 border border-slate-300 rounded-lg pr-10 pl-4 py-2.5 text-sm outline-none focus:border-teal-600 focus:bg-white transition text-left" dir="ltr"> 
            </div> 
        </div> 
        <div> 
            <label class="block text-sm font-bold text-slate-700 mb-1.5">كلمة المرور</label> 
            <div class="relative"> 
                <i class="fa-solid fa-key absolute right-3.5 top-3.5 text-slate-400"></i> 
                <input type="password" name="password" required placeholder="••••••••" class="w-full bg-slate-50 border border-slate-300 rounded-lg pr-10 pl-4 py-2.5 text-sm outline-none focus:border-teal-600 focus:bg-white transition text-left" dir="ltr"> 
            </div> 
        </div> 
        <button type="submit" class="w-full mt-2 py-3 bg-slate-800 hover:bg-slate-900 text-white font-bold text-sm rounded-lg transition shadow-md"> دخول النظام </button> 
    </form> 
    <div class="mt-8 pt-5 border-t border-slate-100 text-center"> 
        <a href="/" class="text-sm font-bold text-teal-600 hover:text-teal-800 transition"> <i class="fa-solid fa-arrow-right ml-1"></i> العودة لبوابة المرضى </a> 
    </div> 
</div>
</body>
</html>
"""

HTML_ADMIN = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"> <meta name="viewport" content="width=device-width, initial-scale=1.0"> 
<title>النظام الإداري | عيادة بلسم</title> 
<script src="https://cdn.tailwindcss.com"></script> 
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"> 
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800&display=swap" rel="stylesheet"> 
<style> body { font-family: 'Tajawal', sans-serif; background-color: #f8fafc; color: #334155; } .card-panel { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 1rem; box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px 0 rgba(0, 0, 0, 0.03); } .table-row:hover { background-color: #f8fafc; } </style>
</head>
<body>
<header class="bg-white border-b border-slate-200 sticky top-0 z-40"> 
    <div class="max-w-[90rem] mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between"> 
        <div class="flex items-center gap-3"> 
            <div class="w-8 h-8 bg-teal-700 text-white rounded-lg flex items-center justify-center text-lg shadow-sm"> <i class="fa-solid fa-hospital"></i> </div> 
            <h1 class="text-lg font-bold text-slate-800 hidden sm:block">{{ center[0] }}</h1> 
        </div> 
        <div class="flex items-center gap-5"> 
            {% if current_user.role == 'Admin' %} 
            <form action="/admin/update-capacity" method="POST" class="hidden md:flex items-center bg-slate-50 border border-slate-200 rounded-lg p-1"> 
                <span class="text-xs font-bold text-slate-500 px-3 border-l border-slate-200">مؤشر الطاقة:</span> 
                <div class="flex gap-1 pr-1"> 
                    {% for r in [25, 50, 75, 100] %} 
                    <button type="submit" name="capacity_ratio" value="{{ r }}" class="px-3 py-1 text-xs font-bold rounded-md transition {% if center[1] == r %}bg-teal-600 text-white shadow-sm{% else %}text-slate-600 hover:bg-slate-200{% endif %}"> {{ r }}% </button> 
                    {% endfor %} 
                </div> 
            </form> 
            {% else %} 
            <div class="text-sm font-bold text-slate-600 bg-slate-100 px-4 py-1.5 rounded-lg border border-slate-200"> الطاقة المحددة: <span class="text-teal-700">{{ center[1] }}%</span> </div> 
            {% endif %} 
            <div class="flex items-center gap-3 pl-4 border-l border-slate-200"> 
                <div class="text-left hidden sm:block"> 
                    <p class="text-sm font-bold text-slate-800">{{ current_user.full_name }}</p> 
                    <p class="text-[10px] font-bold text-slate-500">{{ 'مدير النظام (Admin)' if current_user.role == 'Admin' else 'موظف مراجعة (Staff)' }}</p> 
                </div> 
                <div class="w-9 h-9 bg-slate-200 rounded-full flex items-center justify-center text-slate-600"> <i class="fa-solid fa-user"></i> </div> 
            </div> 
            <a href="/logout" class="text-slate-400 hover:text-red-600 transition" title="تسجيل الخروج"> <i class="fa-solid fa-right-from-bracket text-lg"></i> </a> 
        </div> 
    </div> 
</header> 
<main class="flex-1 max-w-[90rem] w-full mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8"> 
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4"> 
        <div class="card-panel p-5 flex items-start justify-between"> 
            <div> <p class="text-sm font-bold text-slate-500 mb-1">طلبات بانتظار الفحص</p> <h3 class="text-3xl font-bold text-slate-800">{{ requests|length }}</h3> </div> 
            <div class="p-3 bg-orange-50 text-orange-600 rounded-xl"><i class="fa-solid fa-file-signature text-xl"></i></div> 
        </div> 
        <div class="card-panel p-5 flex items-start justify-between"> 
            <div> <p class="text-sm font-bold text-slate-500 mb-1">المواعيد المعتمدة</p> <h3 class="text-3xl font-bold text-slate-800">{{ appointments|length }}</h3> </div> 
            <div class="p-3 bg-teal-50 text-teal-600 rounded-xl"><i class="fa-regular fa-calendar-check text-xl"></i></div> 
        </div> 
        <div class="card-panel p-5 flex items-start justify-between"> <div> <p class="text-sm font-bold text-slate-500 mb-1">نسبة الإشغال الحالية</p> <h3 class="text-3xl font-bold text-slate-800">{{ center[1] }}%</h3> </div> <div class="p-3 bg-blue-50 text-blue-600 rounded-xl"><i class="fa-solid fa-chart-pie text-xl"></i></div> </div> 
        <div class="card-panel p-5 flex items-start justify-between"> <div> <p class="text-sm font-bold text-slate-500 mb-1">حالة الاتصال</p> <h3 class="text-sm font-bold text-green-600 mt-2 flex items-center gap-1.5"><i class="fa-solid fa-circle text-[8px]"></i> نشط ومستقر</h3> </div> <div class="p-3 bg-slate-100 text-slate-600 rounded-xl"><i class="fa-solid fa-server text-xl"></i></div> </div> 
    </div> 
    {% if current_user.role == 'Admin' %} 
    <section class="card-panel overflow-hidden"> 
        <div class="bg-slate-50 px-6 py-4 border-b border-slate-200 flex justify-between items-center"> 
            <h2 class="text-base font-bold text-slate-800 flex items-center gap-2"> <i class="fa-solid fa-users-cog text-slate-400"></i> إدارة حسابات الكادر الطبي والموظفين </h2> 
            <span class="text-xs font-bold text-slate-500 bg-white border border-slate-200 px-2.5 py-1 rounded-md">صلاحية الإدارة</span> 
        </div> 
        <div class="p-6"> 
            <form action="/admin/add-user" method="POST" class="grid grid-cols-1 md:grid-cols-5 gap-4 mb-6 items-end"> 
                <div> <label class="block text-xs font-bold text-slate-600 mb-1">الاسم الكامل</label> <input type="text" name="full_name" required class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm outline-none"> </div> 
                <div> <label class="block text-xs font-bold text-slate-600 mb-1">اسم المستخدم</label> <input type="text" name="username" required class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm outline-none text-left" dir="ltr"> </div> 
                <div> <label class="block text-xs font-bold text-slate-600 mb-1">كلمة المرور</label> <input type="password" name="password" required class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm outline-none text-left" dir="ltr"> </div> 
                <div> <label class="block text-xs font-bold text-slate-600 mb-1">الدور والصلاحية</label> <select name="role" class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm outline-none"> <option value="Doctor">طبيب</option> <option value="Staff">موظف استقبال</option> <option value="Admin">مدير نظام</option> </select> </div> 
                <button type="submit" class="bg-slate-800 hover:bg-slate-700 text-white font-bold text-sm rounded-lg py-2 transition shadow-sm"> إضافة المستخدم </button> 
            </form> 
            <div class="border border-slate-200 rounded-xl overflow-hidden"> 
                <table class="w-full text-right text-sm text-slate-600"> 
                    <thead class="bg-slate-50 border-b border-slate-200 text-slate-500"> <tr> <th class="p-3 font-bold">الاسم</th> <th class="p-3 font-bold">اسم الدخول</th> <th class="p-3 font-bold">الصلاحية</th> <th class="p-3 font-bold">إعادة تعيين كلمة المرور</th> </tr> </thead> 
                    <tbody class="divide-y divide-slate-100"> 
                        {% for u in users %} 
                        <tr class="table-row"> 
                            <td class="p-3 font-bold text-slate-800">{{ u[2] }}</td> <td class="p-3">{{ u[1] }}</td> 
                            <td class="p-3"> <span class="px-2 py-1 rounded text-xs font-bold {% if u[3] == 'Admin' %}bg-purple-100 text-purple-700{% elif u[3] == 'Doctor' %}bg-blue-100 text-blue-700{% else %}bg-slate-100 text-slate-700{% endif %}"> {{ u[3] }} </span> </td> 
                            <td class="p-3"> 
                                <form action="/admin/change-password" method="POST" class="flex gap-2"> 
                                    <input type="hidden" name="user_id" value="{{ u[0] }}"> 
                                    <input type="password" name="new_password" required placeholder="كلمة سر جديدة" class="bg-white border border-slate-300 rounded-md px-2 py-1 text-xs outline-none w-32" dir="ltr"> 
                                    <button type="submit" class="bg-slate-200 hover:bg-slate-300 text-slate-700 px-3 py-1 rounded-md text-xs font-bold transition">حفظ</button> 
                                </form> 
                            </td> 
                        </tr> 
                        {% endfor %} 
                    </tbody> 
                </table> 
            </div> 
        </div> 
    </section> 
    {% endif %} 
    <section class="card-panel overflow-hidden"> 
        <div class="bg-white px-6 py-4 border-b border-slate-200 flex justify-between items-center"> 
            <h2 class="text-base font-bold text-slate-800 flex items-center gap-2"> <i class="fa-solid fa-inbox text-teal-600"></i> صندوق الطلبات والمراجعة الطبية </h2> 
            <span class="bg-orange-100 text-orange-700 text-xs font-bold px-3 py-1 rounded-full">{{ requests|length }} بانتظار الفحص</span> 
        </div> 
        {% if requests %} 
        <div class="p-6 grid grid-cols-1 gap-4"> 
            {% for req in requests %} 
            <div class="border {% if req.info[4] == 'Chronic' %}border-orange-200 bg-orange-50/50{% else %}border-slate-200 bg-white{% endif %} rounded-xl p-5 flex flex-col xl:flex-row justify-between gap-6 hover:shadow-md transition-shadow"> 
                <div class="space-y-2 flex-1"> 
                    <div class="flex items-center gap-3 mb-1"> 
                        <h3 class="text-base font-bold text-slate-800">{{ req.info[1] }}</h3> 
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-md {% if req.info[4] == 'Chronic' %}bg-orange-100 text-orange-800 border border-orange-200{% else %}bg-slate-100 text-slate-600 border border-slate-200{% endif %}"> {{ 'مسار مزمن (أولوية)' if req.info[4] == 'Chronic' else 'طلب كشف عادي' }} </span> 
                    </div> 
                    <div class="flex flex-wrap gap-x-5 gap-y-2 text-sm text-slate-600"> 
                        <p><i class="fa-solid fa-phone text-slate-400 ml-1"></i> <span dir="ltr">{{ req.info[2] }}</span></p> 
                        <p><i class="fa-solid fa-location-dot text-slate-400 ml-1"></i> {{ req.info[3] }}</p> 
                        <p><i class="fa-solid fa-stethoscope text-slate-400 ml-1"></i> العيادة: <span class="font-bold text-teal-700">{{ req.info[5] }}</span></p> 
                    </div> 
                    <div class="bg-slate-50 border border-slate-200 p-3 rounded-lg text-sm mt-3"> 
                        <span class="font-bold text-slate-700">التشخيص والشكوى:</span> 
                        <span class="text-slate-600">{{ req.info[6] }} - {{ req.info[7] }}</span> 
                    </div> 
                </div> 
                <div class="flex flex-col justify-center gap-2 min-w-[180px] border-r border-slate-100 pr-6"> 
                    <span class="text-xs font-bold text-slate-500 mb-1">الوثائق الثبوتية والطبية:</span> 
                    {% if req.attachments.get('ID_Front') %} 
                    <button onclick="openModal('/uploads/{{ req.attachments.get('ID_Front') }}')" class="w-full text-right px-3 py-1.5 bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 text-xs font-bold rounded-lg transition shadow-sm"> <i class="fa-regular fa-id-card text-slate-400 ml-1.5"></i> صورة البطاقة (أمام) </button> 
                    {% endif %} 
                    {% if req.attachments.get('ID_Back') %} 
                    <button onclick="openModal('/uploads/{{ req.attachments.get('ID_Back') }}')" class="w-full text-right px-3 py-1.5 bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 text-xs font-bold rounded-lg transition shadow-sm"> <i class="fa-regular fa-id-card text-slate-400 ml-1.5"></i> صورة البطاقة (خلف) </button> 
                    {% endif %} 
                    {% if req.attachments.get('Medical_Report') %} 
                    <button onclick="openModal('/uploads/{{ req.attachments.get('Medical_Report') }}')" class="w-full text-right px-3 py-1.5 bg-teal-50 hover:bg-teal-100 border border-teal-200 text-teal-700 text-xs font-bold rounded-lg transition shadow-sm mt-1"> <i class="fa-solid fa-file-medical text-teal-600 ml-1.5"></i> التقرير الطبي المرفق </button> 
                    {% endif %} 
                </div> 
                <div class="flex flex-col justify-center gap-2 min-w-[150px] border-r border-slate-100 pr-6"> 
                    <form action="/admin/decision/{{ req.info[0] }}/approve" method="POST"> 
                        <button type="submit" class="w-full py-2.5 bg-teal-600 hover:bg-teal-700 text-white font-bold rounded-lg text-xs shadow-sm transition flex items-center justify-center gap-1.5"> <i class="fa-solid fa-check"></i> قبول واعتماد </button> 
                    </form> 
                    <form action="/admin/decision/{{ req.info[0] }}/reject" method="POST"> 
                        <button type="submit" class="w-full py-2 bg-white hover:bg-slate-50 text-red-600 font-bold rounded-lg text-xs border border-slate-200 transition flex items-center justify-center gap-1.5"> <i class="fa-solid fa-xmark"></i> رفض الطلب </button> 
                    </form> 
                </div> 
            </div> 
            {% endfor %} 
        </div> 
        {% else %} 
        <div class="p-12 text-center text-slate-500"> 
            <div class="w-16 h-16 bg-slate-50 rounded-full flex items-center justify-center text-3xl mx-auto mb-3 border border-slate-200"> <i class="fa-solid fa-clipboard-check text-slate-300"></i> </div> 
            <p class="text-sm font-bold">لا توجد طلبات معلقة.</p> 
            <p class="text-xs text-slate-400 mt-1">تمت مراجعة جميع ملفات المرضى بنجاح.</p> 
        </div> 
        {% endif %} 
    </section> 
    <section class="card-panel overflow-hidden"> 
        <div class="bg-white px-6 py-4 border-b border-slate-200"> 
            <h2 class="text-base font-bold text-slate-800 flex items-center gap-2"> <i class="fa-solid fa-calendar-days text-slate-400"></i> سجل المواعيد الطبية المجدولة </h2> 
        </div> 
        <div class="overflow-x-auto"> 
            <table class="w-full text-right text-sm text-slate-600"> 
                <thead class="bg-slate-50 border-b border-slate-200 text-slate-500 text-xs"> 
                    <tr> <th class="p-4 font-bold">رقم الحجز</th> <th class="p-4 font-bold">اسم المريض</th> <th class="p-4 font-bold">رقم الهاتف</th> <th class="p-4 font-bold">تاريخ الحضور المقرر</th> <th class="p-4 font-bold">التصنيف الآلي</th> <th class="p-4 font-bold">حالة الإشعار</th> </tr> 
                </thead> 
                <tbody class="divide-y divide-slate-100"> 
                    {% for appt in appointments %} 
                    <tr class="table-row"> 
                        <td class="p-4 font-mono font-bold text-slate-800">#{{ appt[0] }}</td> 
                        <td class="p-4 font-bold text-slate-800">{{ appt[1] }}</td> 
                        <td class="p-4" dir="ltr"><span class="float-right">{{ appt[2] }}</span></td> 
                        <td class="p-4 font-bold text-teal-700">{{ appt[3] }}</td> 
                        <td class="p-4"> 
                            <span class="px-2.5 py-1 rounded-md text-[11px] font-bold {% if appt[4] == 'New' %}bg-green-100 text-green-700 border border-green-200{% elif appt[4] == 'Chronic_Priority' %}bg-orange-100 text-orange-700 border border-orange-200{% else %}bg-slate-100 text-slate-600 border border-slate-200{% endif %}"> 
                                {{ 'مريض جديد (أولوية)' if appt[4] == 'New' else ('حالة مزمنة' if appt[4] == 'Chronic_Priority' else 'مريض متكرر') }} 
                            </span> 
                        </td> 
                        <td class="p-4 text-green-600 font-bold text-xs flex items-center gap-1.5"> 
                            <i class="fa-solid fa-check-double"></i> أُرسلت (SMS) 
                        </td> 
                    </tr> 
                    {% endfor %} 
                </tbody> 
            </table> 
        </div> 
    </section> 
</main> 
<div id="imageModal" class="fixed inset-0 bg-slate-900/80 z-50 hidden flex items-center justify-center p-4 backdrop-blur-sm"> 
    <div class="bg-white rounded-2xl p-2 max-w-2xl w-full relative shadow-2xl"> 
        <div class="flex justify-between items-center p-3 border-b border-slate-100 mb-2"> 
            <h3 class="text-sm font-bold text-slate-800">معاينة المستند</h3> 
            <button onclick="closeModal()" class="w-8 h-8 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-600 flex items-center justify-center transition"> <i class="fa-solid fa-xmark"></i> </button> 
        </div> 
        <div class="p-2 bg-slate-50 rounded-xl border border-slate-100"> 
            <img id="modalImg" src="" class="max-h-[75vh] mx-auto rounded-lg shadow-sm"> 
        </div> 
    </div> 
</div> 
<script> 
function openModal(src) { document.getElementById('modalImg').src = src; document.getElementById('imageModal').classList.remove('hidden'); } 
function closeModal() { document.getElementById('imageModal').classList.add('hidden'); } 
</script>
</body>
</html>
"""

# =====================================================================
# إدارة وتخزين الملفات
# =====================================================================
def save_attachment(file, request_id, attachment_type):
    if not file or not file.filename:
        return None
    original_name = secure_filename(file.filename)
    if not original_name:
        return None
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    filename = f"{request_id}_{attachment_type}_{timestamp}_{original_name}"
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)
    return filename

# =====================================================================
# المسارات ونقاط الوصول
# =====================================================================
@app.route('/')
def home():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT Specialty_ID, Name FROM Specialties ORDER BY Specialty_ID")
    specialties = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template_string(HTML_PATIENT, specialties=specialties, msg=request.args.get('msg'))

@app.route('/submit-request', methods=['POST'])
def submit_request():
    full_name = request.form.get('full_name', '').strip()
    phone = request.form.get('phone', '').strip()
    address = request.form.get('address', '').strip()
    specialty_id = request.form.get('specialty_id')
    disease_type = request.form.get('disease_type', '').strip()
    visit_reason = request.form.get('visit_reason', '').strip()
    request_type = request.form.get('request_type', 'Normal')

    if not full_name or not phone or not address or not specialty_id:
        return redirect(url_for('home', msg='يرجى إكمال البيانات المطلوبة.'))

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT Patient_ID FROM Patients WHERE Phone = %s ORDER BY Patient_ID DESC LIMIT 1", (phone,))
        patient = cursor.fetchone()

        if patient:
            patient_id = patient[0]
            cursor.execute("UPDATE Patients SET Full_Name = %s, Address = %s WHERE Patient_ID = %s", (full_name, address, patient_id))
        else:
            cursor.execute("""
                INSERT INTO Patients (Full_Name, Phone, Address) 
                VALUES (%s, %s, %s) 
                RETURNING Patient_ID;
            """, (full_name, phone, address))
            patient_id = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO Requests (Patient_ID, Specialty_ID, Request_Type, Disease_Type, Visit_Reason, Status) 
            VALUES (%s, %s, %s, %s, %s, 'Pending') 
            RETURNING Request_ID;
        """, (patient_id, int(specialty_id), request_type, disease_type, visit_reason))
        request_id = cursor.fetchone()[0]

        files_to_save = [
            ('id_front', 'ID_Front'),
            ('id_back', 'ID_Back'),
            ('medical_report', 'Medical_Report')
        ]
        for field_name, attachment_type in files_to_save:
            file = request.files.get(field_name)
            if file and file.filename:
                saved_name = save_attachment(file, request_id, attachment_type)
                if saved_name:
                    cursor.execute("""
                        INSERT INTO Attachments (Request_ID, Attachment_Type, File_Name) 
                        VALUES (%s, %s, %s)
                    """, (request_id, attachment_type, saved_name))

        conn.commit()
        return redirect(url_for('home', msg='تم إرسال طلبك بنجاح، وسيتم فحصه من قبل الإدارة الطبية.'))
    except Exception as e:
        conn.rollback()
        print(f"خطأ أثناء حفظ طلب المريض: {e}")
        return redirect(url_for('home', msg='حدث خطأ أثناء حفظ الطلب، يرجى المحاولة مرة أخرى.'))
    finally:
        cursor.close()
        conn.close()

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/check-status')
def check_status():
    phone = request.args.get('phone', '').strip()
    if not phone:
        return jsonify({"found": False})

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.Request_ID, p.Full_Name, r.Status, s.Name, a.Appointment_Date 
        FROM Requests r 
        INNER JOIN Patients p ON r.Patient_ID = p.Patient_ID 
        INNER JOIN Specialties s ON r.Specialty_ID = s.Specialty_ID 
        LEFT JOIN Appointments a ON r.Request_ID = a.Request_ID 
        WHERE p.Phone = %s 
        ORDER BY r.Request_ID DESC 
        LIMIT 1;
    """, (phone,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        return jsonify({"found": False})

    status = row[2]
    status_arabic = 'تم قبول الطلب' if status == 'Approved' else ('تم رفض الطلب' if status == 'Rejected' else 'قيد المراجعة')
    appointment_date = row[4].strftime("%Y-%m-%d") if row[4] else None

    return jsonify({
        "found": True,
        "name": row[1],
        "status": status,
        "status_arabic": status_arabic,
        "specialty": row[3],
        "appt_date": appointment_date
    })

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template_string(HTML_LOGIN, error=None)

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT User_ID, Username, Password, Full_Name, Role FROM Users WHERE Username = %s", (username,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user or user[2] != password:
        return render_template_string(HTML_LOGIN, error='اسم المستخدم أو كلمة المرور غير صحيحة.')

    session['user_id'] = user[0]
    session['username'] = user[1]
    session['full_name'] = user[3]
    session['role'] = user[4]
    return redirect(url_for('admin'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/admin')
@login_required()
def admin():
    conn = get_db()
    cursor = conn.cursor()

    current_user = {
        'user_id': session.get('user_id'),
        'username': session.get('username'),
        'full_name': session.get('full_name'),
        'role': session.get('role')
    }

    cursor.execute("SELECT Center_Name, Capacity_Ratio FROM CenterSettings LIMIT 1")
    center = cursor.fetchone() or ('عيادة بلسم الرقمية', 100)

    cursor.execute("SELECT User_ID, Username, Full_Name, Role FROM Users ORDER BY User_ID")
    users = cursor.fetchall()

    cursor.execute("""
        SELECT r.Request_ID, p.Full_Name, p.Phone, p.Address, r.Request_Type, s.Name, r.Disease_Type, r.Visit_Reason 
        FROM Requests r 
        INNER JOIN Patients p ON r.Patient_ID = p.Patient_ID 
        INNER JOIN Specialties s ON r.Specialty_ID = s.Specialty_ID 
        WHERE r.Status = 'Pending' 
        ORDER BY CASE WHEN r.Request_Type = 'Chronic' THEN 0 ELSE 1 END, r.Created_At ASC
    """)
    request_rows = cursor.fetchall()

    requests_list = []
    for row in request_rows:
        request_id = row[0]
        cursor.execute("SELECT Attachment_Type, File_Name FROM Attachments WHERE Request_ID = %s", (request_id,))
        attachment_rows = cursor.fetchall()
        attachments = {att[0]: att[1] for att in attachment_rows}
        requests_list.append({'info': row, 'attachments': attachments})

    cursor.execute("""
        SELECT a.Appointment_ID, p.Full_Name, p.Phone, a.Appointment_Date, a.Patient_Status 
        FROM Appointments a 
        INNER JOIN Patients p ON a.Patient_ID = p.Patient_ID 
        ORDER BY a.Appointment_Date ASC
    """)
    appointments = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template_string(
        HTML_ADMIN,
        current_user=current_user,
        center=center,
        users=users,
        requests=requests_list,
        appointments=appointments
    )

@app.route('/admin/update-capacity', methods=['POST'])
@login_required(roles=['Admin'])
def update_capacity():
    try:
        capacity_ratio = int(request.form.get('capacity_ratio', 100))
    except (ValueError, TypeError):
        return redirect(url_for('admin'))

    if capacity_ratio in [25, 50, 75, 100]:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE CenterSettings SET Capacity_Ratio = %s WHERE Setting_ID = (SELECT Setting_ID FROM CenterSettings ORDER BY Setting_ID LIMIT 1)", (capacity_ratio,))
        conn.commit()
        cursor.close()
        conn.close()

    return redirect(url_for('admin'))

@app.route('/admin/add-user', methods=['POST'])
@login_required(roles=['Admin'])
def add_user():
    full_name = request.form.get('full_name', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'Staff')
    if role not in ['Admin', 'Doctor', 'Staff']:
        role = 'Staff'

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO Users (Username, Password, Full_Name, Role) VALUES (%s, %s, %s, %s)", (username, password, full_name, role))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"خطأ في إضافة المستخدم: {e}")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin'))

@app.route('/admin/change-password', methods=['POST'])
@login_required(roles=['Admin'])
def change_password():
    user_id = request.form.get('user_id')
    new_password = request.form.get('new_password')
    if user_id and new_password:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE Users SET Password = %s WHERE User_ID = %s", (new_password, int(user_id)))
        conn.commit()
        cursor.close()
        conn.close()

    return redirect(url_for('admin'))

@app.route('/admin/decision/<int:request_id>/<action>', methods=['POST'])
@login_required(roles=['Admin', 'Staff', 'Doctor'])
def decision(request_id, action):
    if action not in ['approve', 'reject']:
        return redirect(url_for('admin'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.Patient_ID, r.Request_Type, p.Phone, p.Full_Name 
        FROM Requests r 
        INNER JOIN Patients p ON r.Patient_ID = p.Patient_ID 
        WHERE r.Request_ID = %s
    """, (request_id,))
    request_data = cursor.fetchone()

    if not request_data:
        cursor.close()
        conn.close()
        return redirect(url_for('admin'))

    patient_id, request_type, phone, full_name = request_data

    if action == 'reject':
        cursor.execute("UPDATE Requests SET Status = 'Rejected' WHERE Request_ID = %s", (request_id,))
        conn.commit()
        cursor.close()
        conn.close()
        send_textbee_sms(phone, f"عيادة بلسم: نعتذر، تم رفض طلب الموعد الخاص بـ {full_name}.")
        return redirect(url_for('admin'))

    cursor.execute("SELECT Capacity_Ratio FROM CenterSettings ORDER BY Setting_ID LIMIT 1")
    center_row = cursor.fetchone()
    capacity_ratio = center_row[0] if center_row else 100

    scheduled_date, patient_status = calculate_appointment_date(patient_id, request_type, capacity_ratio)
    scheduled_date_sql = scheduled_date.strftime("%Y-%m-%d")

    cursor.execute("UPDATE Requests SET Status = 'Approved' WHERE Request_ID = %s", (request_id,))
    cursor.execute("""
        INSERT INTO Appointments (Request_ID, Patient_ID, Appointment_Date, Patient_Status) 
        VALUES (%s, %s, %s, %s)
    """, (request_id, patient_id, scheduled_date_sql, patient_status))
    conn.commit()
    cursor.close()
    conn.close()

    formatted_date = scheduled_date.strftime("%Y-%m-%d")
    if patient_status == "Chronic_Priority":
        sms_text = f"عيادة بلسم: تم اعتماد طلبك يا {full_name}. موعد الحضور: {formatted_date}. الحالة مصنفة كأولوية."
    else:
        sms_text = f"عيادة بلسم: تم اعتماد طلبك يا {full_name}. موعد الحضور: {formatted_date}."

    send_textbee_sms(phone, sms_text)
    return redirect(url_for('admin'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
