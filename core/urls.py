from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from booking import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home_page, name='home'),
    path('login/', views.login_view, name='login'),
    path('verify/', views.verify_otp, name='verify_otp'),
    path('book/', views.book_appointment, name='book_appointment'),
    path('api/available-slots/', views.api_available_slots, name='api_available_slots'),
    path('dashboard/', views.dashboard_router, name='dashboard_router'),
    path('patient-panel/', views.patient_dashboard, name='patient_dashboard'),
    path('doctor-panel/', views.doctor_dashboard, name='doctor_dashboard'),
    path('cancel/<int:appointment_id>/', views.cancel_appointment, name='cancel_appointment'),
    path('reschedule/<int:appointment_id>/', views.reschedule_appointment, name='reschedule_appointment'),
    path('logout/', views.logout_view, name='logout'),

    # پنل مدیریت اختصاصی مالک و پذیرش
    path('manager-login/', views.manager_login, name='manager_login'),
    path('manager-panel/', views.manager_dashboard, name='manager_dashboard'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
