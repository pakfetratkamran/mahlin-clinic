from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from datetime import timedelta
import jdatetime
import urllib.request
import urllib.parse

from .models import Patient, Appointment, OTPRequest, Doctor, DoctorLeave, Review
from .forms import BookingForm


# تابع کمکی ارسال پیامک (بدون نیاز به نصب کتابخانه اضافی)
def send_sms_notification(phone, message_text):
    """
    برای فعال‌سازی واقعی، کافیست API_KEY پنل پیامکی (مثلاً کاوه‌نگار یا ملی‌پیامک)
    را در متغیر زیر قرار دهی.
    """
    api_key = ""  # کلید API پنل پیامک را اینجا قرار بده
    if not api_key:
        print(f"[SMS SIMULATION] To: {phone} | Message: {message_text}")
        return True
    try:
        params = urllib.parse.urlencode({'receptor': phone, 'message': message_text})
        url = f"https://api.kavenegar.com/v1/{api_key}/sms/send.json?{params}"
        urllib.request.urlopen(url, timeout=5)
        return True
    except Exception as e:
        print("SMS Error:", e)
        return False


def to_jalali_str(gregorian_date):
    jalali = jdatetime.date.fromgregorian(date=gregorian_date)
    return f"{jalali.year}/{jalali.month:02d}/{jalali.day:02d}"


def parse_date_input(date_str):
    """پشتیبانی هم‌زمان از تاریخ شمسی (1405/07/26) و میلادی (2026-10-18)"""
    date_str = date_str.strip().replace('-', '/')
    parts = list(map(int, date_str.split('/')))
    if parts[0] < 1600:
        return jdatetime.date(parts[0], parts[1], parts[2]).togregorian()
    return timezone.datetime(parts[0], parts[1], parts[2]).date()


# ۱. صفحه اصلی سایت عمومی
def home_page(request):
    doctors = Doctor.objects.filter(is_active=True)
    reviews = Review.objects.filter(is_approved=True)[:6]
    return render(request, 'home.html', {
        'doctors': doctors,
        'reviews': reviews,
    })


# ۲. صفحه آرشیو پزشکان (همراه با جست‌وجو و فیلتر تخصص)
def doctor_list(request):
    query = request.GET.get('q', '').strip()
    specialty = request.GET.get('specialty', '').strip()

    doctors = Doctor.objects.filter(is_active=True)
    if query:
        doctors = doctors.filter(Q(name__icontains=query) | Q(specialty__icontains=query))
    if specialty:
        doctors = doctors.filter(specialty__icontains=specialty)

    specialties = Doctor.objects.filter(is_active=True).values_list('specialty', flat=True).distinct()
    return render(request, 'doctor_list.html', {
        'doctors': doctors,
        'specialties': specialties,
        'query': query,
        'selected_specialty': specialty,
    })


# ۳. صفحه تکی پروفایل پزشک (Single Doctor) + ثبت نظر
def doctor_detail(request, pk):
    doctor = get_object_or_404(Doctor, pk=pk)
    reviews = doctor.reviews.filter(is_approved=True)

    if request.method == 'POST' and 'submit_review' in request.POST:
        patient_name = request.POST.get('patient_name', 'مراجعه‌کننده')
        rating = int(request.POST.get('rating', 5))
        comment = request.POST.get('comment', '').strip()
        if comment:
            Review.objects.create(
                patient_name=patient_name,
                doctor=doctor,
                rating=min(max(rating, 1), 5),
                comment=comment
            )
            messages.success(request, 'دیدگاه و امتیاز شما با موفقیت ثبت شد.')
            return redirect('doctor_detail', pk=doctor.pk)

    return render(request, 'doctor_detail.html', {
        'doctor': doctor,
        'reviews': reviews,
    })


# ۴. صفحات درباره ما و تماس با ما
def about_us(request):
    return render(request, 'about.html')


def contact_us(request):
    if request.method == 'POST':
        messages.success(request, 'پیام شما دریافت شد. کارشناسان کلینیک ماهلین به‌زودی با شما تماس می‌گیرند.')
        return redirect('contact')
    return render(request, 'contact.html')


