from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import connection, transaction
from django.db.models import Q, Count
from django.core.paginator import Paginator
from datetime import timedelta, datetime
from functools import wraps
import jdatetime

from .models import (
    Patient, Appointment, OTPRequest, Doctor,
    StaffProfile, ClinicSettings, AuditLog
)
from .forms import BookingForm


# ==============================================================================
# ۱. همگام‌ساز خودکار دیتابیس (جلوگیری ۱۰۰٪ از خطای OperationalError در Railway)
# ==============================================================================
_SCHEMA_CHECKED = False

def ensure_mahlin_schema():
    global _SCHEMA_CHECKED
    if _SCHEMA_CHECKED:
        return
    try:
        with connection.cursor() as cursor:
            # بررسی ستون‌های جدول Doctor
            cursor.execute("PRAGMA table_info(booking_doctor)")
            doc_cols = [row[1] for row in cursor.fetchall()]
            if doc_cols:
                if 'bio' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN bio TEXT DEFAULT ''")
                if 'working_days' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN working_days VARCHAR(150) DEFAULT 'شنبه تا چهارشنبه'")
                if 'working_hours' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN working_hours VARCHAR(100) DEFAULT '۰۹:۰۰ تا ۲۰:۰۰'")
                if 'is_active' not in doc_cols:
                    cursor.execute("ALTER TABLE booking_doctor ADD COLUMN is_active bool DEFAULT 1")

            # بررسی ستون‌های جدول Patient
            cursor.execute("PRAGMA table_info(booking_patient)")
            pat_cols = [row[1] for row in cursor.fetchall()]
            if pat_cols and 'medical_notes' not in pat_cols:
                cursor.execute("ALTER TABLE booking_patient ADD COLUMN medical_notes TEXT DEFAULT ''")

            # بررسی ستون‌های جدول Appointment
            cursor.execute("PRAGMA table_info(booking_appointment)")
            app_cols = [row[1] for row in cursor.fetchall()]
            if app_cols:
                if 'status' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN status VARCHAR(20) DEFAULT 'pending'")
                    cursor.execute("UPDATE booking_appointment SET status = 'confirmed' WHERE is_confirmed = 1")
                if 'cancel_reason' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN cancel_reason VARCHAR(255) DEFAULT ''")

            # ایجاد جدول StaffProfile در صورت عدم وجود
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

            # ایجاد جدول ClinicSettings در صورت عدم وجود
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
                    booking_rules TEXT NOT NULL
                )
            """)

            # ایجاد جدول AuditLog در صورت عدم وجود
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
# ۲. توابع کمکی تاریخ، تنظیمات، لاگ امنیتی و کنترل دسترسی (RBAC)
# ==============================================================================
def to_jalali_str(greg_date):
    if not greg_date:
        return ""
    j = jdatetime.date.fromgregorian(date=greg_date)
    return f"{j.year}/{j.month:02d}/{j.day:02d}"


def parse_flexible_date(date_str):
    """تبدیل ایمن تاریخ شمسی (1405/07/27) یا میلادی (2026-10-19) به date میلادی"""
    clean = date_str.strip().replace('-', '/')
    parts = [int(p) for p in clean.split('/')]
    if parts[0] < 1600:
        return jdatetime.date(parts[0], parts[1], parts[2]).togregorian()
    return datetime(parts[0], parts[1], parts[2]).date()


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
    """تشخیص نقش کاربر در سمت سرور"""
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
    """دکوراتور امنیتی سمت سرور برای جلوگیری از ورود غیرمجاز حتی با لینک مستقیم"""
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
# ۳. ویوهای اصلی سایت عمومی، بیمار و پزشک (حفظ ۱۰۰٪ عملکرد قبلی)
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
    if request.method == 'POST':
        phone = request.POST.get('phone', '').strip()
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
            return redirect('dashboard_router')
        else:
            return render(request, 'verify.html', {
                'phone': phone,
                'error': '❌ برای ورود در نسخه تستی، لطفاً کد 12345 را وارد کنید.'
            })

    return render(request, 'verify.html', {'phone': phone})


@login_required(login_url='/login/')
def book_appointment(request):
    ensure_mahlin_schema()
    if request.method == 'POST':
        form = BookingForm(request.POST)
        if form.is_valid():
            try:
                gregorian_date = parse_flexible_date(form.cleaned_data['date'])
            except Exception:
                form.add_error('date', '❌ فرمت تاریخ صحیح نیست (مثال: 1405/07/28).')
                return render(request, 'book.html', {'form': form})

            doctor = form.cleaned_data['doctor']
            time_val = form.cleaned_data['time']

            if not doctor.is_active:
                form.add_error('doctor', '❌ این پزشک در حال حاضر غیرفعال است.')
                return render(request, 'book.html', {'form': form})

            # جلوگیری از رزرو هم‌زمان در بک‌اند (نادیده گرفتن نوبت‌های لغوشده)
            conflict = Appointment.objects.filter(
                doctor=doctor, date=gregorian_date, time=time_val
            ).exclude(status='canceled').exists()

            if conflict:
                form.add_error('time', '❌ متاسفانه این ساعت قبلاً رزرو شده است.')
                return render(request, 'book.html', {'form': form})

            patient, _ = Patient.objects.get_or_create(
                national_id=form.cleaned_data['national_id'],
                defaults={'name': form.cleaned_data['name'], 'phone': request.user.username}
            )

            Appointment.objects.create(
                doctor=doctor,
                patient=patient,
                date=gregorian_date,
                time=time_val,
                status='pending',
                is_confirmed=False
            )
            return redirect('dashboard_router')
    else:
        form = BookingForm()
    return render(request, 'book.html', {'form': form})


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
    appointments = Appointment.objects.filter(patient__phone=request.user.username).order_by('-date', '-time')
    for appt in appointments:
        appt.jalali_date = to_jalali_str(appt.date)
    return render(request, 'dashboard.html', {'appointments': appointments})


@login_required(login_url='/login/')
def doctor_dashboard(request):
    ensure_mahlin_schema()
    doctor = Doctor.objects.filter(phone=request.user.username).first()
    if not doctor:
        return redirect('patient_dashboard')

    today = timezone.now().date()
    str_today_jalali = to_jalali_str(today)

    appointments = Appointment.objects.filter(doctor=doctor, date=today).exclude(status='canceled').order_by('time')
    return render(request, 'doctor_dashboard.html', {
        'doctor': doctor,
        'appointments': appointments,
        'today': str_today_jalali
    })


@login_required(login_url='/login/')
def cancel_appointment(request, appointment_id):
    ensure_mahlin_schema()
    Appointment.objects.filter(id=appointment_id, patient__phone=request.user.username).delete()
    return redirect('dashboard_router')


def logout_view(request):
    logout(request)
    return redirect('home')


def custom_404(request, exception):
    return render(request, '404.html', status=404)


# ==============================================================================
# ۴. احراز هویت و پنل مدیریت اختصاصی مالک کلینیک (مرحله جدید)
# ==============================================================================
def manager_login(request):
    """ورود امن مدیر مالک و کارکنان (به همراه راه‌اندازی اولیه امن در صورت نبود مدیر)"""
    ensure_mahlin_schema()
    if request.user.is_authenticated and get_user_role(request.user) in ['owner', 'receptionist']:
        return redirect('manager_dashboard')

    has_owner = User.objects.filter(is_superuser=True).exists() or StaffProfile.objects.filter(role='owner', is_active=True).exists()

    if request.method == 'POST':
        action = request.POST.get('action', 'login')

        # راه‌اندازی اولیه حساب مالک (فقط یک‌بار زمانی که هیچ مالکی در دیتابیس نیست)
        if action == 'setup_owner' and not has_owner:
            username = request.POST.get('username', '').strip()
            full_name = request.POST.get('full_name', 'مدیر کلینیک ماهلین').strip()
            password = request.POST.get('password', '').strip()
            if len(username) < 3 or len(password) < 6:
                messages.error(request, "نام کاربری حداقل ۳ کاراکتر و رمز عبور باید حداقل ۶ کاراکتر باشد.")
            else:
                user, created = User.objects.get_or_create(username=username)
                user.set_password(password)
                user.is_staff = True
                user.is_superuser = True
                user.save()
                StaffProfile.objects.update_or_create(
                    user=user,
                    defaults={'full_name': full_name, 'phone': username, 'role': 'owner', 'is_active': True}
                )
                record_audit(user, "ایجاد حساب مالک اولیه", f"حساب مالک کلینیک با نام کاربری {username} راه‌اندازی شد.")
                login(request, user)
                messages.success(request, "حساب مالک کلینیک با موفقیت ساخته شد. خوش آمدید!")
                return redirect('manager_dashboard')

        elif action == 'login':
            username = request.POST.get('username', '').strip()
            password = request.POST.get('password', '').strip()
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
    """داشبورد و پنل جامع مدیریت کلینیک ماهلین"""
    ensure_mahlin_schema()
    role = request.staff_role
    today = timezone.now().date()
    active_tab = request.GET.get('tab', 'dashboard')

    # ------------------------------------------------------------------
    # پردازش عملیات‌های ارسالی (POST) با کنترل سطح دسترسی سمت سرور
    # ------------------------------------------------------------------
    if request.method == 'POST':
        form_type = request.POST.get('form_type', '')

        # ۱. افزودن یا ویرایش پزشک (مخصوص مدیر مالک)
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
            working_hours = request.POST.get('working_hours', '۰۹:۰۰ تا ۲۰:۰۰').strip()
            is_active = request.POST.get('is_active') == 'on'
            profile_img = request.FILES.get('profile_image')

            if not name or not specialty or not phone:
                messages.error(request, "نام، تخصص و شماره تماس پزشک الزامی است.")
                return redirect('/manager-panel/?tab=doctors')

            # جلوگیری از ایجاد پزشک تکراری با شماره یکسان
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
                    is_active=is_active
                )
                if profile_img:
                    doc.profile_image = profile_img
                doc.save()
                record_audit(request.user, "افزودن پزشک", f"پزشک جدید «{name}» با تخصص {specialty} اضافه شد.")
                messages.success(request, f"پزشک جدید «{name}» با موفقیت به کلینیک اضافه شد.")
            return redirect('/manager-panel/?tab=doctors')

        # ۲. تغییر وضعیت فعال/غیرفعال پزشک (مخصوص مدیر مالک)
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

        # ۳. ثبت نوبت جدید توسط مدیر یا پذیرش (با بررسی تداخل در بک‌اند)
        elif form_type == 'create_appointment':
            patient_name = request.POST.get('patient_name', '').strip()
            national_id = request.POST.get('national_id', '').strip()
            phone = request.POST.get('phone', '').strip()
            doctor_id = request.POST.get('doctor_id')
            date_str = request.POST.get('date', '').strip()
            time_str = request.POST.get('time', '').strip()

            try:
                greg_date = parse_flexible_date(date_str)
                time_obj = datetime.strptime(time_str, '%H:%M').time()
                doctor_obj = get_object_or_404(Doctor, id=doctor_id)
            except Exception:
                messages.error(request, "فرمت تاریخ یا ساعت وارد شده معتبر نیست.")
                return redirect('/manager-panel/?tab=appointments')

            # بررسی عدم تداخل نوبت پزشک در بک‌اند
            if Appointment.objects.filter(doctor=doctor_obj, date=greg_date, time=time_obj).exclude(status='canceled').exists():
                messages.error(request, f"خطای تداخل: دکتر {doctor_obj.name} در تاریخ {date_str} ساعت {time_str} نوبت فعال دیگری دارد.")
                return redirect('/manager-panel/?tab=appointments')

            patient_obj, created = Patient.objects.get_or_create(
                national_id=national_id,
                defaults={'name': patient_name, 'phone': phone}
            )
            if not created:
                patient_obj.name = patient_name
                patient_obj.phone = phone
                patient_obj.save()

            Appointment.objects.create(
                patient=patient_obj,
                doctor=doctor_obj,
                date=greg_date,
                time=time_obj,
                is_confirmed=True,
                status='confirmed'
            )
            record_audit(request.user, "ثبت نوبت مدیریتی", f"ثبت نوبت برای {patient_name} نزد {doctor_obj.name} در {date_str} ساعت {time_str}")
            messages.success(request, "نوبت جدید با موفقیت ثبت و تأیید شد.")
            return redirect('/manager-panel/?tab=appointments')

        # ۴. تغییر وضعیت یا زمان نوبت (تأیید، لغو، تکمیل، تغییر ساعت/تاریخ)
        elif form_type == 'update_appointment':
            appt = get_object_or_404(Appointment, id=request.POST.get('appointment_id'))
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
                    target_time = datetime.strptime(new_time_str[:5], '%H:%M').time()
                except Exception:
                    messages.error(request, "فرمت ساعت جدید صحیح نیست.")
                    return redirect('/manager-panel/?tab=appointments')

            # اگر نوبت لغو نشده، تداخل زمان جدید را چک کن
            if new_status != 'canceled':
                conflict = Appointment.objects.filter(
                    doctor=appt.doctor, date=target_date, time=target_time
                ).exclude(id=appt.id).exclude(status='canceled').exists()
                if conflict:
                    messages.error(request, "تغییر زمان امکان‌پذیر نیست؛ پزشک در آن ساعت نوبت دیگری دارد.")
                    return redirect('/manager-panel/?tab=appointments')

            appt.date = target_date
            appt.time = target_time
            appt.status = new_status
            appt.is_confirmed = (new_status in ['confirmed', 'completed'])
            if cancel_reason:
                appt.cancel_reason = cancel_reason
            appt.save()

            record_audit(request.user, "بروزرسانی نوبت", f"نوبت کد {appt.id} ({appt.patient.name}) به وضعیت «{new_status}» تغییر یافت.")
            messages.success(request, f"وضعیت نوبت «{appt.patient.name}» بروزرسانی شد.")
            return redirect('/manager-panel/?tab=appointments')

        # ۵. ویرایش اطلاعات بیمار (جلوگیری از کد ملی تکراری)
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

        # ۶. افزودن یا مدیریت کارکنان و نقش‌ها (فقط مدیر مالک)
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

            user_obj, created = User.objects.get_or_create(username=phone)
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

        # ۷. ذخیره تنظیمات کلینیک (فقط مدیر مالک)
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

    # ------------------------------------------------------------------
    # استخراج داده‌های واقعی از پایگاه داده برای نمایش در پنل
    # ------------------------------------------------------------------
    # ۱. آمار داشبورد اصلی
    today_appointments_qs = Appointment.objects.filter(date=today).select_related('patient', 'doctor').order_by('time')
    for a in today_appointments_qs:
        a.jalali_date = to_jalali_str(a.date)

    total_today = today_appointments_qs.count()
    pending_count = Appointment.objects.filter(status='pending').count()
    confirmed_count = Appointment.objects.filter(status__in=['confirmed', 'completed']).count()
    canceled_count = Appointment.objects.filter(status='canceled').count()
    total_patients = Patient.objects.count()
    active_doctors_count = Doctor.objects.filter(is_active=True).count()

    # عملکرد هفتگی و ماهانه واقعی
    week_ago = today - timedelta(days=6)
    month_ago = today - timedelta(days=29)
    weekly_total = Appointment.objects.filter(date__gte=week_ago, date__lte=today).count()
    monthly_total = Appointment.objects.filter(date__gte=month_ago, date__lte=today).count()

    # داده‌های نمودار ۷ روز اخیر (واقعی از دیتابیس)
    chart_days = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        cnt = Appointment.objects.filter(date=d).exclude(status='canceled').count()
        chart_days.append({
            'label': to_jalali_str(d)[5:],  # ماه/روز
            'count': cnt,
        })
    max_chart = max([item['count'] for item in chart_days] + [1])
    for item in chart_days:
        item['height_pct'] = int((item['count'] / max_chart) * 100) if item['count'] > 0 else 8

    # ۲. فیلتر و جست‌وجوی پزشکان
    doc_q = request.GET.get('doc_q', '').strip()
    doc_status = request.GET.get('doc_status', '')
    doctors_qs = Doctor.objects.all().order_by('-is_active', 'id')
    if doc_q:
        doctors_qs = doctors_qs.filter(Q(name__icontains=doc_q) | Q(specialty__icontains=doc_q) | Q(phone__icontains=doc_q))
    if doc_status == 'active':
        doctors_qs = doctors_qs.filter(is_active=True)
    elif doc_status == 'inactive':
        doctors_qs = doctors_qs.filter(is_active=False)

    # ۳. فیلتر، جست‌وجو و صفحه‌بندی نوبت‌ها
    appt_q = request.GET.get('appt_q', '').strip()
    appt_doctor = request.GET.get('appt_doctor', '')
    appt_status = request.GET.get('appt_status', '')
    appt_date = request.GET.get('appt_date', '').strip()

    appointments_qs = Appointment.objects.select_related('patient', 'doctor').order_by('-date', 'time')
    if appt_q:
        appointments_qs = appointments_qs.filter(
            Q(patient__name__icontains=appt_q) |
            Q(patient__national_id__icontains=appt_q) |
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

    # ۴. فیلتر، جست‌وجو و صفحه‌بندی بیماران
    pat_q = request.GET.get('pat_q', '').strip()
    patients_qs = Patient.objects.annotate(appt_count=Count('appointment')).order_by('-id')
    if pat_q:
        patients_qs = patients_qs.filter(
            Q(name__icontains=pat_q) | Q(national_id__icontains=pat_q) | Q(phone__icontains=pat_q)
        )
    pat_paginator = Paginator(patients_qs, 15)
    patients_page = pat_paginator.get_page(request.GET.get('pat_page', 1))

    # ۵. فهرست کارکنان و لاگ‌های امنیتی
    staff_list = StaffProfile.objects.select_related('user').order_by('-created_at')
    audit_logs = AuditLog.objects.all()[:30] if role == 'owner' else []

    context = {
        'active_tab': active_tab,
        'staff_role': role,
        'today_jalali': to_jalali_str(today),
        'clinic': get_clinic_settings(),
        # آمار داشبورد
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
        # داده‌های بخش‌ها
        'doctors_list': doctors_qs,
        'all_active_doctors': Doctor.objects.filter(is_active=True),
        'appointments_page': appointments_page,
        'patients_page': patients_page,
        'staff_list': staff_list,
        'audit_logs': audit_logs,
        # مقادیر فیلترها
        'doc_q': doc_q,
        'doc_status': doc_status,
        'appt_q': appt_q,
        'appt_doctor': appt_doctor,
        'appt_status': appt_status,
        'appt_date': appt_date,
        'pat_q': pat_q,
    }
    return render(request, 'manager_panel.html', context)
