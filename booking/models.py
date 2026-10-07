from django.db import models
import random

class Doctor(models.Model):
    name = models.CharField(max_length=100, verbose_name="نام پزشک")
    specialty = models.CharField(max_length=100, verbose_name="تخصص")
    phone = models.CharField(max_length=15, verbose_name="شماره تماس")
    profile_image = models.ImageField(upload_to='doctors/', null=True, blank=True, verbose_name="عکس پروفایل")

    def __str__(self):
        return f"{self.name} - {self.specialty}"

    class Meta:
        verbose_name = "پزشک"
        verbose_name_plural = "پزشکان"

class Patient(models.Model):
    name = models.CharField(max_length=100, verbose_name="نام و نام خانوادگی")
    national_id = models.CharField(max_length=10, unique=True, verbose_name="کد ملی")
    phone = models.CharField(max_length=15, verbose_name="شماره موبایل")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "بیمار"
        verbose_name_plural = "بیماران"

class Appointment(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, verbose_name="بیمار")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, verbose_name="پزشک")
    date = models.DateField(verbose_name="تاریخ نوبت")
    time = models.TimeField(verbose_name="ساعت نوبت")
    is_confirmed = models.BooleanField(default=False, verbose_name="تایید شده؟")

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