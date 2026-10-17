from django.db import models
import random


class Doctor(models.Model):
    name = models.CharField(max_length=100, verbose_name="نام پزشک")
    specialty = models.CharField(max_length=100, verbose_name="تخصص")
    phone = models.CharField(max_length=15, verbose_name="شماره تماس")
    profile_image = models.ImageField(upload_to='doctors/', null=True, blank=True, verbose_name="عکس پروفایل")
    bio = models.TextField(blank=True, null=True, verbose_name="رزومه و بیوگرافی پزشک")
    working_days = models.CharField(max_length=150, default="شنبه تا چهارشنبه", verbose_name="روزهای حضور")
    working_hours = models.CharField(max_length=100, default="۰۹:۰۰ تا ۲۰:۰۰", verbose_name="ساعات کاری")
    is_active = models.BooleanField(default=True, verbose_name="فعال در سایت؟")
    display_order = models.PositiveIntegerField(default=1, verbose_name="ترتیب نمایش")

    def __str__(self):
        return f"{self.name} - {self.specialty}"

    @property
    def average_rating(self):
        approved_reviews = self.reviews.filter(is_approved=True)
        if not approved_reviews.exists():
            return 5.0
        total = sum(r.rating for r in approved_reviews)
        return round(total / approved_reviews.count(), 1)

    class Meta:
        verbose_name = "پزشک"
        verbose_name_plural = "پزشکان"
        ordering = ['display_order', 'id']


class Patient(models.Model):
    name = models.CharField(max_length=100, verbose_name="نام و نام خانوادگی")
    national_id = models.CharField(max_length=10, unique=True, verbose_name="کد ملی")
    phone = models.CharField(max_length=15, verbose_name="شماره موبایل")
    medical_history = models.TextField(blank=True, null=True, verbose_name="سوابق پزشکی و حساسیت‌ها")

    def __str__(self):
        return f"{self.name} ({self.phone})"

    class Meta:
        verbose_name = "بیمار"
        verbose_name_plural = "بیماران"


class Appointment(models.Model):
    STATUS_CHOICES = [
        ('pending', 'در انتظار تایید'),
        ('confirmed', 'تایید شده'),
        ('completed', 'انجام شده'),
        ('canceled', 'لغو شده'),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='appointments', verbose_name="بیمار")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name='appointments', verbose_name="پزشک")
    date = models.DateField(verbose_name="تاریخ نوبت")
    time = models.TimeField(verbose_name="ساعت نوبت")
    is_confirmed = models.BooleanField(default=False, verbose_name="تایید شده؟")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="وضعیت نوبت")
    doctor_note = models.TextField(blank=True, null=True, verbose_name="یادداشت درمانی / نسخه پزشک")
    cancel_reason = models.CharField(max_length=255, blank=True, null=True, verbose_name="دلیل لغو")
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True, verbose_name="زمان ثبت")

    def __str__(self):
        return f"{self.patient.name} - {self.doctor.name} - {self.date} {self.time}"

    class Meta:
        verbose_name = "نوبت"
        verbose_name_plural = "نوبت‌ها"
        ordering = ['-date', 'time']


class DoctorLeave(models.Model):
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name='leaves', verbose_name="پزشک")
    date = models.DateField(verbose_name="تاریخ مرخصی")
    reason = models.CharField(max_length=200, blank=True, null=True, verbose_name="دلیل عدم حضور")

    def __str__(self):
        return f"مرخصی {self.doctor.name} در تاریخ {self.date}"

    class Meta:
        verbose_name = "مرخصی پزشک"
        verbose_name_plural = "مرخصی‌های پزشکان"


class Review(models.Model):
    patient_name = models.CharField(max_length=100, verbose_name="نام بیمار")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name='reviews', null=True, blank=True, verbose_name="پزشک مربوطه")
    rating = models.PositiveSmallIntegerField(default=5, verbose_name="امتیاز (۱ تا ۵)")
    comment = models.TextField(verbose_name="متن نظر")
    is_approved = models.BooleanField(default=True, verbose_name="تایید شده برای نمایش؟")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ثبت")

    def __str__(self):
        return f"{self.patient_name} ({self.rating} ستاره)"

    class Meta:
        verbose_name = "نظر بیمار"
        verbose_name_plural = "نظرات بیماران"
        ordering = ['-created_at']


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
