from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.models import User
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import connection, transaction
from django.db.models import Q, Count
from django.core.paginator import Paginator
from datetime import timedelta, datetime, time as dt_time
from zoneinfo import ZoneInfo
from functools import wraps
import os
import random
import string
import urllib.request
import urllib.parse
import jdatetime

from .models import (
    Patient, Appointment, OTPRequest, Doctor, DoctorTimeOff,
    StaffProfile, ClinicSettings, AuditLog, generate_tracking_code
)

# منطقه زمانی واحد کلینیک (ایران - تهران)
TEHRAN_TZ = ZoneInfo("Asia/Tehran")
PERSIAN_WEEKDAYS = ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه']


def get_tehran_now():
    return datetime.now(TEHRAN_TZ)


# ==============================================================================
# ۱. همگام‌ساز خودکار دیتابیس (بدون نیاز به اجرای makemigrations در کنسول)
# ==============================================================================
_SCHEMA_CHECKED = False

def ensure_mahlin_schema():
    global _SCHEMA_CHECKED
    if _SCHEMA_CHECKED:
        return
    try:
        with connection.cursor() as cursor:
            # ۱. جدول Doctor
            cursor.execute("PRAGMA table_info(booking_doctor)")
            doc_cols = [row[1] for row in cursor.fetchall()]
            if doc_cols:
                if 'bio' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN bio TEXT DEFAULT ''")
                if 'working_days' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN working_days VARCHAR(150) DEFAULT 'شنبه تا چهارشنبه'")
                if 'working_hours' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN working_hours VARCHAR(100) DEFAULT '۰۹:۰۰ تا ۲۰:۰۰'")
                if 'work_days_mask' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN work_days_mask VARCHAR(30) DEFAULT '0,1,2,3,4'")
                if 'work_start' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN work_start VARCHAR(5) DEFAULT '09:00'")
                if 'work_end' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN work_end VARCHAR(5) DEFAULT '20:00'")
                if 'slot_duration' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN slot_duration INTEGER DEFAULT 30")
                if 'is_active' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN is_active bool DEFAULT 1")

            # ۲. جدول Patient
            cursor.execute("PRAGMA table_info(booking_patient)")
            pat_cols = [row[1] for row in cursor.fetchall()]
            if pat_cols and 'medical_notes' not in pat_cols:
                cursor.execute("ALTER TABLE booking_patient ADD COLUMN medical_notes TEXT DEFAULT ''")

            # ۳. جدول Appointment
            cursor.execute("PRAGMA table_info(booking_appointment)")
            app_cols = [row[1] for row in cursor.fetchall()]
            if app_cols:
                if 'status' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN status VARCHAR(20) DEFAULT 'pending'")
                    cursor.execute("UPDATE booking_appointment SET status = 'confirmed' WHERE is_confirmed = 1")
                if 'cancel_reason' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN cancel_reason VARCHAR(255) DEFAULT ''")
                if 'end_time' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN end_time time NULL")
                if 'tracking_code' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN tracking_code VARCHAR(20) DEFAULT 'MHL-100000'")
                if 'doctor_note' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN doctor_note TEXT DEFAULT ''")
                if 'sms_status' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN sms_status VARCHAR(30) DEFAULT 'not_configured'")
                if 'history_log' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN history_log TEXT DEFAULT ''")
                if 'created_at' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN created_at datetime NULL")

            # ۴. جدول DoctorTimeOff
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS booking_doctortimeoff (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doctor_id INTEGER NOT NULL REFERENCES booking_doctor(id) ON DELETE CASCADE,
                    date date NOT NULL,
                    is_full_day bool NOT NULL DEFAULT 1,
                    start_time time NULL,
                    end_time time NULL,
                    reason VARCHAR(200) NOT NULL DEFAULT 'مرخصی پزشک',
                    created_at datetime NOT NULL
                )
            """)

            # ۵. جدول StaffProfile
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS booking_staffprofile (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE REFERENCES auth_user(id) ON DELETE CASCADE,
                    full_name VARCHAR(100) NOT NULL,
                    phone VARCHAR(15) NOT NULL UNIQUE,
                    role VARCHAR(20) NOT NULL DEFAULT 'receptionist',
                    is_active bool NOT NULL DEFAULT 1,
                    created_at datetime NOT NULL
                )
            """)

            # ۶. جدول ClinicSettings
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS booking_clinicsettings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clinic_name VARCHAR(120) NOT NULL,
                    slogan VARCHAR(150) NOT NULL,
                    phone VARCHAR(50) NOT NULL,
                    address TEXT NOT NULL,
                    map_link VARCHAR(300) NOT NULL DEFAULT '',
                    working_hours VARCHAR(200) NOT NULL,
                    holidays_notice VARCHAR(250) NOT NULL DEFAULT '',
                    booking_rules TEXT NOT NULL,
                    default_slot_duration INTEGER NOT NULL DEFAULT 30
                )
            """)
            cursor.execute("PRAGMA table_info(booking_clinicsettings)")
            cs_cols = [row[1] for row in cursor.fetchall()]
            if cs_cols and 'default_slot_duration' not in cs_cols:
                cursor.execute("ALTER TABLE booking_clinicsettings ADD COLUMN default_slot_duration INTEGER DEFAULT 30")

            # ۷. جدول AuditLog
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS booking_auditlog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor VARCHAR(100) NOT NULL,
                    action_type VARCHAR(80) NOT NULL,
                    details TEXT NOT NULL,
                    created_at datetime NOT NULL
                )
            """)
        _SCHEMA_CHECKED = True
    except Exception as e:
        print("Schema sync warning:", e)


# ==============================================================================
# ۲. توابع کمکی، تاریخ، پیامک واقعی و موتور محاسبه زمان‌های آزاد
# ==============================================================================
def to_jalali_str(greg_date):
    if not greg_date:
        return ""
    j = jdatetime.date.fromgregorian(date=greg_date)
    return f"{j.year}/{j.month:02d}/{j.day:02d}"


def get_jalali_weekday_name(greg_date):
    j = jdatetime.date.fromgregorian(date=greg_date)
    return PERSIAN_WEEKDAYS[j.weekday()]


def parse_flexible_date(date_str):
    clean = str(date_str).strip().replace('-', '/')
    parts = [int(p) for p in clean.split('/')]
    if parts[0] < 1600:
        return jdatetime.date(parts[0], parts[1], parts[2]).togregorian()
    return datetime(parts[0], parts[1], parts[2]).date()


def parse_time_str(t_str):
    clean = str(t_str).strip()[:5]
    parts = [int(p) for p in clean.split(':')]
    return dt_time(parts[0], parts[1])


def add_minutes_to_time(t_obj, minutes):
    dummy = datetime(2026, 1, 1, t_obj.hour, t_obj.minute) + timedelta(minutes=int(minutes))
    return dummy.time()


def append_history(existing_log, actor_name, action_text):
    now_j = to_jalali_str(get_tehran_now().date())
    now_t = get_tehran_now().strftime("%H:%M")
    line = f"[{now_j} {now_t} - {actor_name}]: {action_text}"
    if existing_log:
        return f"{existing_log}\n{line}"
    return line


