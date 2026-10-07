from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from .models import Patient, Appointment, OTPRequest, Doctor
from .forms import BookingForm
import jdatetime

def home_page(request):
    doctors = Doctor.objects.all()
    return render(request, 'home.html', {'doctors': doctors})

def login_view(request):
    if request.method == 'POST':
        phone = request.POST.get('phone')
        # ثبت شماره در سشن (بدون تولید کد تصادفی و پرینت در ترمینال)
        request.session['auth_phone'] = phone
        return redirect('verify_otp')
    return render(request, 'login.html')

def verify_otp(request):
    phone = request.session.get('auth_phone')
    if not phone: return redirect('login')

    if request.method == 'POST':
        code = request.POST.get('code')
        
        # 🔑 سیستم تست: ورود فقط با کد ثابت 12345
        if code == '12345':
            user, created = User.objects.get_or_create(username=phone)
            login(request, user)
            return redirect('dashboard_router')
        else:
            return render(request, 'verify.html', {'phone': phone, 'error': '❌ برای ورود در نسخه تستی، لطفاً کد 12345 را وارد کنید.'})
            
    return render(request, 'verify.html', {'phone': phone})

@login_required(login_url='/login/')
def book_appointment(request):
    if request.method == 'POST':
        form = BookingForm(request.POST)
        if form.is_valid():
            shamsi_date = form.cleaned_data['date']
            y, m, d = map(int, shamsi_date.split('/'))
            gregorian_date = jdatetime.date(y, m, d).togregorian()
            
            doctor = form.cleaned_data['doctor']
            time = form.cleaned_data['time']
            
            if Appointment.objects.filter(doctor=doctor, date=gregorian_date, time=time).exists():
                form.add_error('time', '❌ متاسفانه این ساعت رزرو شده است.')
                return render(request, 'book.html', {'form': form})
            
            patient, _ = Patient.objects.get_or_create(
                national_id=form.cleaned_data['national_id'],
                defaults={'name': form.cleaned_data['name'], 'phone': request.user.username}
            )
            
            Appointment.objects.create(doctor=doctor, patient=patient, date=gregorian_date, time=time)
            return redirect('dashboard_router')
    else:
        form = BookingForm()
    return render(request, 'book.html', {'form': form})

@login_required(login_url='/login/')
def dashboard_router(request):
    if Doctor.objects.filter(phone=request.user.username).exists():
        return redirect('doctor_dashboard')
    return redirect('patient_dashboard')

@login_required(login_url='/login/')
def patient_dashboard(request):
    appointments = Appointment.objects.filter(patient__phone=request.user.username).order_by('-date', '-time')
    for appt in appointments:
        jalali = jdatetime.date.fromgregorian(date=appt.date)
        appt.jalali_date = f"{jalali.year}/{jalali.month:02d}/{jalali.day:02d}"
    return render(request, 'dashboard.html', {'appointments': appointments})

@login_required(login_url='/login/')
def doctor_dashboard(request):
    try:
        doctor = Doctor.objects.get(phone=request.user.username)
    except Doctor.DoesNotExist:
        return redirect('patient_dashboard')
    
    today = timezone.now().date()
    today_jalali = jdatetime.date.fromgregorian(date=today)
    str_today_jalali = f"{today_jalali.year}/{today_jalali.month:02d}/{today_jalali.day:02d}"
    
    appointments = Appointment.objects.filter(doctor=doctor, date=today).order_by('time')
    return render(request, 'doctor_dashboard.html', {'doctor': doctor, 'appointments': appointments, 'today': str_today_jalali})

@login_required(login_url='/login/')
def cancel_appointment(request, appointment_id):
    Appointment.objects.filter(id=appointment_id, patient__phone=request.user.username).delete()
    return redirect('dashboard_router')

def logout_view(request):
    logout(request)
    return redirect('home')

def custom_404(request, exception):
    return render(request, '404.html', status=404)