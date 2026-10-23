from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.models import User
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import connection, transaction
from django.db.models import Q, Count
from django.core.paginator import Paginator
from datetime import timedelta, datetime, time as dt_time
from functools import wraps
import json
import jdatetime

from .models import (
    Patient, Appointment, OTPRequest, Doctor,
    StaffProfile, ClinicSettings, AuditLog, generate_tracking_code
)

_SCHEMA_CHECKED = False

def ensure_mahlin_schema():
    global _SCHEMA_CHECKED
    if _SCHEMA_CHECKED:
        return
    try:
        with connection.cursor() as cursor:
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

            cursor.execute("PRAGMA table_info(booking_patient)")
            pat_cols = [row[1] for row in cursor.fetchall()]
            if pat_cols and 'medical_notes' not in pat_cols:
                cursor.execute("ALTER TABLE booking_patient ADD COLUMN medical_notes TEXT DEFAULT ''")

            cursor.execute("PRAGMA table_info(booking_appointment)")
            app_cols = [row[1] for row in cursor.fetchall()]
            if app_cols:
                if 'status' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN status VARCHAR(20) DEFAULT 'pending'")
                    cursor.execute("UPDATE booking_appointment SET status = 'confirmed' WHERE is_confirmed = 1")
                if 'cancel_reason' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN cancel_reason VARCHAR(255) DEFAULT ''")
                if 'tracking_code' not in app_cols:
                    cursor.execute("ALTER TABLE booking_appointment ADD COLUMN tracking_code VARCHAR(20) DEFAULT 'MHL-100000'")

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


def to_jalali_str(greg_date):
    if not greg_date:
        return ""
    j = jdatetime.date.fromgregorian(date=greg_date)
    return f"{j.year}/{j.month:02d}/{j.day:02d}"


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
                return redirect('manager_login')
            role = get_user_role(request.user)
            if role not in allowed_roles:
                return redirect('home')
            request.staff_role = role
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


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
    doctors = Doctor.objects.filter(is_active=True).order_by('id')
    existing_patient = Patient.objects.filter(phone=request.user.username).first()

    # ارسال ساعت‌های رزرو شده به صفحه جهت غیرفعال‌سازی خودکار دکمه‌های پر شده
    today = timezone.now().date()
    active_appts = Appointment.objects.filter(date__gte=today).exclude(status='canceled')
    booked_map = {}
    for a in active_appts:
        key = f"{a.doctor_id}_{to_jalali_str(a.date)}"
        booked_map.setdefault(key, []).append(a.time.strftime("%H:%M"))

    if request.method == 'POST':
        doctor_id = request.POST.get('doctor', '').strip()
        date_str = request.POST.get('date', '').strip()
        time_str = request.POST.get('time', '').strip()
        name = request.POST.get('name', '').strip()
        national_id = request.POST.get('national_id', '').strip()

        if not (doctor_id and date_str and time_str and name and national_id):
            messages.error(request, "لطفاً پزشک، روز، ساعت، نام و کد ملی خود را کامل انتخاب و وارد کنید.")
            return render(request, 'book.html', {
                'doctors': doctors,
                'patient': existing_patient,
                'booked_json': json.dumps(booked_map),
            })

        try:
            gregorian_date = parse_flexible_date(date_str)
            time_val = parse_time_str(time_str)
            doctor = Doctor.objects.get(id=doctor_id, is_active=True)
        except Exception:
            messages.error(request, "اطلاعات تاریخ، ساعت یا پزشک معتبر نیست.")
            return render(request, 'book.html', {
                'doctors': doctors,
                'patient': existing_patient,
                'booked_json': json.dumps(booked_map),
            })

        with transaction.atomic():
            conflict = Appointment.objects.filter(
                doctor=doctor, date=gregorian_date, time=time_val
            ).exclude(status='canceled').exists()

            if conflict:
                messages.error(request, f"❌ متأسفانه ساعت {time_str} در تاریخ {to_jalali_str(gregorian_date)} قبلاً رزرو شده است. لطفاً ساعت دیگری را انتخاب کنید.")
                return render(request, 'book.html', {
                    'doctors': doctors,
                    'patient': existing_patient,
                    'booked_json': json.dumps(booked_map),
                })

            patient, created = Patient.objects.get_or_create(
                national_id=national_id,
                defaults={'name': name, 'phone': request.user.username}
            )
            if not created:
                patient.name = name
                patient.phone = request.user.username
                patient.save()

            t_code = generate_tracking_code()
            Appointment.objects.create(
                doctor=doctor,
                patient=patient,
                date=gregorian_date,
                time=time_val,
                tracking_code=t_code,
                status='pending',
                is_confirmed=False
            )

        messages.success(request, f"✅ نوبت شما با موفقیت ثبت شد! کد پیگیری: {t_code}")
        return redirect('patient_dashboard')

    return render(request, 'book.html', {
        'doctors': doctors,
        'patient': existing_patient,
        'booked_json': json.dumps(booked_map),
    })