def send_real_sms(receptor_phone, message_text):
    """
    ارسال پیامک از طریق وب‌سرویس واقعی در صورت وجود SMS_API_KEY در متغیرهای محیطی سرور.
    در صورت عدم تنظیم کلید، هیچ شبیه‌سازی ساختگی انجام نمی‌شود و وضعیت شفاف برگردانده می‌شود.
    """
    api_key = os.environ.get('SMS_API_KEY', '').strip()
    sender = os.environ.get('SMS_SENDER', '').strip()

    if not api_key:
        return 'not_configured', 'سرویس پیامک در متغیرهای محیطی سرور (SMS_API_KEY) پیکربندی نشده است.'

    try:
        params = {'receptor': receptor_phone, 'message': message_text}
        if sender:
            params['sender'] = sender
        encoded_params = urllib.parse.urlencode(params)
        url = f"https://api.kavenegar.com/v1/{api_key}/sms/send.json?{encoded_params}"
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                return 'sent', 'پیامک اطلاع‌رسانی با موفقیت ارسال شد.'
            return 'failed', f'پاسخ ناموفق از درگاه پیامک (کد {response.status}).'
    except Exception as e:
        return 'failed', f'عدم برقراری ارتباط با درگاه پیامک ({str(e)[:50]}).'


def calculate_doctor_slots(doctor, target_date, exclude_appointment_id=None):
    """
    محاسبه دقیق ساعت‌های آزاد و اشغال‌شده پزشک در یک تاریخ مشخص بر اساس:
    - فعال بودن پزشک
    - عدم گذشتن از تاریخ/ساعت فعلی (به وقت تهران)
    - روزهای کاری هفته پزشک (work_days_mask)
    - مرخصی‌های روزانه یا ساعتی پزشک (DoctorTimeOff)
    - نوبت‌های ثبت‌شده غیرلغوشده و مدت زمان هر نوبت (slot_duration)
    """
    ensure_mahlin_schema()
    now_tehran = get_tehran_now()
    today_tehran = now_tehran.date()

    if not doctor.is_active:
        return [], False, "این پزشک در حال حاضر غیرفعال است."

    if target_date < today_tehran:
        return [], False, "امکان رزرو نوبت برای تاریخ‌های گذشته وجود ندارد."

    j_date = jdatetime.date.fromgregorian(date=target_date)
    j_weekday = j_date.weekday()  # 0=شنبه ... 6=جمعه

    allowed_days = [int(x.strip()) for x in (doctor.work_days_mask or "0,1,2,3,4").split(',') if x.strip().isdigit()]
    if j_weekday not in allowed_days:
        return [], False, f"پزشک در روزهای {PERSIAN_WEEKDAYS[j_weekday]} برنامه کاری ندارد."

    # بررسی مرخصی‌ها و زمان‌های مسدود پزشک
    time_offs = list(DoctorTimeOff.objects.filter(doctor=doctor, date=target_date))
    for toff in time_offs:
        if toff.is_full_day:
            return [], False, f"پزشک در این تاریخ حضور ندارد ({toff.reason})."

    try:
        start_t = parse_time_str(doctor.work_start or "09:00")
        end_t = parse_time_str(doctor.work_end or "20:00")
    except Exception:
        start_t = dt_time(9, 0)
        end_t = dt_time(20, 0)

    duration = int(doctor.slot_duration or 30)
    if duration < 10 or duration > 180:
        duration = 30

    # واکشی نوبت‌های فعال پزشک در آن تاریخ
    active_appts_qs = Appointment.objects.filter(
        doctor=doctor, date=target_date
    ).exclude(status='canceled')

    if exclude_appointment_id:
        active_appts_qs = active_appts_qs.exclude(id=exclude_appointment_id)

    booked_intervals = []
    for appt in active_appts_qs:
        a_start = appt.time
        a_end = appt.end_time or add_minutes_to_time(a_start, duration)
        booked_intervals.append((a_start, a_end))

    blocked_intervals = []
    for toff in time_offs:
        if not toff.is_full_day and toff.start_time and toff.end_time:
            blocked_intervals.append((toff.start_time, toff.end_time, toff.reason))

    slots = []
    curr_dt = datetime.combine(target_date, start_t)
    limit_dt = datetime.combine(target_date, end_t)

    while curr_dt + timedelta(minutes=duration) <= limit_dt:
        s_time = curr_dt.time()
        e_time = (curr_dt + timedelta(minutes=duration)).time()
        time_label = s_time.strftime("%H:%M")

        slot_state = "available"
        reason_label = "آزاد"

        # ۱. بررسی زمان‌های گذشته در روز جاری
        if target_date == today_tehran and s_time <= now_tehran.time():
            slot_state = "past"
            reason_label = "گذشته"

        # ۲. بررسی بازه‌های مسدود ساعتی پزشک
        if slot_state == "available":
            for b_start, b_end, b_reason in blocked_intervals:
                if s_time < b_end and e_time > b_start:
                    slot_state = "blocked"
                    reason_label = b_reason or "مسدود"
                    break

        # ۳. بررسی تداخل با نوبت‌های رزرو شده
        if slot_state == "available":
            for a_start, a_end in booked_intervals:
                if s_time < a_end and e_time > a_start:
                    slot_state = "booked"
                    reason_label = "رزرو شده"
                    break

        slots.append({
            'time': time_label,
            'end_time': e_time.strftime("%H:%M"),
            'state': slot_state,
            'is_free': (slot_state == "available"),
            'reason': reason_label,
        })
        curr_dt += timedelta(minutes=duration)

    has_any_free = any(s['is_free'] for s in slots)
    return slots, has_any_free, "OK"


def get_upcoming_days_for_doctor(doctor, days_ahead=14):
    """تولید لیست ۱۴ روز آینده به همراه وضعیت باز/بسته بودن هر روز برای نمایش در تقویم بیمار"""
    today_tehran = get_tehran_now().date()
    days_list = []
    for i in range(days_ahead):
        d = today_tehran + timedelta(days=i)
        slots, has_free, msg = calculate_doctor_slots(doctor, d)
        free_count = sum(1 for s in slots if s['is_free'])
        days_list.append({
            'gregorian': d.strftime("%Y-%m-%d"),
            'jalali': to_jalali_str(d),
            'jalali_short': to_jalali_str(d)[5:],
            'weekday': get_jalali_weekday_name(d),
            'is_today': (i == 0),
            'has_free': has_free,
            'free_count': free_count,
            'status_note': f"{free_count} نوبت خالی" if has_free else msg,
        })
    return days_list


def get_clinic_settings():
    ensure_mahlin_schema()
    settings_obj = ClinicSettings.objects.first()
    if not settings_obj:
        settings_obj = ClinicSettings.objects.create()
    return settings_obj


def record_audit(user, action_type, details):
    ensure_mahlin_schema()
    actor_name = user.username if user and user.is_authenticated else "سیستم"
    AuditLog.objects.create(actor=actor_name, action_type=action_type, details=details)


def get_user_role(user):
    ensure_mahlin_schema()
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return 'owner'
    profile = StaffProfile.objects.filter(user=user, is_active=True).first()
    if profile:
        return profile.role
    if Doctor.objects.filter(phone=user.username, is_active=True).exists():
        return 'doctor'
    return 'patient'


