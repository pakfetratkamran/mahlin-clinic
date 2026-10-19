from django.db import models
from django.contrib.auth.models import User
import random


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
    medical_notes = models.TextField(blank=True, null=True, default="", verbose_name="پرونده و سوابق پزشکی (محرمانه)")

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
    is_confirmed = models.BooleanField(default=False, verbose_name="تایید شده؟")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="وضعیت")
    cancel_reason = models.CharField(max_length=255, blank=True, null=True, default="", verbose_name="دلیل لغو / توضیحات")

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

    class Meta:
        verbose_name = "کد تایید"
        verbose_name_plural = "کدهای تایید"


class StaffProfile(models.Model):
    ROLE_CHOICES = [
        ('owner', 'مدیر مالک'),
        ('receptionist', 'منشی / پذیرش'),
        ('doctor', 'پزشک'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_profile', verbose_name="حساب کاربری")
    full_name = models.CharField(max_length=100, verbose_name="نام و نام خانوادگی")
    phone = models.CharField(max_length=15, unique=True, verbose_name="شماره همراه / کد کاربری")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='receptionist', verbose_name="نقش سازمانی")
    is_active = models.BooleanField(default=True, verbose_name="وضعیت حساب")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.full_name} ({self.get_role_display()})"


class ClinicSettings(models.Model):
    clinic_name = models.CharField(max_length=120, default="کلینیک دندان‌پزشکی ماهلین", verbose_name="نام کلینیک")
    slogan = models.CharField(max_length=150, default="لبخندی سالم، حسی ماندگار", verbose_name="شعار کلینیک")
    phone = models.CharField(max_length=50, default="۰۲۱-۰۰۰۰۰۰۰۰", verbose_name="شماره تماس کلینیک")
    address = models.TextField(default="تهران، کلینیک تخصصی دندان‌پزشکی ماهلین", verbose_name="آدرس کلینیک")
    map_link = models.CharField(max_length=300, blank=True, default="", verbose_name="لینک مسیریابی")
    working_hours = models.CharField(max_length=200, default="شنبه تا چهارشنبه: ۰۹:۰۰ الی ۲۰:۰۰ | پنج‌شنبه‌ها: ۰۹:۰۰ الی ۱۴:۰۰", verbose_name="ساعات کاری")
    holidays_notice = models.CharField(max_length=250, blank=True, default="جمعه‌ها و تعطیلات رسمی کلینیک تعطیل است.", verbose_name="اطلاعیه تعطیلات")
    booking_rules = models.TextField(default="لطفاً ۱۵ دقیقه پیش از زمان نوبت در کلینیک حضور داشته باشید.", verbose_name="قوانین عمومی نوبت‌دهی")

    def __str__(self):
        return self.clinic_name


class AuditLog(models.Model):
    actor = models.CharField(max_length=100, verbose_name="انجام‌دهنده")
    action_type = models.CharField(max_length=80, verbose_name="نوع عملیات")
    details = models.TextField(verbose_name="جزئیات رویداد")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ثبت")

    class Meta:
        ordering = ['-created_at']
