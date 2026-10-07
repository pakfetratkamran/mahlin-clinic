from django.contrib import admin
from django.urls import path
from booking.views import (home_page, book_appointment, login_view, verify_otp, 
                           dashboard_router, patient_dashboard, doctor_dashboard, 
                           cancel_appointment, logout_view)
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home_page, name='home'),
    path('book/', book_appointment, name='book'),
    path('login/', login_view, name='login'),
    path('verify/', verify_otp, name='verify_otp'),
    
    path('dashboard/', dashboard_router, name='dashboard_router'),
    path('patient-panel/', patient_dashboard, name='patient_dashboard'),
    path('doctor-panel/', doctor_dashboard, name='doctor_dashboard'),
    
    path('cancel/<int:appointment_id>/', cancel_appointment, name='cancel_appointment'),
    path('logout/', logout_view, name='logout'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = 'booking.views.custom_404'