def staff_role_required(allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            ensure_mahlin_schema()
            if not request.user.is_authenticated:
                messages.error(request, "لطفاً ابتدا وارد حساب مدیریتی خود شوید.")
                return redirect('manager_login')
            role = get_user_role(request.user)
            if role not in allowed_roles:
                record_audit(request.user, "تلاش دسترسی غیرمجاز", f"تلاش برای باز کردن مسیر {request.path} با نقش {role}")
                messages.error(request, "شما مجوز دسترسی به این بخش را ندارید.")
                return redirect('home')
            request.staff_role = role
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


# ==============================================================================
# ۳. ویوهای سایت عمومی و احراز هویت
# ==============================================================================
def home_page(request):
    ensure_mahlin_schema()
    doctors = Doctor.objects.filter(is_active=True).order_by('id')
    clinic = get_clinic_settings()
    return render(request, 'home.html', {
        'doctors': doctors,
        'clinic': clinic,
    })


def login_view(request):
    ensure_mahlin_schema()
    next_url = request.GET.get('next', '')
    if next_url:
        request.session['next_url'] = next_url

    if request.method == 'POST':
        phone = request.POST.get('phone', '').strip()
        if not phone.isdigit() or len(phone) < 10:
            return render(request, 'login.html', {'error': 'لطفاً شماره موبایل معتبر وارد کنید.'})
        request.session['auth_phone'] = phone
        return redirect('verify_otp')
    return render(request, 'login.html')


def verify_otp(request):
    ensure_mahlin_schema()
    phone = request.session.get('auth_phone')
    if not phone:
        return redirect('login')

    if request.method == 'POST':
        code = request.POST.get('code', '').strip()
        if code == '12345':
            user, _ = User.objects.get_or_create(username=phone)
            login(request, user)
            next_url = request.session.pop('next_url', None)
            if next_url and next_url.startswith('/'):
                return redirect(next_url)
            return redirect('dashboard_router')
        else:
            return render(request, 'verify.html', {
                'phone': phone,
                'error': '❌ برای ورود در محیط آزمایشی، لطفاً کد تایید 12345 را وارد کنید.'
            })

    return render(request, 'verify.html', {'phone': phone})


# ==============================================================================
# ۴. وب‌سرویس استعلام زمان‌های آزاد و فرایند رزرو آنلاین اتمیک
# ==============================================================================
@login_required(login_url='/login/')
def api_available_slots(request):
    """API بازگشت روزهای قابل رزرو و ساعت‌های آزاد پزشک به صورت JSON"""
    ensure_mahlin_schema()
    doctor_id = request.GET.get('doctor_id')
    date_str = request.GET.get('date', '').strip()
    exclude_id = request.GET.get('exclude_id')

    if not doctor_id:
        return JsonResponse({'ok': False, 'error': 'پزشک انتخاب نشده است.'}, status=400)

    doctor = Doctor.objects.filter(id=doctor_id, is_active=True).first()
    if not doctor:
        return JsonResponse({'ok': False, 'error': 'پزشک موردنظر یافت نشد یا غیرفعال است.'}, status=404)

    upcoming_days = get_upcoming_days_for_doctor(doctor, days_ahead=14)

    # انتخاب تاریخ هدف
    target_date = None
    if date_str:
        try:
            target_date = parse_flexible_date(date_str)
        except Exception:
            return JsonResponse({'ok': False, 'error': 'فرمت تاریخ نامعتبر است.'}, status=400)
    else:
        for d_item in upcoming_days:
            if d_item['has_free']:
                target_date = parse_flexible_date(d_item['gregorian'])
                break
        if not target_date:
            target_date = get_tehran_now().date()

    slots, has_free, message = calculate_doctor_slots(doctor, target_date, exclude_appointment_id=exclude_id)

    return JsonResponse({
        'ok': True,
        'doctor': {
            'id': doctor.id,
            'name': doctor.name,
            'specialty': doctor.specialty,
            'working_days': doctor.working_days,
            'working_hours': f"{doctor.work_start} تا {doctor.work_end}",
            'slot_duration': doctor.slot_duration,
        },
        'selected_date_gregorian': target_date.strftime("%Y-%m-%d"),
        'selected_date_jalali': to_jalali_str(target_date),
        'selected_weekday': get_jalali_weekday_name(target_date),
        'has_free': has_free,
        'message': message,
        'days': upcoming_days,
        'slots': slots,
    })


@login_required(login_url='/login/?next=/book/')
def book_appointment(request):
    """فرایند ۶ مرحله‌ای رزرو آنلاین نوبت توسط بیمار با قفل اتمیک دیتابیس"""
    ensure_mahlin_schema()
    doctors = Doctor.objects.filter(is_active=True).order_by('id')
    existing_patient = Patient.objects.filter(phone=request.user.username).first()
    clinic = get_clinic_settings()

    selected_doctor_id = request.GET.get('doctor') or (doctors.first().id if doctors.exists() else None)

    if request.method == 'POST':
        doctor_id = request.POST.get('doctor')
        date_str = request.POST.get('date', '').strip()
        time_str = request.POST.get('time', '').strip()
        patient_name = request.POST.get('name', '').strip()
        national_id = request.POST.get('national_id', '').strip()

        # اعتبارسنجی ورودی‌ها در بک‌اند
        if not (doctor_id and date_str and time_str and patient_name and national_id):
            messages.error(request, "لطفاً تمام فیلدهای ستاره‌دار (پزشک، تاریخ، ساعت، نام و کد ملی) را تکمیل کنید.")
            return render(request, 'book.html', {
                'doctors': doctors, 'patient': existing_patient, 'clinic': clinic,
                'selected_doctor_id': int(doctor_id) if str(doctor_id).isdigit() else selected_doctor_id
            })

        if not (national_id.isdigit() and len(national_id) == 10):
            messages.error(request, "کد ملی باید دقیقاً ۱۰ رقم عددی باشد.")
            return render(request, 'book.html', {
                'doctors': doctors, 'patient': existing_patient, 'clinic': clinic,
                'selected_doctor_id': int(doctor_id) if str(doctor_id).isdigit() else selected_doctor_id
            })

        try:
            target_date = parse_flexible_date(date_str)
            target_time = parse_time_str(time_str)
        except Exception:
            messages.error(request, "فرمت تاریخ یا ساعت انتخاب‌شده معتبر نیست.")
            return redirect('book_appointment')

        # تراکنش اتمیک برای جلوگیری ۱۰۰٪ از رزرو هم‌زمان دو کاربر روی یک ساعت
        try:
            with transaction.atomic():
                doctor = Doctor.objects.select_for_update().get(id=doctor_id, is_active=True)
                slots, _, day_msg = calculate_doctor_slots(doctor, target_date)

                matched_slot = next((s for s in slots if s['time'] == target_time.strftime("%H:%M")), None)
                if not matched_slot or not matched_slot['is_free']:
                    reason = matched_slot['reason'] if matched_slot else day_msg
                    messages.error(request, f"❌ متأسفانه ساعت {time_str} در تاریخ {to_jalali_str(target_date)} قابل رزرو نیست ({reason}). لطفاً ساعت دیگری را انتخاب کنید.")
                    return render(request, 'book.html', {
                        'doctors': doctors, 'patient': existing_patient, 'clinic': clinic,
                        'selected_doctor_id': doctor.id
                    })

                end_time_obj = add_minutes_to_time(target_time, doctor.slot_duration or 30)

                # بررسی مضاعف عدم تداخل در سطح جدول نوبت‌ها
                conflict = Appointment.objects.filter(
                    doctor=doctor,
                    date=target_date
                ).exclude(status='canceled').filter(
                    time__lt=end_time_obj,
                    end_time__gt=target_time
                ).exists() or Appointment.objects.filter(
                    doctor=doctor, date=target_date, time=target_time
                ).exclude(status='canceled').exists()

                if conflict:
                    messages.error(request, "❌ این بازه زمانی لحظاتی پیش توسط مراجعه‌کننده دیگری رزرو شد. لطفاً ساعت دیگری را انتخاب کنید.")
                    return redirect(f"/book/?doctor={doctor.id}")

                # بررسی یا ایجاد پرونده بیمار بدون تداخل کد ملی
                patient_by_nid = Patient.objects.filter(national_id=national_id).first()
                if patient_by_nid and patient_by_nid.phone != request.user.username:
                    messages.error(request, "این کد ملی قبلاً با شماره همراه دیگری در سامانه ثبت شده است.")
                    return render(request, 'book.html', {
                        'doctors': doctors, 'patient': existing_patient, 'clinic': clinic,
                        'selected_doctor_id': doctor.id
                    })

                if patient_by_nid:
                    patient_obj = patient_by_nid
                    patient_obj.name = patient_name
                    patient_obj.save()
                else:
                    patient_obj = Patient.objects.create(
                        name=patient_name,
                        national_id=national_id,
                        phone=request.user.username
                    )

                tracking_code = generate_tracking_code()
                while Appointment.objects.filter(tracking_code=tracking_code).exists():
                    tracking_code = generate_tracking_code()

                initial_history = append_history("", request.user.username, f"ثبت اولیه نوبت آنلاین برای {to_jalali_str(target_date)} ساعت {target_time.strftime('%H:%M')}")

                appt = Appointment.objects.create(
                    patient=patient_obj,
                    doctor=doctor,
                    date=target_date,
                    time=target_time,
                    end_time=end_time_obj,
                    tracking_code=tracking_code,
                    status='pending',
                    is_confirmed=False,
                    history_log=initial_history
                )

                record_audit(request.user, "رزرو آنلاین نوبت", f"ثبت نوبت {tracking_code} برای {patient_name} نزد {doctor.name}")
        except Doctor.DoesNotExist:
            messages.error(request, "پزشک انتخاب‌شده یافت نشد.")
            return redirect('book_appointment')

        # تلاش برای ارسال پیامک (کاملاً مستقل از تراکنش ثبت نوبت)
        sms_text = (
            f"{patient_obj.name} عزیز، درخواست نوبت شما در {clinic.clinic_name} "
            f"نزد {doctor.name} برای تاریخ {to_jalali_str(target_date)} ساعت {target_time.strftime('%H:%M')} ثبت شد. "
            f"کد پیگیری: {appt.tracking_code}"
        )
        sms_state, sms_msg = send_real_sms(request.user.username, sms_text)
        appt.sms_status = sms_state
        appt.save(update_fields=['sms_status'])

        messages.success(
            request,
            f"✅ نوبت شما با موفقیت ثبت شد! کد پیگیری: {appt.tracking_code} | وضعیت نوبت: در انتظار تأیید | وضعیت پیامک: {sms_msg}"
        )
        return redirect('patient_dashboard')

    return render(request, 'book.html', {
        'doctors': doctors,
        'patient': existing_patient,
        'clinic': clinic,
        'selected_doctor_id': int(selected_doctor_id) if str(selected_doctor_id).isdigit() else None,
    })


# ==============================================================================
# ۵. پنل بیمار، لغو نوبت و تغییر زمان نوبت با بررسی مالکیت
# ==============================================================================
@login_required(login_url='/login/')
def dashboard_router(request):
    ensure_mahlin_schema()
    role = get_user_role(request.user)
    if role in ['owner', 'receptionist']:
        return redirect('manager_dashboard')
    if role == 'doctor':
        return redirect('doctor_dashboard')
    return redirect('patient_dashboard')


@login_required(login_url='/login/')
def patient_dashboard(request):
    ensure_mahlin_schema()
    now_tehran = get_tehran_now()
    today_tehran = now_tehran.date()

    # بیمار فقط نوبت‌های متعلق به شماره همراه حساب خودش را می‌بیند
    appointments = Appointment.objects.filter(
        patient__phone=request.user.username
    ).select_related('doctor', 'patient').order_by('-date', '-time')

    for appt in appointments:
        appt.jalali_date = to_jalali_str(appt.date)
        appt.weekday_name = get_jalali_weekday_name(appt.date)
        is_future = (appt.date > today_tehran) or (appt.date == today_tehran and appt.time > now_tehran.time())
        appt.can_modify = is_future and (appt.status in ['pending', 'confirmed'])

    clinic = get_clinic_settings()
    return render(request, 'dashboard.html', {
        'appointments': appointments,
        'clinic': clinic,
        'today_jalali': to_jalali_str(today_tehran),
    })


@login_required(login_url='/login/')
def cancel_appointment(request, appointment_id):
    """لغو نوبت توسط بیمار با بررسی دقیق مالکیت نوبت در بک‌اند"""
    ensure_mahlin_schema()
    appt = Appointment.objects.filter(id=appointment_id).select_related('patient', 'doctor').first()
    if not appt or appt.patient.phone != request.user.username:
        record_audit(request.user, "تلاش غیرمجاز لغو نوبت", f"تلاش برای لغو نوبت #{appointment_id} متعلق به کاربر دیگر")
        messages.error(request, "شما مجوز دسترسی به این نوبت را ندارید.")
        return redirect('patient_dashboard')

    now_tehran = get_tehran_now()
    if appt.status in ['completed', 'canceled', 'no_show']:
        messages.warning(request, "امکان لغو نوبت‌های تکمیل‌شده یا قبلاً لغوشده وجود ندارد.")
        return redirect('patient_dashboard')

    if appt.date < now_tehran.date() or (appt.date == now_tehran.date() and appt.time <= now_tehran.time()):
        messages.error(request, "امکان لغو نوبت‌های گذشته وجود ندارد.")
        return redirect('patient_dashboard')

    reason = request.POST.get('cancel_reason', 'لغو توسط بیمار در پنل کاربری').strip()
    appt.status = 'canceled'
    appt.is_confirmed = False
    appt.cancel_reason = reason
    appt.history_log = append_history(appt.history_log, request.user.username, f"لغو نوبت توسط بیمار ({reason})")
    appt.save()

    record_audit(request.user, "لغو نوبت توسط بیمار", f"نوبت {appt.tracking_code} لغو شد.")
    messages.info(request, f"نوبت با کد پیگیری {appt.tracking_code} لغو گردید و ساعت آن آزاد شد.")
    return redirect('patient_dashboard')


@login_required(login_url='/login/')
def reschedule_appointment(request, appointment_id):
    """تغییر زمان نوبت توسط بیمار با بررسی مالکیت و بررسی مجدد ظرفیت ساعت جدید"""
    ensure_mahlin_schema()
    if request.method != 'POST':
        return redirect('patient_dashboard')

    appt = Appointment.objects.filter(id=appointment_id).select_related('patient', 'doctor').first()
    if not appt or appt.patient.phone != request.user.username:
        record_audit(request.user, "تلاش غیرمجاز تغییر نوبت", f"تلاش برای تغییر زمان نوبت #{appointment_id} متعلق به کاربر دیگر")
        messages.error(request, "شما مجوز تغییر این نوبت را ندارید.")
        return redirect('patient_dashboard')

    if appt.status not in ['pending', 'confirmed']:
        messages.error(request, "فقط نوبت‌های فعال قابل جابه‌جایی هستند.")
        return redirect('patient_dashboard')

    new_date_str = request.POST.get('new_date', '').strip()
    new_time_str = request.POST.get('new_time', '').strip()

    try:
        target_date = parse_flexible_date(new_date_str)
        target_time = parse_time_str(new_time_str)
    except Exception:
        messages.error(request, "فرمت تاریخ یا ساعت جدید معتبر نیست.")
        return redirect('patient_dashboard')

    with transaction.atomic():
        doctor = Doctor.objects.select_for_update().get(id=appt.doctor.id)
        slots, _, day_msg = calculate_doctor_slots(doctor, target_date, exclude_appointment_id=appt.id)
        matched_slot = next((s for s in slots if s['time'] == target_time.strftime("%H:%M")), None)

        if not matched_slot or not matched_slot['is_free']:
            reason = matched_slot['reason'] if matched_slot else day_msg
            messages.error(request, f"❌ تغییر زمان انجام نشد: ساعت {new_time_str} در تاریخ {to_jalali_str(target_date)} در دسترس نیست ({reason}).")
            return redirect('patient_dashboard')

        old_date_j = to_jalali_str(appt.date)
        old_time_s = appt.time.strftime("%H:%M")

        appt.date = target_date
        appt.time = target_time
        appt.end_time = add_minutes_to_time(target_time, doctor.slot_duration or 30)
        appt.status = 'pending'
        appt.is_confirmed = False
        appt.history_log = append_history(
            appt.history_log,
            request.user.username,
            f"تغییر زمان توسط بیمار از {old_date_j} ساعت {old_time_s} به {to_jalali_str(target_date)} ساعت {target_time.strftime('%H:%M')}"
        )
        appt.save()

    record_audit(request.user, "تغییر زمان نوبت توسط بیمار", f"نوبت {appt.tracking_code} به {to_jalali_str(target_date)} ساعت {new_time_str} منتقل شد.")
    messages.success(request, f"زمان نوبت {appt.tracking_code} با موفقیت به {to_jalali_str(target_date)} ساعت {new_time_str} تغییر یافت.")
    return redirect('patient_dashboard')


# ==============================================================================
# ۶. پنل اختصاصی پزشک (مشاهده فقط نوبت‌های خود پزشک + مدیریت مرخصی و وضعیت‌ها)
# ==============================================================================
@login_required(login_url='/login/')
def doctor_dashboard(request):
    ensure_mahlin_schema()
    doctor = Doctor.objects.filter(phone=request.user.username).first()
    if not doctor:
        messages.error(request, "حساب شما به عنوان پزشک ثبت نشده است.")
        return redirect('patient_dashboard')

    now_tehran = get_tehran_now()
    today = now_tehran.date()

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'update_appt_status':
            appt_id = request.POST.get('appointment_id')
            new_status = request.POST.get('status')
            doctor_note = request.POST.get('doctor_note', '').strip()

            # پزشک فقط به نوبت‌های خودش دسترسی دارد
            appt = Appointment.objects.filter(id=appt_id, doctor=doctor).first()
            if appt and new_status in ['confirmed', 'completed', 'no_show', 'canceled']:
                appt.status = new_status
                appt.is_confirmed = (new_status in ['confirmed', 'completed'])
                if doctor_note:
                    appt.doctor_note = doctor_note
                appt.history_log = append_history(appt.history_log, f"دکتر {doctor.name}", f"تغییر وضعیت به {appt.get_status_display()}")
                appt.save()
                record_audit(request.user, "بروزرسانی نوبت توسط پزشک", f"نوبت {appt.tracking_code} به وضعیت {new_status} تغییر یافت.")
                messages.success(request, f"وضعیت نوبت بیمار «{appt.patient.name}» بروزرسانی شد.")

        elif action == 'add_time_off':
            leave_date_str = request.POST.get('leave_date', '').strip()
            reason = request.POST.get('reason', 'مرخصی پزشک').strip()
            try:
                l_date = parse_flexible_date(leave_date_str)
                DoctorTimeOff.objects.create(doctor=doctor, date=l_date, is_full_day=True, reason=reason)
                record_audit(request.user, "ثبت مرخصی پزشک", f"ثبت مرخصی دکتر {doctor.name} برای تاریخ {to_jalali_str(l_date)}")
                messages.success(request, f"مرخصی تاریخ {to_jalali_str(l_date)} ثبت شد و نوبت‌دهی آن روز بسته شد.")
            except Exception:
                messages.error(request, "فرمت تاریخ مرخصی معتبر نیست.")

        elif action == 'delete_time_off':
            toff_id = request.POST.get('toff_id')
            DoctorTimeOff.objects.filter(id=toff_id, doctor=doctor).delete()
            messages.info(request, "محدودیت مرخصی حذف شد.")

        return redirect('doctor_dashboard')

    selected_date_str = request.GET.get('date', '').strip()
    try:
        selected_date = parse_flexible_date(selected_date_str) if selected_date_str else today
    except Exception:
        selected_date = today

    week_days = []
    for i in range(-1, 6):
        d = today + timedelta(days=i)
        j_d = jdatetime.date.fromgregorian(date=d)
        week_days.append({
            'gregorian': d.strftime('%Y-%m-%d'),
            'day_num': j_d.day,
            'weekday_name': get_jalali_weekday_name(d),
            'is_selected': (d == selected_date),
        })

    appointments = Appointment.objects.filter(
        doctor=doctor, date=selected_date
    ).select_related('patient').order_by('time')

    for appt in appointments:
        appt.jalali_date = to_jalali_str(appt.date)

    time_offs = DoctorTimeOff.objects.filter(doctor=doctor, date__gte=today).order_by('date')
    for t in time_offs:
        t.jalali_date = to_jalali_str(t.date)

    return render(request, 'doctor_dashboard.html', {
        'doctor': doctor,
        'appointments': appointments,
        'today': to_jalali_str(selected_date),
        'week_days': week_days,
        'time_offs': time_offs,
        'total_appointments': Appointment.objects.filter(doctor=doctor).count(),
        'confirmed_count': Appointment.objects.filter(doctor=doctor, status__in=['confirmed', 'completed']).count(),
    })


def logout_view(request):
    logout(request)
    return redirect('home')


def custom_404(request, exception):
    return render(request, '404.html', status=404)


# ==============================================================================
# ۷. ورود و پنل مدیریت اختصاصی مالک و منشی کلینیک
# ==============================================================================
def manager_login(request):
    ensure_mahlin_schema()
    if request.user.is_authenticated and get_user_role(request.user) in ['owner', 'receptionist']:
        return redirect('manager_dashboard')

    has_owner = StaffProfile.objects.filter(role='owner', is_active=True).exists()

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        full_name = request.POST.get('full_name', 'مدیر کلینیک ماهلین').strip()

        if not has_owner:
            if len(username) < 3 or len(password) < 4:
                messages.error(request, "نام کاربری حداقل ۳ کاراکتر و رمز عبور باید حداقل ۴ کاراکتر باشد.")
            else:
                user, _ = User.objects.get_or_create(username=username)
                user.set_password(password)
                user.is_staff = True
                user.is_superuser = True
                user.is_active = True
                user.save()

                StaffProfile.objects.update_or_create(
                    user=user,
                    defaults={
                        'full_name': full_name,
                        'phone': username,
                        'role': 'owner',
                        'is_active': True
                    }
                )
                record_audit(user, "ایجاد حساب مالک اولیه", f"حساب مالک کلینیک با نام کاربری {username} فعال شد.")
                login(request, user)
                messages.success(request, "حساب مدیر مالک با موفقیت ساخته شد. خوش آمدید!")
                return redirect('manager_dashboard')
        else:
            user = authenticate(request, username=username, password=password)
            if user is not None:
                role = get_user_role(user)
                if role in ['owner', 'receptionist']:
                    login(request, user)
                    record_audit(user, "ورود به پنل مدیریت", f"ورود موفق با نقش {role}")
                    return redirect('manager_dashboard')
                else:
                    messages.error(request, "این حساب کاربری دسترسی مدیریت یا پذیرش ندارد.")
            else:
                messages.error(request, "نام کاربری یا رمز عبور اشتباه است.")

    return render(request, 'manager_login.html', {'has_owner': has_owner})


@staff_role_required(['owner', 'receptionist'])
def manager_dashboard(request):
    ensure_mahlin_schema()
    role = request.staff_role
    today = get_tehran_now().date()
    active_tab = request.GET.get('tab', 'dashboard')

    if request.method == 'POST':
        form_type = request.POST.get('form_type', '')

        # ۱. افزودن یا ویرایش پزشک (همراه با تنظیمات ساعت کاری و مدت هر نوبت)
        if form_type == 'save_doctor':
            if role != 'owner':
                messages.error(request, "فقط مدیر مالک مجاز به تغییر اطلاعات پزشکان است.")
                return redirect('/manager-panel/?tab=doctors')

            doc_id = request.POST.get('doctor_id', '').strip()
            name = request.POST.get('name', '').strip()
            specialty = request.POST.get('specialty', '').strip()
            phone = request.POST.get('phone', '').strip()
            bio = request.POST.get('bio', '').strip()
            working_days = request.POST.get('working_days', 'شنبه تا چهارشنبه').strip()
            work_days_mask = request.POST.get('work_days_mask', '0,1,2,3,4').strip()
            work_start = request.POST.get('work_start', '09:00').strip()
            work_end = request.POST.get('work_end', '20:00').strip()
            slot_duration = int(request.POST.get('slot_duration', 30) or 30)
            working_hours = f"{work_start} تا {work_end}"
            is_active = request.POST.get('is_active') == 'on'
            profile_img = request.FILES.get('profile_image')

            if not name or not specialty or not phone:
                messages.error(request, "نام، تخصص و شماره تماس پزشک الزامی است.")
                return redirect('/manager-panel/?tab=doctors')

            dup_qs = Doctor.objects.filter(phone=phone)
            if doc_id:
                dup_qs = dup_qs.exclude(id=doc_id)
            if dup_qs.exists():
                messages.error(request, f"پزشکی با شماره تماس {phone} قبلاً در سیستم ثبت شده است.")
                return redirect('/manager-panel/?tab=doctors')

            if doc_id:
                doc = get_object_or_404(Doctor, id=doc_id)
                doc.name = name
                doc.specialty = specialty
                doc.phone = phone
                doc.bio = bio
                doc.working_days = working_days
                doc.working_hours = working_hours
                doc.work_days_mask = work_days_mask
                doc.work_start = work_start
                doc.work_end = work_end
                doc.slot_duration = slot_duration
                doc.is_active = is_active
                if profile_img:
                    doc.profile_image = profile_img
                doc.save()
                record_audit(request.user, "ویرایش پزشک", f"اطلاعات دکتر {name} بروزرسانی شد.")
                messages.success(request, f"اطلاعات «{name}» با موفقیت بروزرسانی شد.")
            else:
                doc = Doctor(
                    name=name, specialty=specialty, phone=phone,
                    bio=bio, working_days=working_days, working_hours=working_hours,
                    work_days_mask=work_days_mask, work_start=work_start,
                    work_end=work_end, slot_duration=slot_duration,
                    is_active=is_active
                )
                if profile_img:
                    doc.profile_image = profile_img
                doc.save()
                record_audit(request.user, "افزودن پزشک", f"پزشک جدید «{name}» با تخصص {specialty} اضافه شد.")
                messages.success(request, f"پزشک جدید «{name}» با موفقیت به کلینیک اضافه شد.")
            return redirect('/manager-panel/?tab=doctors')

        # ۲. فعال یا غیرفعال کردن پزشک
        elif form_type == 'toggle_doctor':
            if role != 'owner':
                messages.error(request, "عدم دسترسی مجاز.")
                return redirect('/manager-panel/?tab=doctors')
            doc = get_object_or_404(Doctor, id=request.POST.get('doctor_id'))
            doc.is_active = not doc.is_active
            doc.save()
            status_txt = "فعال" if doc.is_active else "غیرفعال"
            record_audit(request.user, "تغییر وضعیت پزشک", f"وضعیت دکتر {doc.name} به {status_txt} تغییر یافت.")
            messages.info(request, f"حساب «{doc.name}» {status_txt} شد.")
            return redirect('/manager-panel/?tab=doctors')

        # ۳. حذف کامل پزشک
        elif form_type == 'delete_doctor':
            if role != 'owner':
                messages.error(request, "فقط مدیر مالک مجاز به حذف پزشک است.")
                return redirect('/manager-panel/?tab=doctors')
            doc = get_object_or_404(Doctor, id=request.POST.get('doctor_id'))
            doc_name = doc.name
            doc.delete()
            record_audit(request.user, "حذف پزشک", f"پزشک «{doc_name}» به طور کامل از سامانه حذف شد.")
            messages.warning(request, f"پزشک «{doc_name}» با موفقیت از سامانه حذف گردید.")
            return redirect('/manager-panel/?tab=doctors')

        # ۴. ثبت مرخصی یا مسدودسازی زمان پزشک توسط مدیریت
        elif form_type == 'add_time_off':
            doc = get_object_or_404(Doctor, id=request.POST.get('doctor_id'))
            date_str = request.POST.get('date', '').strip()
            is_full_day = request.POST.get('is_full_day') == 'on'
            s_time_str = request.POST.get('start_time', '').strip()
            e_time_str = request.POST.get('end_time', '').strip()
            reason = request.POST.get('reason', 'مرخصی / عدم حضور').strip()
            try:
                g_date = parse_flexible_date(date_str)
                s_time = parse_time_str(s_time_str) if (not is_full_day and s_time_str) else None
                e_time = parse_time_str(e_time_str) if (not is_full_day and e_time_str) else None
                DoctorTimeOff.objects.create(
                    doctor=doc, date=g_date, is_full_day=is_full_day,
                    start_time=s_time, end_time=e_time, reason=reason
                )
                record_audit(request.user, "ثبت مرخصی پزشک", f"ثبت عدم حضور برای {doc.name} در {to_jalali_str(g_date)}")
                messages.success(request, f"عدم حضور دکتر {doc.name} برای تاریخ {to_jalali_str(g_date)} ثبت شد.")
            except Exception:
                messages.error(request, "فرمت تاریخ یا ساعت مرخصی صحیح نیست.")
            return redirect('/manager-panel/?tab=doctors')

        elif form_type == 'delete_time_off':
            DoctorTimeOff.objects.filter(id=request.POST.get('toff_id')).delete()
            messages.info(request, "محدودیت زمانی پزشک حذف شد.")
            return redirect('/manager-panel/?tab=doctors')

        # ۵. ثبت نوبت تلفنی/حضوری توسط مدیر یا منشی (با بررسی اتمیک ظرفیت)
        elif form_type == 'create_appointment':
            patient_name = request.POST.get('patient_name', '').strip()
            national_id = request.POST.get('national_id', '').strip()
            phone = request.POST.get('phone', '').strip()
            doctor_id = request.POST.get('doctor_id')
            date_str = request.POST.get('date', '').strip()
            time_str = request.POST.get('time', '').strip()

            try:
                greg_date = parse_flexible_date(date_str)
                time_obj = parse_time_str(time_str)
            except Exception:
                messages.error(request, "فرمت تاریخ یا ساعت وارد شده معتبر نیست.")
                return redirect('/manager-panel/?tab=appointments')

            with transaction.atomic():
                doctor_obj = get_object_or_404(Doctor.objects.select_for_update(), id=doctor_id)
                slots, _, day_msg = calculate_doctor_slots(doctor_obj, greg_date)
                matched = next((s for s in slots if s['time'] == time_obj.strftime("%H:%M")), None)

                if not matched or not matched['is_free']:
                    reason = matched['reason'] if matched else day_msg
                    messages.error(request, f"خطای ظرفیت: ساعت {time_str} برای دکتر {doctor_obj.name} قابل رزرو نیست ({reason}).")
                    return redirect('/manager-panel/?tab=appointments')

                patient_obj, created = Patient.objects.get_or_create(
                    national_id=national_id,
                    defaults={'name': patient_name, 'phone': phone}
                )
                if not created:
                    patient_obj.name = patient_name
                    patient_obj.phone = phone
                    patient_obj.save()

                tracking_code = generate_tracking_code()
                end_t = add_minutes_to_time(time_obj, doctor_obj.slot_duration or 30)
                hist = append_history("", request.user.username, "ثبت نوبت حضوری/تلفنی توسط پذیرش یا مدیریت")

                appt = Appointment.objects.create(
                    patient=patient_obj,
                    doctor=doctor_obj,
                    date=greg_date,
                    time=time_obj,
                    end_time=end_t,
                    tracking_code=tracking_code,
                    is_confirmed=True,
                    status='confirmed',
                    history_log=hist
                )

            sms_state, _ = send_real_sms(phone, f"نوبت شما در کلینیک ماهلین نزد {doctor_obj.name} برای {to_jalali_str(greg_date)} ساعت {time_str} تأیید شد. کد: {tracking_code}")
            appt.sms_status = sms_state
            appt.save(update_fields=['sms_status'])

            record_audit(request.user, "ثبت نوبت مدیریتی", f"ثبت نوبت {tracking_code} برای {patient_name} نزد {doctor_obj.name}")
            messages.success(request, f"نوبت جدید با کد پیگیری {tracking_code} ثبت و تأیید شد.")
            return redirect('/manager-panel/?tab=appointments')

        # ۶. تغییر وضعیت یا تغییر زمان نوبت توسط مدیر/منشی (با بررسی مجدد ظرفیت ساعت جدید)
        elif form_type == 'update_appointment':
            with transaction.atomic():
                appt = get_object_or_404(Appointment.objects.select_for_update(), id=request.POST.get('appointment_id'))
                new_status = request.POST.get('status', appt.status)
                new_date_str = request.POST.get('new_date', '').strip()
                new_time_str = request.POST.get('new_time', '').strip()
                cancel_reason = request.POST.get('cancel_reason', '').strip()

                target_date = appt.date
                target_time = appt.time

                if new_date_str:
                    try:
                        target_date = parse_flexible_date(new_date_str)
                    except Exception:
                        messages.error(request, "فرمت تاریخ جدید صحیح نیست.")
                        return redirect('/manager-panel/?tab=appointments')

                if new_time_str:
                    try:
                        target_time = parse_time_str(new_time_str)
                    except Exception:
                        messages.error(request, "فرمت ساعت جدید صحیح نیست.")
                        return redirect('/manager-panel/?tab=appointments')

                date_or_time_changed = (target_date != appt.date) or (target_time != appt.time)

                # اگر زمان تغییر کرده یا نوبت از حالت لغو خارج می‌شود، ظرفیت را بررسی کن
                if new_status != 'canceled' and (date_or_time_changed or appt.status == 'canceled'):
                    slots, _, day_msg = calculate_doctor_slots(appt.doctor, target_date, exclude_appointment_id=appt.id)
                    matched = next((s for s in slots if s['time'] == target_time.strftime("%H:%M")), None)
                    if not matched or not matched['is_free']:
                        # در صورتی که فقط وضعیت نوبتِ همان ساعت قبلی تغییر می‌کند، خطای زمان گذشته نگیر
                        conflict_exists = Appointment.objects.filter(
                            doctor=appt.doctor, date=target_date, time=target_time
                        ).exclude(id=appt.id).exclude(status='canceled').exists()
                        if conflict_exists or date_or_time_changed:
                            reason = matched['reason'] if matched else day_msg
                            messages.error(request, f"تغییر زمان امکان‌پذیر نیست؛ بازه انتخابی در دسترس نیست ({reason}).")
                            return redirect('/manager-panel/?tab=appointments')

                appt.date = target_date
                appt.time = target_time
                appt.end_time = add_minutes_to_time(target_time, appt.doctor.slot_duration or 30)
                appt.status = new_status
                appt.is_confirmed = (new_status in ['confirmed', 'completed'])
                if cancel_reason:
                    appt.cancel_reason = cancel_reason

                appt.history_log = append_history(
                    appt.history_log,
                    request.user.username,
                    f"بروزرسانی به وضعیت «{appt.get_status_display()}» برای {to_jalali_str(target_date)} ساعت {target_time.strftime('%H:%M')}"
                )
                appt.save()

            record_audit(request.user, "بروزرسانی نوبت", f"نوبت {appt.tracking_code} ({appt.patient.name}) به وضعیت «{new_status}» تغییر یافت.")
            messages.success(request, f"نوبت {appt.tracking_code} («{appt.patient.name}») بروزرسانی شد.")
            return redirect('/manager-panel/?tab=appointments')

        # ۷. ویرایش اطلاعات بیمار
        elif form_type == 'update_patient':
            pat = get_object_or_404(Patient, id=request.POST.get('patient_id'))
            new_name = request.POST.get('name', '').strip()
            new_nid = request.POST.get('national_id', '').strip()
            new_phone = request.POST.get('phone', '').strip()
            med_notes = request.POST.get('medical_notes', '').strip()

            if Patient.objects.filter(national_id=new_nid).exclude(id=pat.id).exists():
                messages.error(request, "بیمار دیگری با این کد ملی در سیستم وجود دارد.")
                return redirect('/manager-panel/?tab=patients')

            pat.name = new_name
            pat.national_id = new_nid
            pat.phone = new_phone
            if role == 'owner':
                pat.medical_notes = med_notes
            pat.save()
            record_audit(request.user, "ویرایش پرونده بیمار", f"اطلاعات بیمار {new_name} (کدملی {new_nid}) ویرایش شد.")
            messages.success(request, "اطلاعات بیمار با موفقیت بروزرسانی شد.")
            return redirect('/manager-panel/?tab=patients')

        # ۸. تعریف یا ویرایش کارکنان
        elif form_type == 'save_staff':
            if role != 'owner':
                messages.error(request, "فقط مدیر مالک مجاز به مدیریت کارکنان و دسترسی‌ها است.")
                return redirect('/manager-panel/?tab=staff')

            full_name = request.POST.get('full_name', '').strip()
            phone = request.POST.get('phone', '').strip()
            staff_role = request.POST.get('role', 'receptionist')
            password = request.POST.get('password', '').strip()
            is_active = request.POST.get('is_active') == 'on'

            if not full_name or not phone:
                messages.error(request, "نام و شماره/کد کاربری الزامی است.")
                return redirect('/manager-panel/?tab=staff')

            user_obj, _ = User.objects.get_or_create(username=phone)
            if password:
                user_obj.set_password(password)
            user_obj.is_staff = (staff_role == 'owner')
            user_obj.is_active = is_active
            user_obj.save()

            StaffProfile.objects.update_or_create(
                user=user_obj,
                defaults={
                    'full_name': full_name,
                    'phone': phone,
                    'role': staff_role,
                    'is_active': is_active
                }
            )
            record_audit(request.user, "مدیریت کارکنان", f"حساب {full_name} ({phone}) با نقش {staff_role} ذخیره شد.")
            messages.success(request, f"حساب کاربری «{full_name}» با موفقیت ذخیره شد.")
            return redirect('/manager-panel/?tab=staff')

        # ۹. ذخیره تنظیمات کلینیک
        elif form_type == 'save_settings':
            if role != 'owner':
                messages.error(request, "فقط مدیر مالک مجاز به تغییر تنظیمات کلینیک است.")
                return redirect('/manager-panel/?tab=settings')

            c_settings = get_clinic_settings()
            c_settings.clinic_name = request.POST.get('clinic_name', c_settings.clinic_name).strip()
            c_settings.slogan = request.POST.get('slogan', c_settings.slogan).strip()
            c_settings.phone = request.POST.get('phone', c_settings.phone).strip()
            c_settings.address = request.POST.get('address', c_settings.address).strip()
            c_settings.map_link = request.POST.get('map_link', '').strip()
            c_settings.working_hours = request.POST.get('working_hours', c_settings.working_hours).strip()
            c_settings.holidays_notice = request.POST.get('holidays_notice', '').strip()
            c_settings.booking_rules = request.POST.get('booking_rules', c_settings.booking_rules).strip()
            c_settings.save()

            record_audit(request.user, "تغییر تنظیمات کلینیک", "اطلاعات پایه و ساعات کاری کلینیک بروزرسانی شد.")
            messages.success(request, "تنظیمات کلینیک ذخیره شد و در سایت اعمال گردید.")
            return redirect('/manager-panel/?tab=settings')

    today_appointments_qs = Appointment.objects.filter(date=today).select_related('patient', 'doctor').order_by('time')
    for a in today_appointments_qs:
        a.jalali_date = to_jalali_str(a.date)

    total_today = today_appointments_qs.count()
    pending_count = Appointment.objects.filter(status='pending').count()
    confirmed_count = Appointment.objects.filter(status__in=['confirmed', 'completed']).count()
    canceled_count = Appointment.objects.filter(status='canceled').count()
    total_patients = Patient.objects.count()
    active_doctors_count = Doctor.objects.filter(is_active=True).count()

    week_ago = today - timedelta(days=6)
    month_ago = today - timedelta(days=29)
    weekly_total = Appointment.objects.filter(date__gte=week_ago, date__lte=today).count()
    monthly_total = Appointment.objects.filter(date__gte=month_ago, date__lte=today).count()

    chart_days = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        cnt = Appointment.objects.filter(date=d).exclude(status='canceled').count()
        chart_days.append({
            'label': to_jalali_str(d)[5:],
            'count': cnt,
        })
    max_chart = max([item['count'] for item in chart_days] + [1])
    for item in chart_days:
        item['height_pct'] = int((item['count'] / max_chart) * 100) if item['count'] > 0 else 8

    doc_q = request.GET.get('doc_q', '').strip()
    doc_status = request.GET.get('doc_status', '')
    doctors_qs = Doctor.objects.all().order_by('-is_active', 'id')
    if doc_q:
        doctors_qs = doctors_qs.filter(Q(name__icontains=doc_q) | Q(specialty__icontains=doc_q) | Q(phone__icontains=doc_q))
    if doc_status == 'active':
        doctors_qs = doctors_qs.filter(is_active=True)
    elif doc_status == 'inactive':
        doctors_qs = doctors_qs.filter(is_active=False)

    appt_q = request.GET.get('appt_q', '').strip()
    appt_doctor = request.GET.get('appt_doctor', '')
    appt_status = request.GET.get('appt_status', '')
    appt_date = request.GET.get('appt_date', '').strip()

    appointments_qs = Appointment.objects.select_related('patient', 'doctor').order_by('-date', 'time')
    if appt_q:
        appointments_qs = appointments_qs.filter(
            Q(patient__name__icontains=appt_q) |
            Q(patient__national_id__icontains=appt_q) |
            Q(tracking_code__icontains=appt_q) |
            Q(id__icontains=appt_q)
        )
    if appt_doctor:
        appointments_qs = appointments_qs.filter(doctor_id=appt_doctor)
    if appt_status:
        appointments_qs = appointments_qs.filter(status=appt_status)
    if appt_date:
        try:
            g_filter_date = parse_flexible_date(appt_date)
            appointments_qs = appointments_qs.filter(date=g_filter_date)
        except Exception:
            pass

    appt_paginator = Paginator(appointments_qs, 15)
    appointments_page = appt_paginator.get_page(request.GET.get('appt_page', 1))
    for a in appointments_page:
        a.jalali_date = to_jalali_str(a.date)

    pat_q = request.GET.get('pat_q', '').strip()
    patients_qs = Patient.objects.annotate(appt_count=Count('appointment')).order_by('-id')
    if pat_q:
        patients_qs = patients_qs.filter(
            Q(name__icontains=pat_q) | Q(national_id__icontains=pat_q) | Q(phone__icontains=pat_q)
        )
    pat_paginator = Paginator(patients_qs, 15)
    patients_page = pat_paginator.get_page(request.GET.get('pat_page', 1))

    staff_list = StaffProfile.objects.select_related('user').order_by('-created_at')
    audit_logs = AuditLog.objects.all()[:30] if role == 'owner' else []
    doctor_leaves = DoctorTimeOff.objects.select_related('doctor').filter(date__gte=today).order_by('date')
    for lv in doctor_leaves:
        lv.jalali_date = to_jalali_str(lv.date)

    context = {
        'active_tab': active_tab,
        'staff_role': role,
        'today_jalali': to_jalali_str(today),
        'clinic': get_clinic_settings(),
        'sms_configured': bool(os.environ.get('SMS_API_KEY', '').strip()),
        'total_today': total_today,
        'pending_count': pending_count,
        'confirmed_count': confirmed_count,
        'canceled_count': canceled_count,
        'total_patients': total_patients,
        'active_doctors_count': active_doctors_count,
        'weekly_total': weekly_total,
        'monthly_total': monthly_total,
        'today_appointments': today_appointments_qs,
        'chart_days': chart_days,
        'doctors_list': doctors_qs,
        'doctor_leaves': doctor_leaves,
        'all_active_doctors': Doctor.objects.filter(is_active=True),
        'appointments_page': appointments_page,
        'patients_page': patients_page,
        'staff_list': staff_list,
        'audit_logs': audit_logs,
        'doc_q': doc_q,
        'doc_status': doc_status,
        'appt_q': appt_q,
        'appt_doctor': appt_doctor,
        'appt_status': appt_status,
        'appt_date': appt_date,
        'pat_q': pat_q,
    }
    return render(request, 'manager_panel.html', context)