# ۵. ورود و احراز هویت
def login_view(request):
    if request.method == 'POST':
        phone = request.POST.get('phone', '').strip()
        request.session['auth_phone'] = phone
        return redirect('verify_otp')
    return render(request, 'login.html')


def verify_otp(request):
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
                'error': 'برای ورود در نسخه تستی، لطفاً کد 12345 را وارد کنید.'
            })

    return render(request, 'verify.html', {'phone': phone})


# ۶. سیستم نوبت‌دهی هوشمند (بررسی تداخل + بررسی مرخصی پزشک + ارسال پیامک)
@login_required(login_url='/login/')
def book_appointment(request):
    if request.method == 'POST':
        form = BookingForm(request.POST)
        if form.is_valid():
            try:
                gregorian_date = parse_date_input(form.cleaned_data['date'])
            except Exception:
                form.add_error('date', 'فرمت تاریخ وارد شده صحیح نیست (مثال: 1405/07/26).')
                return render(request, 'book.html', {'form': form})

            doctor = form.cleaned_data['doctor']
            time = form.cleaned_data['time']

            # بررسی مرخصی پزشک
            if DoctorLeave.objects.filter(doctor=doctor, date=gregorian_date).exists():
                form.add_error('date', 'پزشک انتخابی در این تاریخ در مرخصی هستند. لطفاً روز دیگری را انتخاب کنید.')
                return render(request, 'book.html', {'form': form})

            # بررسی پر بودن ساعت
            if Appointment.objects.filter(doctor=doctor, date=gregorian_date, time=time).exclude(status='canceled').exists():
                form.add_error('time', 'متاسفانه این ساعت قبلاً رزرو شده است.')
                return render(request, 'book.html', {'form': form})

            patient, _ = Patient.objects.get_or_create(
                national_id=form.cleaned_data['national_id'],
                defaults={'name': form.cleaned_data['name'], 'phone': request.user.username}
            )

            appt = Appointment.objects.create(
                doctor=doctor,
                patient=patient,
                date=gregorian_date,
                time=time
            )

            # ارسال پیامک تایید ثبت نوبت
            shamsi_str = to_jalali_str(gregorian_date)
            sms_text = f"{patient.name} عزیز، نوبت شما نزد {doctor.name} برای تاریخ {shamsi_str} ساعت {time} در کلینیک ماهلین ثبت شد."
            send_sms_notification(request.user.username, sms_text)

            messages.success(request, 'نوبت شما با موفقیت ثبت شد و پیامک اطلاع‌رسانی ارسال گردید.')
            return redirect('dashboard_router')
    else:
        form = BookingForm()
    return render(request, 'book.html', {'form': form})


@login_required(login_url='/login/')
def dashboard_router(request):
    if Doctor.objects.filter(phone=request.user.username).exists():
        return redirect('doctor_dashboard')
    return redirect('patient_dashboard')


# ۷. پنل اختصاصی بیمار (مشاهده نوبت‌ها + ثبت نظر)
@login_required(login_url='/login/')
def patient_dashboard(request):
    if request.method == 'POST' and 'submit_review' in request.POST:
        doctor_id = request.POST.get('doctor_id')
        rating = int(request.POST.get('rating', 5))
        comment = request.POST.get('comment', '').strip()
        doctor_obj = Doctor.objects.filter(id=doctor_id).first()
        patient_obj = Patient.objects.filter(phone=request.user.username).first()
        if comment:
            Review.objects.create(
                patient_name=patient_obj.name if patient_obj else request.user.username,
                doctor=doctor_obj,
                rating=rating,
                comment=comment
            )
            messages.success(request, 'نظر شما با موفقیت ثبت شد.')
            return redirect('patient_dashboard')

    appointments = Appointment.objects.filter(patient__phone=request.user.username).order_by('-date', '-time')
    for appt in appointments:
        appt.jalali_date = to_jalali_str(appt.date)

    doctors = Doctor.objects.filter(is_active=True)
    return render(request, 'dashboard.html', {
        'appointments': appointments,
        'doctors': doctors,
    })