@login_required(login_url='/login/')
def api_available_slots(request):
    return JsonResponse({'ok': True})


@login_required(login_url='/login/')
def reschedule_appointment(request, appointment_id):
    return redirect('patient_dashboard')


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
    appointments = Appointment.objects.filter(
        patient__phone=request.user.username
    ).select_related('doctor', 'patient').order_by('-date', '-time')

    for appt in appointments:
        appt.jalali_date = to_jalali_str(appt.date)

    return render(request, 'dashboard.html', {
        'appointments': appointments,
        'clinic': get_clinic_settings(),
    })


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
    appt = Appointment.objects.filter(id=appointment_id, patient__phone=request.user.username).first()
    if appt:
        appt.delete()
        messages.info(request, "نوبت موردنظر با موفقیت لغو و حذف گردید.")
    return redirect('patient_dashboard')


def logout_view(request):
    logout(request)
    return redirect('home')


def custom_404(request, exception):
    return render(request, '404.html', status=404)


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
                return redirect('manager_dashboard')
        else:
            user = authenticate(request, username=username, password=password)
            if user is not None:
                role = get_user_role(user)
                if role in ['owner', 'receptionist']:
                    login(request, user)
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
    today = timezone.now().date()
    active_tab = request.GET.get('tab', 'dashboard')

    if request.method == 'POST':
        form_type = request.POST.get('form_type', '')

        if form_type == 'save_doctor':
            if role != 'owner':
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

            dup_qs = Doctor.objects.filter(phone=phone)
            if doc_id:
                dup_qs = dup_qs.exclude(id=doc_id)
            if dup_qs.exists():
                messages.error(request, f"پزشکی با شماره تماس {phone} قبلاً ثبت شده است.")
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
                messages.success(request, f"اطلاعات «{name}» بروزرسانی شد.")
            else:
                doc = Doctor(
                    name=name, specialty=specialty, phone=phone,
                    bio=bio, working_days=working_days, working_hours=working_hours,
                    is_active=is_active
                )
                if profile_img:
                    doc.profile_image = profile_img
                doc.save()
                messages.success(request, f"پزشک جدید «{name}» اضافه شد.")
            return redirect('/manager-panel/?tab=doctors')

        elif form_type == 'toggle_doctor':
            if role == 'owner':
                doc = get_object_or_404(Doctor, id=request.POST.get('doctor_id'))
                doc.is_active = not doc.is_active
                doc.save()
            return redirect('/manager-panel/?tab=doctors')

        elif form_type == 'delete_doctor':
            if role == 'owner':
                doc = get_object_or_404(Doctor, id=request.POST.get('doctor_id'))
                doc_name = doc.name
                doc.delete()
                messages.warning(request, f"پزشک «{doc_name}» به طور کامل حذف گردید.")
            return redirect('/manager-panel/?tab=doctors')

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
                doctor_obj = get_object_or_404(Doctor, id=doctor_id)
            except Exception:
                messages.error(request, "فرمت تاریخ یا ساعت معتبر نیست.")
                return redirect('/manager-panel/?tab=appointments')

            if Appointment.objects.filter(doctor=doctor_obj, date=greg_date, time=time_obj).exclude(status='canceled').exists():
                messages.error(request, "این ساعت برای پزشک انتخابی قبلاً رزرو شده است.")
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
                tracking_code=generate_tracking_code(),
                is_confirmed=True,
                status='confirmed'
            )
            messages.success(request, "نوبت جدید ثبت و تأیید شد.")
            return redirect('/manager-panel/?tab=appointments')

        elif form_type == 'update_appointment':
            appt = get_object_or_404(Appointment, id=request.POST.get('appointment_id'))
            new_status = request.POST.get('status', appt.status)
            new_date_str = request.POST.get('new_date', '').strip()
            new_time_str = request.POST.get('new_time', '').strip()

            target_date = appt.date
            target_time = appt.time
            if new_date_str:
                try:
                    target_date = parse_flexible_date(new_date_str)
                except Exception:
                    pass
            if new_time_str:
                try:
                    target_time = parse_time_str(new_time_str)
                except Exception:
                    pass

            appt.date = target_date
            appt.time = target_time
            appt.status = new_status
            appt.is_confirmed = (new_status in ['confirmed', 'completed'])
            appt.save()
            messages.success(request, "وضعیت نوبت بروزرسانی شد.")
            return redirect('/manager-panel/?tab=appointments')

        elif form_type == 'update_patient':
            pat = get_object_or_404(Patient, id=request.POST.get('patient_id'))
            pat.name = request.POST.get('name', pat.name).strip()
            pat.national_id = request.POST.get('national_id', pat.national_id).strip()
            pat.phone = request.POST.get('phone', pat.phone).strip()
            if role == 'owner':
                pat.medical_notes = request.POST.get('medical_notes', '').strip()
            pat.save()
            messages.success(request, "اطلاعات بیمار بروزرسانی شد.")
            return redirect('/manager-panel/?tab=patients')

        elif form_type == 'save_staff' and role == 'owner':
            full_name = request.POST.get('full_name', '').strip()
            phone = request.POST.get('phone', '').strip()
            staff_role = request.POST.get('role', 'receptionist')
            password = request.POST.get('password', '').strip()
            is_active = request.POST.get('is_active') == 'on'

            if full_name and phone:
                user_obj, _ = User.objects.get_or_create(username=phone)
                if password:
                    user_obj.set_password(password)
                user_obj.is_staff = (staff_role == 'owner')
                user_obj.is_active = is_active
                user_obj.save()
                StaffProfile.objects.update_or_create(
                    user=user_obj,
                    defaults={'full_name': full_name, 'phone': phone, 'role': staff_role, 'is_active': is_active}
                )
                messages.success(request, "حساب کاربری ذخیره شد.")
            return redirect('/manager-panel/?tab=staff')

        elif form_type == 'save_settings' and role == 'owner':
            c_settings = get_clinic_settings()
            c_settings.clinic_name = request.POST.get('clinic_name', c_settings.clinic_name).strip()
            c_settings.slogan = request.POST.get('slogan', c_settings.slogan).strip()
            c_settings.phone = request.POST.get('phone', c_settings.phone).strip()
            c_settings.address = request.POST.get('address', c_settings.address).strip()
            c_settings.working_hours = request.POST.get('working_hours', c_settings.working_hours).strip()
            c_settings.holidays_notice = request.POST.get('holidays_notice', '').strip()
            c_settings.booking_rules = request.POST.get('booking_rules', c_settings.booking_rules).strip()
            c_settings.save()
            messages.success(request, "تنظیمات کلینیک ذخیره شد.")
            return redirect('/manager-panel/?tab=settings')

    today_appointments_qs = Appointment.objects.filter(date=today).select_related('patient', 'doctor').order_by('time')
    for a in today_appointments_qs:
        a.jalali_date = to_jalali_str(a.date)

    chart_days = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        cnt = Appointment.objects.filter(date=d).exclude(status='canceled').count()
        chart_days.append({'label': to_jalali_str(d)[5:], 'count': cnt})
    max_chart = max([item['count'] for item in chart_days] + [1])
    for item in chart_days:
        item['height_pct'] = int((item['count'] / max_chart) * 100) if item['count'] > 0 else 8

    doc_q = request.GET.get('doc_q', '').strip()
    doctors_qs = Doctor.objects.all().order_by('-is_active', 'id')
    if doc_q:
        doctors_qs = doctors_qs.filter(Q(name__icontains=doc_q) | Q(specialty__icontains=doc_q))

    appointments_qs = Appointment.objects.select_related('patient', 'doctor').order_by('-date', 'time')
    appt_paginator = Paginator(appointments_qs, 15)
    appointments_page = appt_paginator.get_page(request.GET.get('appt_page', 1))
    for a in appointments_page:
        a.jalali_date = to_jalali_str(a.date)

    patients_qs = Patient.objects.annotate(appt_count=Count('appointment')).order_by('-id')
    pat_paginator = Paginator(patients_qs, 15)
    patients_page = pat_paginator.get_page(request.GET.get('pat_page', 1))

    context = {
        'active_tab': active_tab,
        'staff_role': role,
        'today_jalali': to_jalali_str(today),
        'clinic': get_clinic_settings(),
        'total_today': today_appointments_qs.count(),
        'pending_count': Appointment.objects.filter(status='pending').count(),
        'confirmed_count': Appointment.objects.filter(status__in=['confirmed', 'completed']).count(),
        'canceled_count': Appointment.objects.filter(status='canceled').count(),
        'total_patients': Patient.objects.count(),
        'active_doctors_count': Doctor.objects.filter(is_active=True).count(),
        'weekly_total': Appointment.objects.filter(date__gte=today - timedelta(days=6)).count(),
        'monthly_total': Appointment.objects.filter(date__gte=today - timedelta(days=29)).count(),
        'today_appointments': today_appointments_qs,
        'chart_days': chart_days,
        'doctors_list': doctors_qs,
        'all_active_doctors': Doctor.objects.filter(is_active=True),
        'appointments_page': appointments_page,
        'patients_page': patients_page,
        'staff_list': StaffProfile.objects.select_related('user').order_by('-created_at'),
        'audit_logs': AuditLog.objects.all()[:30] if role == 'owner' else [],
        'doc_q': doc_q,
    }
    return render(request, 'manager_panel.html', context)
