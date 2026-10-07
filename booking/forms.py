from django import forms
from .models import Doctor

TIME_CHOICES = [
    ('16:00', 'ساعت ۱۶:۰۰'), ('16:30', 'ساعت ۱۶:۳۰'),
    ('17:00', 'ساعت ۱۷:۰۰'), ('17:30', 'ساعت ۱۷:۳۰'),
    ('18:00', 'ساعت ۱۸:۰۰'), ('18:30', 'ساعت ۱۸:۳۰'),
    ('19:00', 'ساعت ۱۹:۰۰'), ('19:30', 'ساعت ۱۹:۳۰'),
]

class BookingForm(forms.Form):
    name = forms.CharField(label="نام و نام خانوادگی بیمار", widget=forms.TextInput(attrs={
        'class': 'w-full p-4 border border-gray-200 rounded-xl focus:border-primary focus:ring-1 focus:ring-primary outline-none mb-4',
        'placeholder': 'مثال: علی احمدی'
    }))
    
    national_id = forms.CharField(label="کد ملی / شناسه اتباع", widget=forms.TextInput(attrs={
        'class': 'w-full p-4 border border-gray-200 rounded-xl focus:border-primary focus:ring-1 focus:ring-primary outline-none mb-4',
        'placeholder': 'کد ۱۰ رقمی'
    }))
    
    doctor = forms.ModelChoiceField(label="انتخاب پزشک متخصص", queryset=Doctor.objects.all(), empty_label="پزشک مورد نظر را انتخاب کنید...", widget=forms.Select(attrs={
        'class': 'w-full p-4 border border-gray-200 rounded-xl focus:border-primary focus:ring-1 focus:ring-primary outline-none mb-4'
    }))
    
    date = forms.CharField(label="تاریخ نوبت", widget=forms.TextInput(attrs={
        'class': 'jalali-datepicker w-full p-4 border border-gray-200 rounded-xl focus:border-primary focus:ring-1 focus:ring-primary outline-none mb-4 text-center',
        'placeholder': 'انتخاب از تقویم...',
        'readonly': 'readonly'
    }))
    
    time = forms.ChoiceField(label="ساعت پیشنهادی", choices=TIME_CHOICES, widget=forms.Select(attrs={
        'class': 'w-full p-4 border border-gray-200 rounded-xl focus:border-primary focus:ring-1 focus:ring-primary outline-none mb-6'
    }))

    def clean_national_id(self):
        nid = self.cleaned_data.get('national_id')
        if not nid or len(nid) != 10 or not nid.isdigit():
            raise forms.ValidationError("❌ کد ملی باید دقیقاً ۱۰ رقم عدد باشد.")
        
        check = int(nid[9])
        s = sum([int(nid[x]) * (10 - x) for x in range(9)]) % 11
        
        if (s < 2 and check == s) or (s >= 2 and check + s == 11):
            return nid
            
        raise forms.ValidationError("❌ کد ملی وارد شده از نظر ساختاری نامعتبر است!")