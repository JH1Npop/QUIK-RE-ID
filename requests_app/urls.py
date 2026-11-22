from django.urls import path

from . import admin_views, review_views, shop_views, student_views, views

urlpatterns = [
    path("", views.home, name="home"),
    path("signup/", views.signup, name="signup"),
    path("files/<int:pk>/", views.upload_file, name="upload_file"),
    path("requests/<int:pk>/authorization/", views.authorization, name="authorization"),

    # Student
    path("my/requests/", student_views.student_list, name="student_list"),
    path("my/requests/new/", student_views.student_new, name="student_new"),
    path("my/requests/<int:pk>/", student_views.student_detail, name="student_detail"),
    path("my/requests/<int:pk>/resubmit/", student_views.student_resubmit, name="student_resubmit"),
    path("my/requests/<int:pk>/cancel/", student_views.student_cancel, name="student_cancel"),

    # Program Head
    path("review/", review_views.ph_queue, name="ph_queue"),
    path("review/<int:pk>/", review_views.ph_detail, name="ph_detail"),
    path("review/<int:pk>/approve/", review_views.ph_approve, name="ph_approve"),
    path("review/<int:pk>/reject/", review_views.ph_reject, name="ph_reject"),

    # OSAS
    path("osas/", review_views.osas_queue, name="osas_queue"),
    path("osas/reports/", review_views.osas_reports, name="osas_reports"),
    path("osas/<int:pk>/", review_views.osas_detail, name="osas_detail"),
    path("osas/<int:pk>/approve/", review_views.osas_approve, name="osas_approve"),
    path("osas/<int:pk>/reject/", review_views.osas_reject, name="osas_reject"),

    # ID shop
    path("shop/", shop_views.shop_list, name="shop_list"),
    path("shop/branch/", shop_views.shop_branch, name="shop_branch"),
    path("shop/<int:pk>/", shop_views.shop_detail, name="shop_detail"),
    path("shop/<int:pk>/payment/", shop_views.shop_payment, name="shop_payment"),
    path("shop/<int:pk>/photo/", shop_views.shop_photo, name="shop_photo"),
    path("shop/<int:pk>/printed/", shop_views.shop_printed, name="shop_printed"),
    path("shop/<int:pk>/release/", shop_views.shop_release, name="shop_release"),

    # Admin
    path("manage/users/", admin_views.manage_users, name="manage_users"),
    path("manage/users/new/", admin_views.manage_staff_new, name="manage_staff_new"),
    path("manage/users/<int:pk>/edit/", admin_views.manage_user_edit, name="manage_user_edit"),
    path("manage/users/<int:pk>/deactivate/", admin_views.manage_deactivate, name="manage_deactivate"),
    path("manage/users/<int:pk>/reactivate/", admin_views.manage_reactivate, name="manage_reactivate"),
    path("manage/departments/", admin_views.manage_departments, name="manage_departments"),
    path("manage/departments/add/", admin_views.manage_department_add, name="manage_department_add"),
    path("manage/departments/<int:pk>/rename/", admin_views.manage_department_rename, name="manage_department_rename"),
    path("manage/departments/<int:pk>/archive/", admin_views.manage_department_archive, name="manage_department_archive"),
    path("manage/departments/<int:pk>/reactivate/", admin_views.manage_department_reactivate,
         name="manage_department_reactivate"),
    path("manage/departments/<int:pk>/head/", admin_views.manage_assign_head, name="manage_assign_head"),
    path("manage/shops/", admin_views.manage_shops, name="manage_shops"),
    path("manage/shops/add/", admin_views.manage_shop_add, name="manage_shop_add"),
    path("manage/shops/<int:pk>/rename/", admin_views.manage_shop_rename, name="manage_shop_rename"),
    path("manage/shops/<int:pk>/archive/", admin_views.manage_shop_archive, name="manage_shop_archive"),
    path("manage/shops/<int:pk>/reactivate/", admin_views.manage_shop_reactivate, name="manage_shop_reactivate"),
]