# ۸. پنل اختصاصی پزشک (رفع باگ MultipleObjectsReturned + تقویم هفتگی و مدیریت نوبت)
@login_required(login_url='/login/')
def doctor_dashboard(request):
    # استفاده از filter().first() به جای get() برای جلوگیری کامل از کرش کردن سایت
    doctor = Doctor.objects.filter(phone=request.user.username).first()
    if not doctor:
        return redirect('patient_dashboard')

    # عملیات‌های پزشک (تایید نوبت، ثبت یادداشت درمانی، لغو نوبت، یا ثبت مرخصی)
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'confirm_appt':
            appt_id = request.POST.get('appointment_id')
            appt = Appointment.objects.filter(id=appt_id, doctor=doctor).first()
            if appt:
                appt.is_confirmed = True
                appt.status = 'confirmed'
                appt.save()
                send_sms_notification(appt.patient.phone, f"نوبت شما نزد {doctor.name} تایید نهایی شد.")
                messages.success(request, 'نوبت بیمار تایید شد.')

        elif action == 'save_note':
            appt_id = request.POST.get('appointment_id')
            note = request.POST.get('doctor_note', '').strip()
            appt = Appointment.objects.filter(id=appt_id, doctor=doctor).first()
            if appt:
                appt.doctor_note = note
                appt.status = 'completed'
                appt.is_confirmed = True
                appt.save()
                messages.success(request, 'یادداشت درمانی و پرونده بیمار بروزرسانی شد.')

        elif action == 'add_leave':
            leave_date_str = request.POST.get('leave_date', '').strip()
            reason = request.POST.get('reason', 'مرخصی پزشک').strip()
            try:
                leave_gregorian = parse_date_input(leave_date_str)
                DoctorLeave.objects.get_or_create(doctor=doctor, date=leave_gregorian, defaults={'reason': reason})
                messages.success(request, 'روز مرخصی در تقویم ثبت شد و نوبت‌دهی آن روز بسته شد.')
            except Exception:
                messages.error(request, 'فرمت تاریخ مرخصی صحیح نیست.')

        return redirect('doctor_dashboard')

    # محاسبه تاریخ انتخاب‌شده در تقویم هفتگی پنل پزشک
    today = timezone.now().date()
    selected_date_str = request.GET.get('date')
    if selected_date_str:
        try:
            selected_date = timezone.datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = today
    else:
        selected_date = today

    # ساخت نوار تقویم ۷ روزه (مشابه عکس پنل پزشک)
    persian_weekdays = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه', 'شنبه', 'یکشنبه']
    week_days = []
    for i in range(-1, 6):
        day_date = today + timedelta(days=i)
        jalali_d = jdatetime.date.fromgregorian(date=day_date)
        week_days.append({
            'gregorian': day_date.strftime('%Y-%m-%d'),
            'day_num': jalali_d.day,
            'weekday_name': persian_weekdays[day_date.weekday()],
            'is_selected': day_date == selected_date,
            'is_today': day_date == today,
        })

    appointments = Appointment.objects.filter(doctor=doctor, date=selected_date).exclude(status='canceled').order_by('time')
    for appt in appointments:
        appt.jalali_date = to_jalali_str(appt.date)

    # آمار کارت‌های بالای داشبورد پزشک
    total_doctor_appointments = Appointment.objects.filter(doctor=doctor).count()
    confirmed_count = Appointment.objects.filter(doctor=doctor, is_confirmed=True).count()
    leaves = DoctorLeave.objects.filter(doctor=doctor, date__gte=today).order_by('date')
    for lv in leaves:
        lv.jalali_date = to_jalali_str(lv.date)

    return render(request, 'doctor_dashboard.html', {
        'doctor': doctor,
        'appointments': appointments,
        'today': to_jalali_str(selected_date),
        'week_days': week_days,
        'total_appointments': total_doctor_appointments,
        'confirmed_count': confirmed_count,
        'leaves': leaves,
    })


@login_required(login_url='/login/')
def cancel_appointment(request, appointment_id):
    appt = Appointment.objects.filter(id=appointment_id, patient__phone=request.user.username).first()
    if appt:
        send_sms_notification(request.user.username, f"نوبت شما در کلینیک ماهلین لغو گردید.")
        appt.delete()
        messages.info(request, 'نوبت شما لغو گردید.')
    return redirect('dashboard_router')


def logout_view(request):
    logout(request)
    return redirect('home')


def custom_404(request, exception):
    return render(request, '404.html', status=404)
