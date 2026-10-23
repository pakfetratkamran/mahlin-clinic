from django.db import models
from django.contrib.auth.models import User
import random
import string


def generate_tracking_code():
    digits = ''.join(random.choices(string.digits, k=6))
    return f"MHL-{digits}"


class Doctor(models.Model):
    name = models.CharField(max_length=100, verbose_name="نام پزشک")
    specialty = models.CharField(max_length=100, verbose_name="تخصص")
    phone = models.CharField(max_length=15, verbose_name="شماره تماس")
    profile_image = models.ImageField(upload_to='doctors/', null=True, blank=True, verbose_name="عکس پروفایل")
    bio = models.TextField(blank=True, null=True, default="", verbose_name="سوابق و توضیحات")
    working_days = models.CharField(max_length=150, default="شنبه تا چهارشنبه", verbose_name="روزهای حضور")
    working_hours = models.CharField(max_length=100, default="۰۹:۰۰ تا ۲۰:۰۰", verbose_name="ساعات حضور")
    is_active = models.BooleanField(default=True, verbose_name="فعال در سایت؟")

    def __str__(self):
        return f"{self.name} - {self.specialty}"

    class Meta:
        verbose_name = "پزشک"
        verbose_name_plural = "پزشکان"


class Patient(models.Model):
    name = models.CharField(max_length=100, verbose_name="نام و نام خانوادگی")
    national_id = models.CharField(max_length=10, unique=True, verbose_name="کد ملی")
    phone = models.CharField(max_length=15, verbose_name="شماره موبایل")
    medical_notes = models.TextField(blank=True, null=True, default="", verbose_name="پرونده پزشکی")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "بیمار"
        verbose_name_plural = "بیماران"


class Appointment(models.Model):
    STATUS_CHOICES = [
        ('pending', 'در انتظار تأیید'),
        ('confirmed', 'تأییدشده'),
        ('completed', 'تکمیل‌شده'),
        ('canceled', 'لغوشده'),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, verbose_name="بیمار")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, verbose_name="پزشک")
    date = models.DateField(verbose_name="تاریخ نوبت")
    time = models.TimeField(verbose_name="ساعت نوبت")
    tracking_code = models.CharField(max_length=20, default=generate_tracking_code, verbose_name="کد پیگیری")
    is_confirmed = models.BooleanField(default=False, verbose_name="تایید شده؟")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="وضعیت")
    cancel_reason = models.CharField(max_length=255, blank=True, null=True, default="", verbose_name="توضیحات")

    def __str__(self):
        return f"{self.patient.name} - {self.doctor.name} - {self.date} {self.time}"

    class Meta:
        verbose_name = "نوبت"
        verbose_name_plural = "نوبت‌ها"


class OTPRequest(models.Model):
    phone = models.CharField(max_length=15, verbose_name="شماره موبایل")
    code = models.CharField(max_length=6, verbose_name="کد تایید")
    created_at = models.DateTimeField(auto_now_add=True)

    def generate_code(self):
        self.code = str(random.randint(10000, 99999))
        self.save()
        return self.code


class StaffProfile(models.Model):
    ROLE_CHOICES = [
        ('owner', 'مدیر مالک'),
        ('receptionist', 'منشی / پذیرش'),
        ('doctor', 'پزشک'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_profile')
    full_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15, unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='receptionist')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ClinicSettings(models.Model):
    clinic_name = models.CharField(max_length=120, default="کلینیک دندان‌پزشکی ماهلین")
    slogan = models.CharField(max_length=150, default="لبخندی سالم، حسی ماندگار")
    phone = models.CharField(max_length=50, default="۰۲۱-۰۰۰۰۰۰۰۰")
    address = models.TextField(default="کلینیک تخصصی دندان‌پزشکی ماهلین")
    map_link = models.CharField(max_length=300, blank=True, default="")
    working_hours = models.CharField(max_length=200, default="شنبه تا چهارشنبه: ۰۹:۰۰ الی ۲۰:۰۰ | پنج‌شنبه‌ها: ۰۹:۰۰ الی ۱۴:۰۰")
    holidays_notice = models.CharField(max_length=250, blank=True, default="جمعه‌ها و تعطیلات رسمی کلینیک تعطیل است.")
    booking_rules = models.TextField(default="لطفاً ۱۵ دقیقه پیش از زمان نوبت در کلینیک حضور داشته باشید.")


class AuditLog(models.Model):
    actor = models.CharField(max_length=100)
    action_type = models.CharField(max_length=80)
    details = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
