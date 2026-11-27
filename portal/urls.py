from django.contrib.auth import views as auth
from django.urls import path

from . import views
from .admin import console

urlpatterns = [
    path("", views.home, name="home"),
    path("library/", views.library, name="library"),
    path("documents/<int:pk>/", views.document_page, name="document"),
    path("documents/<int:pk>/agreement/", views.accept_agreement, name="accept"),
    path("documents/<int:pk>/request/", views.request_access, name="request_access"),
    path("documents/<int:pk>/files/<int:revision>/", views.download, name="download"),
    path("notifications/", views.notifications, name="notifications"),
    path("subscription/<uuid:token>/", views.subscription, name="subscription"),
    path("account/login/", auth.LoginView.as_view(), name="login"),
    path("account/logout/", auth.LogoutView.as_view(), name="logout"),
    path("account/reset/", auth.PasswordResetView.as_view(), name="password_reset"),
    path("account/reset/sent/", auth.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("account/reset/<uidb64>/<token>/", auth.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("account/reset/complete/", auth.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("manage/", console.urls),
]
