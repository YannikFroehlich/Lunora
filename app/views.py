from django.shortcuts import render

def home(request):
    template_name = 'app/home.html'
    return render(request, template_name)

def settings(request):
    template_name = 'app/settings.html'
    return render(request, template_name)