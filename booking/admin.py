from django.contrib import admin
from django.utils.html import format_html
from .models import Doctor, Patient, Appointment

@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    # این تابع عکس پروفایل رو به صورت یک دایره کوچیک تو پنل نشون میده
    def image_tag(self, obj):
        if obj.profile_image:
            return format_html('<img src="{}" style="width: 45px; height: 45px; border-radius: 50%;" />', obj.profile_image.url)
        return "بدون عکس"
    
    image_tag.short_description = 'تصویر'
    
    # فیلدهایی که تو لیست دکترا نمایش داده میشن
    list_display = ('image_tag', 'name', 'specialty', 'phone')
    search_fields = ('name',)

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('name', 'national_id', 'phone')
    search_fields = ('name', 'national_id')

@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    # ستون‌هایی که منشی برای مدیریت نوبت‌ها نیاز داره
    list_display = ('patient', 'doctor', 'date', 'time', 'is_confirmed')
    # اضافه کردن فیلتر ساعت، علاوه بر تاریخ و دکتر
    list_filter = ('date', 'time', 'doctor', 'is_confirmed')
    search_fields = ('patient__name', 'patient__national_id')
    # مرتب‌سازی پیش‌فرض بر اساس تاریخ و سپس ساعت
    ordering = ('-date', '-time')